"""Deterministic, non-production SourceCollector fake with security guards."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from threading import RLock
from typing import Mapping
from urllib.parse import urlsplit

from crypto_trust_agent.application.dto.common import (
    DeadlineExceededError,
    ErrorResultDTO,
    PortErrorCategory,
    PortErrorDTO,
    build_local_deadline,
    map_unexpected_exception,
)
from crypto_trust_agent.application.dto.source_collector import (
    COLLECT_ERROR_CODES,
    MAX_CLEAN_BYTES,
    MAX_RAW_BYTES,
    MAX_REDIRECTS,
    CapabilitiesRequestDTO,
    CollectionResultDTO,
    CollectRequestDTO,
    CollectorCapabilitiesDTO,
    CollectorHealthCheckRequestDTO,
    QueryProvenanceDTO,
    RawRecordDTO,
    SecurityResultDTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock

_HASH_A = "sha256:" + "a" * 64
_HASH_B = "sha256:" + "b" * 64
_METADATA_HOSTS = {"169.254.169.254", "metadata.google.internal", "metadata.aws.internal"}


@dataclass(frozen=True, slots=True)
class _Scenario:
    response: object
    redirect_urls: tuple[str, ...]
    raw_decompressed_bytes: int | None
    cleaned_bytes: int | None


def _category(code: str) -> PortErrorCategory:
    if code in {"fetch_timeout", "deadline_exceeded"}:
        return PortErrorCategory.TIMEOUT
    if code in {"source_rate_limited"}:
        return PortErrorCategory.RATE_LIMITED
    if code in {"collector_unavailable", "collector_not_configured"}:
        return PortErrorCategory.UNAVAILABLE
    if code in {"source_not_allowlisted", "dns_ip_rejected", "ssrf_blocked", "robots_disallowed"}:
        return PortErrorCategory.UNSAFE_SOURCE
    if code in {"invalid_source_schema"}:
        return PortErrorCategory.INVALID_PROVIDER_OUTPUT
    return PortErrorCategory.VALIDATION


class FakeSourceCollector:
    """Scenario-driven collector fake; performs no DNS, HTTP, browser, or AWS I/O."""

    non_production = True

    def __init__(
        self,
        clock: FakeClock,
        *,
        provider: str = "fake_collector",
        allowed_hosts: tuple[str, ...] = ("example.com",),
        source_categories: tuple[str, ...] = ("market", "news", "official", "on_chain", "social", "macro"),
        supports_static: bool = True,
        supports_dynamic: bool = True,
    ) -> None:
        self._clock = clock
        self._provider = provider
        self._allowed_hosts = frozenset(host.lower().rstrip(".") for host in allowed_hosts)
        self._source_categories = tuple(source_categories)
        self._supports_static = supports_static
        self._supports_dynamic = supports_dynamic
        self._scenarios: dict[str, _Scenario] = {}
        self._completed: dict[str, tuple[CollectRequestDTO, CollectionResultDTO | ErrorResultDTO]] = {}
        self._lock = RLock()
        self.io_count = 0

    def configure(
        self,
        operation_id: str,
        response: object,
        *,
        redirect_urls: tuple[str, ...] = (),
        raw_decompressed_bytes: int | None = None,
        cleaned_bytes: int | None = None,
    ) -> None:
        """Configure one deterministic invocation without extending the wire contract."""
        with self._lock:
            self._scenarios[operation_id] = _Scenario(
                response,
                tuple(redirect_urls),
                raw_decompressed_bytes,
                cleaned_bytes,
            )

    def _error(self, operation_id: str, code: str) -> ErrorResultDTO:
        return ErrorResultDTO(PortErrorDTO(
            schema_version="1.0.0",
            code=code,
            category=_category(code),
            retryable=code in {"source_rate_limited", "fetch_timeout", "collector_unavailable"},
            safe_message="Collector request failed.",
            provider=self._provider,
            operation_id=operation_id,
            details={},
            occurred_at=self._clock.current_utc(),
        ))

    def _deadline_error(self, request: object, timeout_ms: int) -> ErrorResultDTO | None:
        try:
            build_local_deadline(
                request.deadline,
                provider_timeout_ms=timeout_ms,
                now_utc=self._clock.current_utc().as_datetime(),
                now_monotonic_ms=self._clock.current_monotonic_ms(),
                runtime_id=self._clock.runtime_id,
            )
        except DeadlineExceededError:
            return self._error(request.operation_id, "deadline_exceeded")
        return None

    def _url_error(self, url: str) -> str | None:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return "source_not_allowlisted"
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None or port not in {None, 443}:
            return "source_not_allowlisted"
        if host in _METADATA_HOSTS:
            return "ssrf_blocked"
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            if str(address) == "169.254.169.254":
                return "ssrf_blocked"
            if not address.is_global or address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast:
                return "dns_ip_rejected"
        if host not in self._allowed_hosts:
            return "source_not_allowlisted"
        return None

    def _default_result(self, request: CollectRequestDTO, redirects: int) -> CollectionResultDTO:
        now = self._clock.current_utc()
        source_url = request.approved_urls[0] if request.approved_urls else "https://example.com/generated"
        record = RawRecordDTO(
            raw_record_id=f"RAW-{request.operation_id[3:]}",
            task_id=request.task_id,
            execution_id=request.execution_id,
            plan_job_id=request.plan_job_id,
            source_name="Fake deterministic source",
            source_type=request.source_category,
            source_url=source_url,
            canonical_url=source_url,
            http_status=200,
            published_at=None,
            fetched_at=now,
            content_hash=_HASH_A,
            clean_content_hash=_HASH_B,
            raw_locator=f"urn:cryptotrust:raw:RAW-{request.operation_id[3:]}",
            media_type="text/plain",
            clean_content="bounded source text",
            query_provenance=QueryProvenanceDTO(self._provider, request.approved_query, {"asset": request.assets[0]}),
            security=SecurityResultDTO("1.0.0", True, True, True, redirects),
        )
        return CollectionResultDTO(request.operation_id, "success", (record,), now, now, 0, 0, (), ())

    def _validate_result(self, request: CollectRequestDTO, result: CollectionResultDTO) -> bool:
        if result.operation_id != request.operation_id:
            return False
        return all(
            record.task_id == request.task_id
            and record.execution_id == request.execution_id
            and record.plan_job_id == request.plan_job_id
            and record.source_type == request.source_category
            for record in result.records
        )

    def collect(self, request: CollectRequestDTO) -> CollectionResultDTO | ErrorResultDTO:
        with self._lock:
            completed = self._completed.get(request.operation_id)
            if completed is not None:
                if completed[0] == request:
                    return completed[1]
                return self._error(request.operation_id, "invalid_source_schema")

            expired = self._deadline_error(request, 15_000 if request.collection_mode == "static" else 30_000)
            if expired is not None:
                return expired
            if request.source_category not in self._source_categories:
                return self._error(request.operation_id, "unsupported_category")
            if request.collection_mode == "static" and not self._supports_static:
                return self._error(request.operation_id, "collector_not_configured")
            if request.collection_mode == "playwright" and not self._supports_dynamic:
                return self._error(request.operation_id, "collector_not_configured")
            for url in request.approved_urls:
                code = self._url_error(url)
                if code is not None:
                    return self._error(request.operation_id, code)

            scenario = self._scenarios.get(request.operation_id, _Scenario(None, (), None, None))
            if len(scenario.redirect_urls) > MAX_REDIRECTS:
                return self._error(request.operation_id, "redirect_limit_exceeded")
            for url in scenario.redirect_urls:
                code = self._url_error(url)
                if code is not None:
                    return self._error(request.operation_id, code)
            if scenario.raw_decompressed_bytes is not None and scenario.raw_decompressed_bytes > MAX_RAW_BYTES:
                return self._error(request.operation_id, "payload_too_large")
            if scenario.cleaned_bytes is not None and scenario.cleaned_bytes > MAX_CLEAN_BYTES:
                return self._error(request.operation_id, "payload_too_large")

            self.io_count += 1
            try:
                configured = scenario.response
                if isinstance(configured, BaseException):
                    raise configured
                if isinstance(configured, str):
                    result: CollectionResultDTO | ErrorResultDTO = (
                        self._error(request.operation_id, configured)
                        if configured in COLLECT_ERROR_CODES
                        else self._error(request.operation_id, "invalid_source_schema")
                    )
                elif isinstance(configured, ErrorResultDTO):
                    result = configured if configured.error.code in COLLECT_ERROR_CODES else self._error(request.operation_id, "invalid_source_schema")
                elif isinstance(configured, CollectionResultDTO):
                    result = configured if self._validate_result(request, configured) else self._error(request.operation_id, "invalid_source_schema")
                elif configured is None:
                    result = self._default_result(request, len(scenario.redirect_urls))
                else:
                    result = self._error(request.operation_id, "invalid_source_schema")
            except BaseException as exception:
                result = map_unexpected_exception(
                    exception,
                    provider=self._provider,
                    operation_id=request.operation_id,
                    occurred_at=self._clock.current_utc().as_datetime(),
                ).as_result()
            self._completed[request.operation_id] = (request, result)
            return result

    def health_check(self, request: CollectorHealthCheckRequestDTO) -> ProviderHealthDTO | ErrorResultDTO:
        expired = self._deadline_error(request, 3_000)
        if expired is not None:
            return expired
        if request.provider != self._provider:
            return self._error(request.operation_id, "collector_not_configured")
        return ProviderHealthDTO(
            provider=self._provider,
            capability="source_collection",
            status="healthy",
            checked_at=self._clock.current_utc(),
            latency_ms=0,
            safe_reason_code=None,
            expires_at=None,
        )

    def capabilities(self, request: CapabilitiesRequestDTO) -> CollectorCapabilitiesDTO | ErrorResultDTO:
        expired = self._deadline_error(request, 3_000)
        if expired is not None:
            return expired
        if request.provider != self._provider:
            return self._error(request.operation_id, "collector_not_configured")
        return CollectorCapabilitiesDTO(
            provider=self._provider,
            source_categories=self._source_categories,
            supports_static=self._supports_static,
            supports_dynamic=self._supports_dynamic,
            security_policy_versions=("collector-security-1.0.0",),
        )


__all__ = ("FakeSourceCollector",)
