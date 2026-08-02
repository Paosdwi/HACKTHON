"""Secure one-attempt Binance Spot implementation of LiveMarketDataProvider."""

from __future__ import annotations

import hashlib
import json
import socket
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, time as day_time, timedelta
from decimal import Decimal, InvalidOperation
from threading import RLock
from typing import Callable, Mapping, Protocol
from urllib.parse import urlencode

from crypto_trust_agent.application.dto.common import (
    DeadlineExceededError, ErrorResultDTO, PortErrorCategory, PortErrorDTO,
    build_local_deadline, map_unexpected_exception,
)
from crypto_trust_agent.application.dto.live_market import (
    LiveMarketBarDTO, LiveMarketCapabilitiesDTO, LiveMarketCapabilitiesRequestDTO,
    LiveMarketDataRequestDTO, LiveMarketDataResultDTO, LiveMarketHealthRequestDTO,
    RULESET_VERSION,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO
from crypto_trust_agent.domain.primitives import CanonicalDecimal, ContractValidationError
from crypto_trust_agent.infrastructure.collectors.security import PolicyViolation, UrlSecurityPolicy
from crypto_trust_agent.infrastructure.collectors.transport import Fetcher, PinnedHttpsFetcher

CONTRACT_VERSION = "1.0.0"
PROVIDER_VERSION = "binance-live-market-adapter-1.0.0"
BASE_URL = "https://api.binance.com"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class BinanceTransportFailure(Exception):
    code: str
    retryable: bool = False


class BinanceTransport(Protocol):
    max_attempts: int
    hidden_retries: int
    def get(self, url: str, *, timeout_ms: int) -> bytes: ...


class EventSink(Protocol):
    def emit(self, event: Mapping[str, object]) -> None: ...


class NullEventSink:
    def emit(self, event: Mapping[str, object]) -> None:
        del event


def _default_resolver(host: str, port: int) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))


class PinnedBinanceTransport:
    """Fixed-host HTTPS/443 transport with DNS/peer pinning and no redirect following."""

    max_attempts = 1
    hidden_retries = 0
    non_production = False

    def __init__(self, *, fetcher: Fetcher | None = None, resolver: Callable[[str, int], tuple[str, ...]] = _default_resolver) -> None:
        self._fetcher = fetcher or PinnedHttpsFetcher(user_agent="CryptoTrustLiveMarket/1.0")
        self._security = UrlSecurityPolicy({"api.binance.com"}, resolver)

    def get(self, url: str, *, timeout_ms: int) -> bytes:
        if not url.startswith(BASE_URL + "/") or type(timeout_ms) is not int or timeout_ms <= 0:
            raise BinanceTransportFailure("invalid_request")
        try:
            target = self._security.validate(url)
            response = self._fetcher.fetch(
                target,
                min(3.0, timeout_ms / 1000),
                min(10.0, timeout_ms / 1000),
                timeout_ms / 1000,
                lambda requested, peer: self._security.validate_peer(self._security.validate(requested), peer),
            )
            self._security.validate_peer(target, response.peer_ip)
        except PolicyViolation as failure:
            code = {
                "fetch_timeout": "provider_timeout",
                "payload_too_large": "payload_too_large",
                "dns_ip_rejected": "provider_unavailable",
                "collector_unavailable": "provider_unavailable",
            }.get(failure.code, "provider_unavailable")
            raise BinanceTransportFailure(code, code in {"provider_timeout", "provider_unavailable"}) from failure
        if 300 <= response.status <= 399:
            raise BinanceTransportFailure("provider_access_denied")
        if response.status == 429:
            raise BinanceTransportFailure("provider_rate_limited", True)
        if response.status in {401, 403, 451}:
            raise BinanceTransportFailure("provider_access_denied")
        if response.status >= 500:
            raise BinanceTransportFailure("provider_unavailable", True)
        if not 200 <= response.status <= 299:
            raise BinanceTransportFailure("provider_unavailable")
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise BinanceTransportFailure("payload_too_large")
        return response.body


