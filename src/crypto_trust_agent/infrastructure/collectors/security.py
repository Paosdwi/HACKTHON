"""ADR-007 network and deadline policy for provider-owned collectors."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import threading
import time
from typing import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

SCHEMA_VERSION = "1.0.0"
SECURITY_POLICY_VERSION = "collector-security-1.0.0"
MAX_URL_CHARS = 2048
MAX_REDIRECTS = 3
MAX_RAW_BYTES = 5 * 1024 * 1024
MAX_CLEAN_BYTES = 1024 * 1024
CONNECT_TIMEOUT_S = 3.0
READ_TIMEOUT_S = 10.0
STATIC_TIMEOUT_S = 15.0
PLAYWRIGHT_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class PolicyViolation(Exception):
    code: str
    safe_message: str
    retryable: bool = False

    def __str__(self) -> str:
        return self.safe_message


@dataclass(frozen=True)
class ValidatedTarget:
    url: str
    canonical_url: str
    host: str
    port: int
    approved_ips: tuple[str, ...]


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PolicyViolation("invalid_source_schema", "Deadline timestamps must be UTC Z values")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PolicyViolation("invalid_source_schema", "Deadline timestamp is invalid") from exc
    return parsed.astimezone(timezone.utc)


class LocalDeadline:
    """Receiver-local monotonic deadline built from the frozen six-field DTO."""

    def __init__(
        self,
        dto: dict,
        provider_limit_s: float,
        now_utc: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        required = {
            "schema_version", "operation_id", "deadline_at_utc", "budget_ms",
            "sent_at_utc", "safety_margin_ms",
        }
        if set(dto) != required or dto.get("schema_version") != SCHEMA_VERSION:
            raise PolicyViolation("invalid_source_schema", "Deadline contract is invalid")
        budget = dto.get("budget_ms")
        margin = dto.get("safety_margin_ms")
        if not isinstance(budget, int) or not 1 <= budget <= 900_000:
            raise PolicyViolation("invalid_source_schema", "Deadline budget is invalid")
        if not isinstance(margin, int) or not 100 <= margin <= 5000:
            raise PolicyViolation("invalid_source_schema", "Deadline safety margin is invalid")
        parse_utc(dto["sent_at_utc"])
        utc_clock = now_utc or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        now_wall = utc_clock()
        utc_remaining = (parse_utc(dto["deadline_at_utc"]) - now_wall).total_seconds()
        effective = max(0.0, min(provider_limit_s, budget / 1000.0, utc_remaining - margin / 1000.0))
        self._expires = self._monotonic() + effective
        self.effective_seconds = effective

    def remaining(self) -> float:
        return max(0.0, self._expires - self._monotonic())

    def require_time(self) -> float:
        remaining = self.remaining()
        if remaining <= 0:
            raise PolicyViolation("deadline_exceeded", "Collector deadline was exceeded")
        return remaining


class UrlSecurityPolicy:
    def __init__(self, allowed_hosts: Iterable[str], resolver: Callable[[str, int], Iterable[str]]) -> None:
        normalized = {self._normalize_host(item) for item in allowed_hosts}
        if not normalized:
            raise ValueError("allowed_hosts must not be empty")
        self.allowed_hosts = frozenset(normalized)
        self._resolver = resolver

    @staticmethod
    def _normalize_host(host: str) -> str:
        return host.rstrip(".").encode("idna").decode("ascii").lower()

    def _allowlisted(self, host: str) -> bool:
        return any(host == allowed or host.endswith("." + allowed) for allowed in self.allowed_hosts)

    @staticmethod
    def _public_ip(value: str) -> bool:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        metadata = {
            ipaddress.ip_address("169.254.169.254"),
            ipaddress.ip_address("fd00:ec2::254"),
        }
        return address not in metadata and address.is_global and not any((
            address.is_private, address.is_loopback, address.is_link_local,
            address.is_reserved, address.is_multicast, address.is_unspecified,
        ))

    def validate(self, url: str) -> ValidatedTarget:
        if not isinstance(url, str) or len(url) > MAX_URL_CHARS:
            raise PolicyViolation("ssrf_blocked", "Source URL is invalid or too long")
        parts = urlsplit(url)
        if parts.scheme.lower() != "https" or not parts.hostname:
            raise PolicyViolation("ssrf_blocked", "Only HTTPS source URLs are allowed")
        if parts.username is not None or parts.password is not None:
            raise PolicyViolation("ssrf_blocked", "URL credentials are not allowed")
        try:
            port = parts.port or 443
        except ValueError as exc:
            raise PolicyViolation("ssrf_blocked", "Source port is invalid") from exc
        if port != 443:
            raise PolicyViolation("ssrf_blocked", "Only destination port 443 is allowed")
        host = self._normalize_host(parts.hostname)
        if not self._allowlisted(host):
            raise PolicyViolation("source_not_allowlisted", "Source host is not allowlisted")
        try:
            resolved = tuple(dict.fromkeys(str(item) for item in self._resolver(host, port)))
        except Exception as exc:
            raise PolicyViolation("dns_ip_rejected", "Source DNS resolution failed") from exc
        if not resolved or any(not self._public_ip(item) for item in resolved):
            raise PolicyViolation("dns_ip_rejected", "Source DNS result is not public")
        canonical = urlunsplit(("https", host, parts.path or "/", parts.query, ""))
        return ValidatedTarget(url, canonical, host, port, resolved)

    def validate_peer(self, target: ValidatedTarget, peer_ip: str) -> None:
        if peer_ip not in target.approved_ips or not self._public_ip(peer_ip):
            raise PolicyViolation("dns_ip_rejected", "Connected address did not match validated DNS")


class HostLimiter:
    """Process-local per-host concurrency and request-start spacing guard."""

    def __init__(
        self,
        concurrency: int = 2,
        interval_s: float = 1.0,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._limit = concurrency
        self._interval = interval_s
        self._clock = monotonic or time.monotonic
        self._sleep = sleeper or time.sleep
        self._guard = threading.Lock()
        self._semaphores: dict[str, threading.BoundedSemaphore] = {}
        self._last_start: dict[str, float] = {}

    def _semaphore(self, host: str) -> threading.BoundedSemaphore:
        with self._guard:
            return self._semaphores.setdefault(host, threading.BoundedSemaphore(self._limit))

    def run(self, host: str, deadline: LocalDeadline, action: Callable[[], object]) -> object:
        semaphore = self._semaphore(host)
        if not semaphore.acquire(timeout=deadline.require_time()):
            raise PolicyViolation("source_rate_limited", "Per-host concurrency limit reached", True)
        try:
            with self._guard:
                delay = max(0.0, self._last_start.get(host, -1e30) + self._interval - self._clock())
            if delay:
                if delay >= deadline.remaining():
                    raise PolicyViolation("source_rate_limited", "Per-host request interval exceeds deadline", True)
                self._sleep(delay)
            deadline.require_time()
            with self._guard:
                self._last_start[host] = self._clock()
            return action()
        finally:
            semaphore.release()
