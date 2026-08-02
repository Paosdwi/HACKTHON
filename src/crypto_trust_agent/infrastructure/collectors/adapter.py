"""SourceCollector 1.0.0 internal mapping boundary.

The Core Python Port does not yet exist in this repository.  This adapter accepts
and returns frozen contract-shaped mappings without defining a Core substitute.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import socket
import threading
import time
from typing import Callable, Mapping, Protocol
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

from .security import (
    CONNECT_TIMEOUT_S, MAX_RAW_BYTES, MAX_REDIRECTS, PLAYWRIGHT_TIMEOUT_S,
    READ_TIMEOUT_S, SCHEMA_VERSION, SECURITY_POLICY_VERSION, STATIC_TIMEOUT_S,
    HostLimiter, LocalDeadline, PolicyViolation, UrlSecurityPolicy, ValidatedTarget,
)
from .transport import FetchResponse, Fetcher, PinnedHttpsFetcher, PlaywrightFetcher, clean_content

CONTRACT_VERSION = "1.0.0"
PROVIDER_VERSION = "collector-adapter-1.0.0"
ALLOWED_CATEGORIES = {"market", "news", "official", "on_chain", "social", "macro"}


class RawStore(Protocol):
    def put(self, raw_record_id: str, content: bytes, content_hash: str) -> str: ...


class EventSink(Protocol):
    def emit(self, event: Mapping[str, object]) -> None: ...


class InMemoryRawStore:
    """Non-production record/replay fixture storage; no filesystem or AWS writes."""

    def __init__(self) -> None:
        self.records: dict[str, tuple[bytes, str]] = {}
        self._lock = threading.Lock()

    def put(self, raw_record_id: str, content: bytes, content_hash: str) -> str:
        with self._lock:
            existing = self.records.get(raw_record_id)
            if existing and existing != (content, content_hash):
                raise PolicyViolation("invalid_source_schema", "Raw record identity conflict")
            self.records[raw_record_id] = (content, content_hash)
        return f"urn:cryptotrust:raw:{raw_record_id}"


class NullEventSink:
    def emit(self, event: Mapping[str, object]) -> None:
        del event


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def default_resolver(host: str, port: int) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))


@dataclass(frozen=True)
class _Fetched:
    response: FetchResponse
    target: ValidatedTarget
    redirects: int


class RobotsTxtPolicy:
    """Fail-closed robots checker fetched through the same pinned secure boundary."""

    def __init__(
        self, security: UrlSecurityPolicy, fetcher: Fetcher, limiter: HostLimiter,
        user_agent: str = "CryptoTrustCollector/1.0",
    ) -> None:
        self._security = security
        self._fetcher = fetcher
        self._limiter = limiter
        self._user_agent = user_agent
        self._cache: dict[str, RobotFileParser] = {}
        self._lock = threading.Lock()

    def allowed(self, target: ValidatedTarget, deadline: LocalDeadline) -> bool:
        with self._lock:
            parser = self._cache.get(target.host)
        if parser is None:
            robots_target = self._security.validate(f"https://{target.host}/robots.txt")

            def request_guard(url: str, peer_ip: str) -> None:
                guarded = self._security.validate(url)
                self._security.validate_peer(guarded, peer_ip)

            def fetch() -> FetchResponse:
                remaining = deadline.require_time()
                return self._fetcher.fetch(
                    robots_target, min(CONNECT_TIMEOUT_S, remaining),
                    min(READ_TIMEOUT_S, remaining), remaining, request_guard,
                )

            response = self._limiter.run(target.host, deadline, fetch)
            assert isinstance(response, FetchResponse)
            self._security.validate_peer(robots_target, response.peer_ip)
            if response.status != 200 or len(response.body) > MAX_RAW_BYTES:
                raise PolicyViolation("robots_disallowed", "Robots policy could not be verified")
            try:
                text = response.body.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise PolicyViolation("robots_disallowed", "Robots policy is invalid") from exc
            parser = RobotFileParser()
            parser.set_url(f"https://{target.host}/robots.txt")
            parser.parse(text.splitlines())
            with self._lock:
                self._cache[target.host] = parser
        return parser.can_fetch(self._user_agent, target.url)


class SecureSourceCollector:
    contract_version = CONTRACT_VERSION
    provider_version = PROVIDER_VERSION
    service_version = "https-rss-playwright-boundary-1.0.0"
    waiting_for_core_interface = True

    def __init__(
        self,
        allowed_hosts: set[str],
        allowlist_version: str,
        static_fetcher: Fetcher | None = None,
        playwright_fetcher: PlaywrightFetcher | None = None,
        resolver: Callable[[str, int], tuple[str, ...]] = default_resolver,
        raw_store: RawStore | None = None,
        event_sink: EventSink | None = None,
        robots_policy: object | None = None,
        limiter: HostLimiter | None = None,
        now_utc: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.security = UrlSecurityPolicy(allowed_hosts, resolver)
        self.allowlist_version = allowlist_version
        self.static_fetcher = static_fetcher or PinnedHttpsFetcher()
        self.playwright_fetcher = playwright_fetcher
        self.raw_store = raw_store or InMemoryRawStore()
        self.events = event_sink or NullEventSink()
        self.limiter = limiter or HostLimiter(monotonic=monotonic)
        self.robots = robots_policy or RobotsTxtPolicy(self.security, self.static_fetcher, self.limiter)
        self._now = now_utc or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._replay: dict[str, tuple[str, dict]] = {}
        self._replay_lock = threading.Lock()

    @staticmethod
    def _request_fingerprint(request: Mapping[str, object]) -> str:
        return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _validate_request(request: Mapping[str, object]) -> None:
        required = {
            "schema_version", "operation_id", "task_id", "execution_id", "plan_job_id",
            "source_category", "collection_mode", "requirement", "assets", "approved_query",
            "approved_urls", "reporting_range", "priority", "security_policy_version", "deadline",
        }
        if set(request) != required or request.get("schema_version") != SCHEMA_VERSION:
            raise PolicyViolation("invalid_source_schema", "Collector request contract is invalid")
        if request.get("security_policy_version") != SECURITY_POLICY_VERSION:
            raise PolicyViolation("invalid_source_schema", "Collector security policy version is unsupported")
        if request.get("source_category") not in ALLOWED_CATEGORIES:
            raise PolicyViolation("unsupported_category", "Source category is unsupported")
        if request.get("collection_mode") not in {"static", "playwright"}:
            raise PolicyViolation("invalid_source_schema", "Collection mode is invalid")
        if request.get("requirement") not in {"required", "required_if_available", "optional"}:
            raise PolicyViolation("invalid_source_schema", "Source requirement is invalid")
        query = request.get("approved_query")
        urls = request.get("approved_urls")
        if not isinstance(query, str) or not 1 <= len(query) <= 2000:
            raise PolicyViolation("invalid_source_schema", "Approved query is invalid")
        if not isinstance(urls, list) or len(urls) > 100 or len(set(urls)) != len(urls):
            raise PolicyViolation("invalid_source_schema", "Approved URL list is invalid")
        if not all(isinstance(item, str) for item in urls):
            raise PolicyViolation("invalid_source_schema", "Approved URL is invalid")
        priority = request.get("priority")
        if not isinstance(priority, int) or not 1 <= priority <= 100:
            raise PolicyViolation("invalid_source_schema", "Collection priority is invalid")

    def _port_error(self, operation_id: str, violation: PolicyViolation) -> dict:
        category = {
            "deadline_exceeded": "timeout", "fetch_timeout": "timeout",
            "source_rate_limited": "rate_limited", "collector_unavailable": "unavailable",
            "unexpected_provider_error": "unexpected",
        }.get(violation.code, "unsafe_source" if violation.code in {
            "source_not_allowlisted", "dns_ip_rejected", "ssrf_blocked", "robots_disallowed",
        } else "validation")
        return {"status": "error", "error": {
            "schema_version": SCHEMA_VERSION, "code": violation.code, "category": category,
            "retryable": violation.retryable, "safe_message": violation.safe_message,
            "provider": "source_collector", "operation_id": operation_id,
            "details": {"policy_version": SECURITY_POLICY_VERSION},
            "occurred_at": _utc_text(self._now()),
        }}

    def _guard_network_request(self, url: str, peer_ip: str) -> None:
        target = self.security.validate(url)
        self.security.validate_peer(target, peer_ip)

    def _fetch(self, initial_url: str, mode: str, deadline: LocalDeadline) -> _Fetched:
        current = initial_url
        for redirect_count in range(MAX_REDIRECTS + 1):
            target = self.security.validate(current)
            if not self.robots.allowed(target, deadline):
                raise PolicyViolation("robots_disallowed", "Robots policy disallows this source")
            fetcher: Fetcher
            if mode == "playwright":
                if self.playwright_fetcher is None:
                    raise PolicyViolation("collector_not_configured", "Playwright collector is not configured")
                fetcher = self.playwright_fetcher
            else:
                fetcher = self.static_fetcher

            def action() -> FetchResponse:
                remaining = deadline.require_time()
                return fetcher.fetch(
                    target, min(CONNECT_TIMEOUT_S, remaining),
                    min(READ_TIMEOUT_S, remaining), remaining,
                    self._guard_network_request,
                )

            response = self.limiter.run(target.host, deadline, action)
            assert isinstance(response, FetchResponse)
            self.security.validate_peer(target, response.peer_ip)
            for network_url, peer_ip in response.network_targets:
                self._guard_network_request(network_url, peer_ip)
            if 300 <= response.status <= 399:
                location = response.headers.get("location")
                if not location:
                    raise PolicyViolation("invalid_source_schema", "Redirect response has no location")
                if redirect_count >= MAX_REDIRECTS:
                    raise PolicyViolation("redirect_limit_exceeded", "Source redirect limit exceeded")
                current = urljoin(target.url, location)
                continue
            if not 200 <= response.status <= 299:
                raise PolicyViolation("collector_unavailable", "Source returned an unsuccessful status", True)
            if len(response.body) > MAX_RAW_BYTES:
                raise PolicyViolation("payload_too_large", "Source payload exceeds limit")
            return _Fetched(response, target, redirect_count)
        raise PolicyViolation("redirect_limit_exceeded", "Source redirect limit exceeded")

    def _record(self, request: Mapping[str, object], fetched: _Fetched) -> dict:
        content_type = fetched.response.headers.get("content-type", "text/plain")
        cleaned, media_type = clean_content(fetched.response.body, content_type)
        raw_hash = _sha256(fetched.response.body)
        clean_hash = _sha256(cleaned.encode("utf-8"))
        identity = f"{request['operation_id']}\0{fetched.target.canonical_url}".encode()
        raw_record_id = "RAW-" + hashlib.sha256(identity).hexdigest()[:24].upper()
        locator = self.raw_store.put(raw_record_id, fetched.response.body, raw_hash)
        return {
            "schema_version": SCHEMA_VERSION,
            "raw_record_id": raw_record_id,
            "task_id": request["task_id"], "execution_id": request["execution_id"],
            "plan_job_id": request["plan_job_id"], "source_name": fetched.target.host,
            "source_type": request["source_category"], "source_url": fetched.target.url,
            "canonical_url": fetched.target.canonical_url, "http_status": fetched.response.status,
            "published_at": None, "fetched_at": _utc_text(self._now()),
            "content_hash": raw_hash, "clean_content_hash": clean_hash,
            "raw_locator": locator, "media_type": media_type, "clean_content": cleaned,
            "query_provenance": {
                "collector": "secure_source_collector", "query": request["approved_query"],
                "parameters": {"plan_job_id": request["plan_job_id"], "collection_mode": request["collection_mode"]},
            },
            "security": {
                "allowlist_version": self.allowlist_version, "url_allowed": True,
                "dns_ip_validated": True, "robots_allowed": True,
                "redirect_count": fetched.redirects,
            },
        }

    def _emit(self, request: Mapping[str, object], status: str, duration_ms: int, records: int, code: str | None) -> None:
        event = {
            "schema_version": SCHEMA_VERSION, "step": "collect_source", "tool": "secure_source_collector",
            "status": status, "duration_ms": duration_ms, "retry_count": 0,
            "task_id": request.get("task_id"), "execution_id": request.get("execution_id"),
            "operation_id": request.get("operation_id"),
            "sanitized_parameters": {
                "source_category": request.get("source_category"),
                "collection_mode": request.get("collection_mode"),
                "security_policy_version": SECURITY_POLICY_VERSION,
            },
            "result_summary": {"records": records, "error_code": code},
        }
        try:
            self.events.emit(event)
        except Exception:
            return  # Fail closed: never dump the event, request, exception, or raw content.

    def collect(self, request: Mapping[str, object]) -> dict:
        started_wall = self._now()
        started_mono = self._monotonic()
        operation_id = request.get("operation_id") if isinstance(request.get("operation_id"), str) else "OP-COLLECT-UNKNOWN"
        fingerprint = ""
        try:
            self._validate_request(request)
            fingerprint = self._request_fingerprint(request)
            with self._replay_lock:
                replay = self._replay.get(operation_id)
            if replay:
                if replay[0] != fingerprint:
                    raise PolicyViolation("invalid_source_schema", "Operation replay payload does not match")
                return deepcopy(replay[1])
            mode = str(request["collection_mode"])
            provider_limit = PLAYWRIGHT_TIMEOUT_S if mode == "playwright" else STATIC_TIMEOUT_S
            deadline = LocalDeadline(dict(request["deadline"]), provider_limit, self._now, self._monotonic)
            deadline.require_time()
            records: list[dict] = []
            issues: list[dict] = []
            urls = list(request["approved_urls"])
            if not urls:
                outcome = "skipped"
                limitations = ["No approved source URLs were supplied"]
            else:
                for url in urls:
                    try:
                        deadline.require_time()
                        records.append(self._record(request, self._fetch(url, mode, deadline)))
                    except PolicyViolation as violation:
                        issues.append({
                            "code": violation.code, "retryable": violation.retryable,
                            "safe_message": violation.safe_message,
                        })
                    except Exception:
                        issues.append({
                            "code": "unexpected_provider_error", "retryable": False,
                            "safe_message": "Collector provider failed unexpectedly",
                        })
                outcome = "success" if records else "failed"
                limitations = [] if not issues else ["One or more approved sources were unavailable"]
            finished = self._now()
            duration_ms = min(30000, max(0, int((self._monotonic() - started_mono) * 1000)))
            result = {
                "schema_version": SCHEMA_VERSION, "operation_id": operation_id,
                "outcome": outcome, "records": records,
                "started_at": _utc_text(started_wall), "finished_at": _utc_text(finished),
                "duration_ms": duration_ms, "retry_count": 0,
                "issues": issues, "limitations": limitations,
            }
            with self._replay_lock:
                self._replay[operation_id] = (fingerprint, deepcopy(result))
            self._emit(request, "completed" if outcome == "success" else outcome, duration_ms, len(records), issues[0]["code"] if issues else None)
            return result
        except PolicyViolation as violation:
            result = self._port_error(operation_id, violation)
            self._emit(request, "failed", max(0, int((self._monotonic() - started_mono) * 1000)), 0, violation.code)
            if fingerprint:
                with self._replay_lock:
                    self._replay[operation_id] = (fingerprint, deepcopy(result))
            return result
        except Exception:
            violation = PolicyViolation("unexpected_provider_error", "Collector provider failed unexpectedly")
            self._emit(request, "failed", max(0, int((self._monotonic() - started_mono) * 1000)), 0, violation.code)
            return self._port_error(operation_id, violation)

    def health_check(self, request: Mapping[str, object]) -> dict:
        operation_id = request.get("operation_id", "OP-HEALTH-UNKNOWN")
        try:
            deadline = LocalDeadline(dict(request["deadline"]), 3.0, self._now, self._monotonic)
            deadline.require_time()
            return {
                "schema_version": SCHEMA_VERSION, "provider": request["provider"],
                "capability": "source_collection", "status": "healthy",
                "checked_at": _utc_text(self._now()), "latency_ms": 0,
                "safe_reason_code": None, "expires_at": None,
            }
        except PolicyViolation as violation:
            return self._port_error(str(operation_id), violation)
        except Exception:
            return self._port_error(str(operation_id), PolicyViolation("unexpected_provider_error", "Collector health check failed unexpectedly"))

    def capabilities(self, request: Mapping[str, object]) -> dict:
        operation_id = request.get("operation_id", "OP-CAPABILITIES-UNKNOWN")
        try:
            deadline = LocalDeadline(dict(request["deadline"]), 3.0, self._now, self._monotonic)
            deadline.require_time()
            return {
                "schema_version": SCHEMA_VERSION, "provider": request["provider"],
                "source_categories": sorted(ALLOWED_CATEGORIES),
                "supports_static": True, "supports_dynamic": self.playwright_fetcher is not None,
                "security_policy_versions": [SECURITY_POLICY_VERSION],
                "max_raw_bytes": MAX_RAW_BYTES, "max_clean_bytes": 1024 * 1024,
                "max_redirects": MAX_REDIRECTS,
            }
        except PolicyViolation as violation:
            return self._port_error(str(operation_id), violation)
        except Exception:
            return self._port_error(str(operation_id), PolicyViolation("unexpected_provider_error", "Collector capability check failed unexpectedly"))
