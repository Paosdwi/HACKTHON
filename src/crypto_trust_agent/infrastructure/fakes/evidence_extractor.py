"""Deterministic, non-production EvidenceExtractor fake."""

from __future__ import annotations

from threading import RLock

from crypto_trust_agent.application.dto.common import (
    DeadlineExceededError,
    ErrorResultDTO,
    PortErrorCategory,
    PortErrorDTO,
    build_local_deadline,
    map_unexpected_exception,
)
from crypto_trust_agent.application.dto.evidence_extractor import (
    EXTRACT_ERROR_CODES,
    REPAIR_ERROR_CODES,
    ExtractRequestDTO,
    ExtractedClaimDTO,
    ExtractionProviderDTO,
    ExtractionResultDTO,
    ExtractorHealthCheckRequestDTO,
    InlineContentInputDTO,
    RepairRequestDTO,
    UsageDTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock


def _category(code: str) -> PortErrorCategory:
    if code in {"extractor_timeout", "deadline_exceeded"}:
        return PortErrorCategory.TIMEOUT
    if code == "extractor_rate_limited":
        return PortErrorCategory.RATE_LIMITED
    if code == "extractor_unavailable":
        return PortErrorCategory.UNAVAILABLE
    if code == "raw_record_not_safe":
        return PortErrorCategory.UNSAFE_SOURCE
    if code == "invalid_extraction_schema":
        return PortErrorCategory.INVALID_PROVIDER_OUTPUT
    return PortErrorCategory.VALIDATION


class FakeEvidenceExtractor:
    """Scenario fake which never performs model, network, filesystem, or AWS I/O."""

    non_production = True

    def __init__(
        self,
        clock: FakeClock,
        *,
        provider: str = "fake_extractor",
        model_version: str = "fake-1.0.0",
    ) -> None:
        self._clock = clock
        self._provider = provider
        self._model_version = model_version
        self._extract_scenarios: dict[str, object] = {}
        self._repair_scenarios: dict[str, object] = {}
        self._completed_extracts: dict[str, tuple[ExtractRequestDTO, ExtractionResultDTO | ErrorResultDTO]] = {}
        self._completed_repairs: dict[str, tuple[RepairRequestDTO, ExtractionResultDTO | ErrorResultDTO]] = {}
        self._repair_attempts: dict[tuple[str, str, str], str] = {}
        self._lock = RLock()
        self.invocation_count = 0

    def configure_extract(self, operation_id: str, response: object) -> None:
        with self._lock:
            self._extract_scenarios[operation_id] = response

    def configure_repair(self, operation_id: str, response: object) -> None:
        with self._lock:
            self._repair_scenarios[operation_id] = response

    def _error(self, operation_id: str, code: str) -> ErrorResultDTO:
        return ErrorResultDTO(PortErrorDTO(
            schema_version="1.0.0",
            code=code,
            category=_category(code),
            retryable=code in {"extractor_rate_limited", "extractor_timeout", "extractor_unavailable"},
            safe_message="Evidence extraction request failed.",
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

    def _default_extract(self, request: ExtractRequestDTO) -> ExtractionResultDTO:
        now = self._clock.current_utc()
        quote = request.content.clean_content if isinstance(request.content, InlineContentInputDTO) else "Content available through validated locator."
        claim = ExtractedClaimDTO(
            extracted_claim_id=f"XCL-{request.operation_id[3:]}",
            text="Deterministic fake claim.",
            quote=quote[:4_096],
            related_assets=(request.assets[0],),
            event_type=request.allowed_event_taxonomy[0],
            sentiment="neutral",
            relevance="high",
        )
        return ExtractionResultDTO(
            outcome="valid",
            raw_record_id=request.raw_record_id,
            provider=ExtractionProviderDTO(self._provider, self._model_version, f"INV-{request.operation_id[3:]}"),
            claims=(claim,),
            validation_errors=(),
            usage=UsageDTO(None, None),
            started_at=now,
            finished_at=now,
        )

    @staticmethod
    def _valid_result_semantics(request: ExtractRequestDTO, result: ExtractionResultDTO) -> bool:
        if result.raw_record_id != request.raw_record_id:
            return False
        if result.outcome == "valid" and (not result.claims or result.validation_errors):
            return False
        if result.outcome in {"invalid", "quarantined"} and not result.validation_errors:
            return False
        return all(
            set(claim.related_assets).issubset(request.assets)
            and claim.event_type in request.allowed_event_taxonomy
            for claim in result.claims
        )

    @staticmethod
    def _valid_repair_result(request: RepairRequestDTO, result: ExtractionResultDTO) -> bool:
        if result.raw_record_id != request.raw_record_id:
            return False
        if result.outcome == "valid":
            return bool(result.claims) and not result.validation_errors
        return bool(result.validation_errors)

    def _resolve(
        self,
        operation_id: str,
        configured: object | None,
        allowed_codes: tuple[str, ...],
        default: ExtractionResultDTO | ErrorResultDTO,
    ) -> ExtractionResultDTO | ErrorResultDTO:
        try:
            if isinstance(configured, BaseException):
                raise configured
            if isinstance(configured, str):
                return self._error(operation_id, configured) if configured in allowed_codes else self._error(operation_id, "invalid_extraction_schema")
            if isinstance(configured, ErrorResultDTO):
                return configured if configured.error.code in allowed_codes else self._error(operation_id, "invalid_extraction_schema")
            if isinstance(configured, ExtractionResultDTO):
                return configured
            if configured is None:
                return default
            return self._error(operation_id, "invalid_extraction_schema")
        except BaseException as exception:
            return map_unexpected_exception(
                exception,
                provider=self._provider,
                operation_id=operation_id,
                occurred_at=self._clock.current_utc().as_datetime(),
            ).as_result()

    def extract(self, request: ExtractRequestDTO) -> ExtractionResultDTO | ErrorResultDTO:
        with self._lock:
            completed = self._completed_extracts.get(request.operation_id)
            if completed is not None:
                if completed[0] == request:
                    return completed[1]
                return self._error(request.operation_id, "invalid_extraction_schema")
            expired = self._deadline_error(request, 60_000)
            if expired is not None:
                return expired
            self.invocation_count += 1
            result = self._resolve(
                request.operation_id,
                self._extract_scenarios.get(request.operation_id),
                EXTRACT_ERROR_CODES,
                self._default_extract(request),
            )
            if isinstance(result, ExtractionResultDTO) and not self._valid_result_semantics(request, result):
                result = self._error(request.operation_id, "invalid_extraction_schema")
            self._completed_extracts[request.operation_id] = (request, result)
            return result

    def repair(self, request: RepairRequestDTO) -> ExtractionResultDTO | ErrorResultDTO:
        with self._lock:
            completed = self._completed_repairs.get(request.operation_id)
            if completed is not None:
                if completed[0] == request:
                    return completed[1]
                return self._error(request.operation_id, "invalid_extraction_schema")
            identity = (request.raw_record_id, request.raw_content_hash, request.context_hash)
            prior_operation = self._repair_attempts.get(identity)
            if prior_operation is not None and prior_operation != request.operation_id:
                return self._error(request.operation_id, "invalid_extraction_schema")
            expired = self._deadline_error(request, 20_000)
            if expired is not None:
                return expired
            self._repair_attempts[identity] = request.operation_id
            self.invocation_count += 1
            result = self._resolve(
                request.operation_id,
                self._repair_scenarios.get(request.operation_id),
                REPAIR_ERROR_CODES,
                self._error(request.operation_id, "invalid_extraction_schema"),
            )
            if isinstance(result, ExtractionResultDTO) and not self._valid_repair_result(request, result):
                result = self._error(request.operation_id, "invalid_extraction_schema")
            self._completed_repairs[request.operation_id] = (request, result)
            return result

    def health_check(self, request: ExtractorHealthCheckRequestDTO) -> ProviderHealthDTO | ErrorResultDTO:
        expired = self._deadline_error(request, 3_000)
        if expired is not None:
            return expired
        return ProviderHealthDTO(
            provider=self._provider,
            capability="evidence_extraction",
            status="healthy",
            checked_at=self._clock.current_utc(),
            latency_ms=0,
            safe_reason_code=None,
            expires_at=None,
        )


__all__ = ("FakeEvidenceExtractor",)