class StubBinanceTransport:
    max_attempts = 1
    hidden_retries = 0
    non_production = True

    def __init__(self) -> None:
        self.default_payload: bytes = b"[]"
        self.configured: object | None = None
        self.calls: list[tuple[str, int]] = []

    def configure(self, value: object) -> None:
        self.configured = value

    def get(self, url: str, *, timeout_ms: int) -> bytes:
        self.calls.append((url, timeout_ms))
        configured = self.configured
        if isinstance(configured, BaseException):
            raise configured
        if configured == "rate_limit":
            raise BinanceTransportFailure("provider_rate_limited", True)
        if configured == "unavailable":
            raise BinanceTransportFailure("provider_unavailable", True)
        if configured == "access_denied":
            raise BinanceTransportFailure("provider_access_denied")
        if isinstance(configured, bytes):
            return configured
        if configured is not None:
            raise TypeError("invalid stub configuration")
        if url.endswith("/api/v3/time"):
            return b'{"serverTime":1785672000000}'
        return self.default_payload


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_decimal(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("Binance numeric field must be a string")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ContractValidationError("invalid provider decimal") from error
    if not number.is_finite() or number < 0:
        raise ContractValidationError("invalid provider decimal")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in {"", "-0"}:
        rendered = "0"
    return str(CanonicalDecimal(rendered).require_nonnegative())


def _content_hash(payload: Mapping[str, object]) -> str:
    wire = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(wire).hexdigest()


def _epoch_ms(day: date) -> int:
    return int(datetime.combine(day, day_time.min, tzinfo=UTC).timestamp() * 1000)


class BinanceLiveMarketDataProvider:
    contract_version = CONTRACT_VERSION
    provider_version = PROVIDER_VERSION
    service_version = "binance-spot-rest-boundary-1.0.0"
    max_attempts = 1
    hidden_retries = 0

    def __init__(
        self,
        transport: BinanceTransport,
        *,
        now_utc: Callable[[], datetime] | None = None,
        monotonic_ms: Callable[[], int] | None = None,
        runtime_id: str = "binance-live-market-runtime",
        event_sink: EventSink | None = None,
    ) -> None:
        if transport is None or transport.max_attempts != 1 or transport.hidden_retries != 0:
            raise ValueError("Binance transport must use one attempt and zero hidden retries")
        self._transport = transport
        self._now = now_utc or (lambda: datetime.now(UTC))
        self._monotonic_ms = monotonic_ms or (lambda: time.monotonic_ns() // 1_000_000)
        self._runtime_id = runtime_id
        self._events = event_sink or NullEventSink()
        self._completed: dict[str, tuple[LiveMarketDataRequestDTO, LiveMarketDataResultDTO | ErrorResultDTO]] = {}
        self._lock = RLock()
        self.non_production = bool(getattr(transport, "non_production", False))
        self.invocation_count = 0

    @classmethod
    def production(cls) -> "BinanceLiveMarketDataProvider":
        """Create the approved public, no-credential, fixed-host production boundary."""
        return cls(PinnedBinanceTransport())

    @staticmethod
    def _category(code: str) -> PortErrorCategory:
        if code in {"deadline_exceeded", "provider_timeout"}:
            return PortErrorCategory.TIMEOUT
        if code == "provider_rate_limited":
            return PortErrorCategory.RATE_LIMITED
        if code == "provider_unavailable":
            return PortErrorCategory.UNAVAILABLE
        if code == "provider_access_denied":
            return PortErrorCategory.FORBIDDEN
        if code in {"invalid_provider_schema", "invalid_market_bar"}:
            return PortErrorCategory.INVALID_PROVIDER_OUTPUT
        if code == "reconciliation_failed":
            return PortErrorCategory.INTEGRITY
        if code == "unexpected_provider_error":
            return PortErrorCategory.UNEXPECTED
        if code == "payload_conflict":
            return PortErrorCategory.CONFLICT
        return PortErrorCategory.VALIDATION

    def _error(self, operation_id: str, code: str, retryable: bool = False) -> ErrorResultDTO:
        return PortErrorDTO(
            CONTRACT_VERSION, code, self._category(code), retryable,
            "Live market provider request failed.", "binance_spot", operation_id, {}, _utc_text(self._now()),
        ).as_result()

    def _deadline(self, request, timeout_ms: int):
        return build_local_deadline(
            request.deadline, provider_timeout_ms=timeout_ms, now_utc=self._now(),
            now_monotonic_ms=self._monotonic_ms(), runtime_id=self._runtime_id,
        )

    def _emit(self, operation_id: str, status: str, started_ms: int, code: str | None, *, asset: str | None = None) -> None:
        event = {
            "schema_version": CONTRACT_VERSION,
            "step": "fetch_live_market",
            "adapter": "binance_spot",
            "status": status,
            "duration_ms": max(0, self._monotonic_ms() - started_ms),
            "retry_count": 0,
            "operation_id": operation_id,
            "safe_parameters": {
                "asset": asset,
                "interval": "1d",
                "provider_ruleset_version": RULESET_VERSION,
            },
            "result_summary": {"error_code": code},
        }
        try:
            self._events.emit(event)
        except Exception:
            return

    @staticmethod
    def _url(request: LiveMarketDataRequestDTO) -> str:
        query = urlencode({
            "symbol": request.pair,
            "interval": "1d",
            "startTime": _epoch_ms(request.start_date),
            "endTime": _epoch_ms(request.end_date) + DAY_MS - 1,
            "timeZone": "0",
            "limit": 1000,
        })
        return f"{BASE_URL}/api/v3/klines?{query}"

    def _parse(self, request: LiveMarketDataRequestDTO, raw: bytes, source_url: str) -> LiveMarketDataResultDTO:
        if len(raw) > MAX_RESPONSE_BYTES:
            raise BinanceTransportFailure("payload_too_large")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, list) or len(value) > 1000:
            raise ValueError("invalid provider root")
        fetched_at = _utc_text(self._now())
        as_of_ms = int(request.as_of.as_datetime().timestamp() * 1000)
        bars: list[LiveMarketBarDTO] = []
        incomplete: list[date] = []
        seen: set[date] = set()
        for row in value:
            if not isinstance(row, list) or len(row) != 12:
                raise ValueError("invalid kline row")
            if type(row[0]) is not int or type(row[6]) is not int:
                raise ValueError("invalid kline timestamp")
            open_ms, close_ms = row[0], row[6]
            if open_ms % DAY_MS != 0 or close_ms != open_ms + DAY_MS - 1:
                raise ContractValidationError("kline is not a UTC daily interval")
            day = datetime.fromtimestamp(open_ms / 1000, tz=UTC).date()
            if not request.start_date <= day <= request.end_date or day in seen:
                raise ContractValidationError("out-of-range or duplicate kline")
            seen.add(day)
            if close_ms >= as_of_ms:
                incomplete.append(day)
                continue
            open_value, high, low, close, volume = (_canonical_decimal(row[index]) for index in (1, 2, 3, 4, 5))
            canonical = {
                "asset": request.asset, "pair": request.pair, "day": day.isoformat(),
                "open": open_value, "high": high, "low": low, "close": close,
                "volume": volume, "source_open_time_ms": open_ms,
                "source_close_time_ms": close_ms, "provider_ruleset_version": RULESET_VERSION,
            }
            bars.append(LiveMarketBarDTO(
                request.asset, request.pair, day, open_value, high, low, close, volume,
                source_url, fetched_at, open_ms, close_ms, _content_hash(canonical),
            ))
        days = tuple(bar.day for bar in bars)
        if days != tuple(sorted(days)):
            raise ContractValidationError("provider rows are unordered")
        expected = set()
        cursor = request.start_date
        while cursor <= request.end_date:
            expected.add(cursor)
            cursor += timedelta(days=1)
        missing = tuple(sorted(expected - set(days) - set(incomplete)))
        return LiveMarketDataResultDTO(
            request.operation_id, request.asset, request.pair,
            request.start_date, request.end_date, tuple(bars),
            not missing and not incomplete, missing, tuple(sorted(incomplete)), fetched_at,
        )

    def fetch_daily_ohlcv(self, request: LiveMarketDataRequestDTO) -> LiveMarketDataResultDTO | ErrorResultDTO:
        with self._lock:
            completed = self._completed.get(request.operation_id)
            if completed:
                return completed[1] if completed[0] == request else self._error(request.operation_id, "payload_conflict")
            started_ms = self._monotonic_ms()
            try:
                deadline = self._deadline(request, 30_000)
                source_url = self._url(request)
                self.invocation_count += 1
                raw = self._transport.get(source_url, timeout_ms=min(10_000, deadline.effective_timeout_ms))
                if self._monotonic_ms() >= deadline.deadline_monotonic_ms:
                    raise TimeoutError
                result: LiveMarketDataResultDTO | ErrorResultDTO = self._parse(request, raw, source_url)
            except DeadlineExceededError:
                result = self._error(request.operation_id, "deadline_exceeded")
            except TimeoutError:
                result = self._error(request.operation_id, "provider_timeout", True)
            except BinanceTransportFailure as failure:
                allowed = {
                    "invalid_request", "provider_timeout", "provider_rate_limited",
                    "provider_unavailable", "provider_access_denied", "payload_too_large",
                }
                code = failure.code if failure.code in allowed else "unexpected_provider_error"
                result = self._error(request.operation_id, code, failure.retryable if code != "unexpected_provider_error" else False)
            except (ContractValidationError, InvalidOperation):
                result = self._error(request.operation_id, "invalid_market_bar")
            except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                result = self._error(request.operation_id, "invalid_provider_schema")
            except Exception as exception:
                result = map_unexpected_exception(exception, provider="binance_spot", operation_id=request.operation_id, occurred_at=self._now()).as_result()
            self._completed[request.operation_id] = (request, result)
            code = result.error.code if isinstance(result, ErrorResultDTO) else None
            self._emit(request.operation_id, "failed" if code else "succeeded", started_ms, code, asset=request.asset)
            return result

    def health_check(self, request: LiveMarketHealthRequestDTO) -> ProviderHealthDTO | ErrorResultDTO:
        started = self._monotonic_ms()
        try:
            deadline = self._deadline(request, 3_000)
            raw = self._transport.get(f"{BASE_URL}/api/v3/time", timeout_ms=deadline.effective_timeout_ms)
            if self._monotonic_ms() >= deadline.deadline_monotonic_ms:
                raise TimeoutError
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict) or set(value) != {"serverTime"} or type(value["serverTime"]) is not int:
                raise ValueError("invalid server time")
            return ProviderHealthDTO("binance_spot", "live_market_data", "healthy", _utc_text(self._now()), min(30_000, self._monotonic_ms() - started), None, None)
        except DeadlineExceededError:
            return self._error(request.operation_id, "deadline_exceeded")
        except TimeoutError:
            return self._error(request.operation_id, "provider_timeout", True)
        except BinanceTransportFailure as failure:
            allowed = {"provider_timeout", "provider_rate_limited", "provider_unavailable", "provider_access_denied"}
            code = failure.code if failure.code in allowed else "unexpected_provider_error"
            return self._error(request.operation_id, code, failure.retryable if code != "unexpected_provider_error" else False)
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return self._error(request.operation_id, "invalid_provider_schema")
        except Exception as exception:
            return map_unexpected_exception(exception, provider="binance_spot", operation_id=request.operation_id, occurred_at=self._now()).as_result()

    def capabilities(self, request: LiveMarketCapabilitiesRequestDTO) -> LiveMarketCapabilitiesDTO | ErrorResultDTO:
        try:
            self._deadline(request, 3_000)
            return LiveMarketCapabilitiesDTO()
        except DeadlineExceededError:
            return self._error(request.operation_id, "deadline_exceeded")


__all__ = (
    "BASE_URL", "BinanceLiveMarketDataProvider", "BinanceTransportFailure",
    "PinnedBinanceTransport", "PROVIDER_VERSION", "StubBinanceTransport",
)
