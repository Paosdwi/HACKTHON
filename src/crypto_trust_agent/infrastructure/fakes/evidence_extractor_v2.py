"""Deterministic, non-production EvidenceExtractor 2.0.0 fake."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from threading import RLock

from crypto_trust_agent.application.dto.common import (
    DeadlineExceededError,
    ErrorResultDTO,
    LocalDeadline,
    PortErrorCategory,
    PortErrorDTO,
    build_local_deadline,
)
from crypto_trust_agent.application.dto.evidence_extractor import (
    ExtractedClaimDTO,
    ExtractionProviderDTO,
    ExtractionResultDTO,
    ExtractorHealthCheckRequestDTO,
    ExtractRequestDTO,
    InlineContentInputDTO,
    LocatorContentInputDTO,
    UsageDTO,
)
from crypto_trust_agent.application.dto.evidence_extractor_v2 import (
    REPAIR_CONTENT_MAX_UTF8_BYTES,
    REPAIR_V2_ERROR_CODES,
    RepairRequestV2DTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock
from crypto_trust_agent.infrastructure.fakes.evidence_extractor import (
    FakeEvidenceExtractor,
)


@dataclass(frozen=True, slots=True)
class LocatorResolution:
    clean_content: str
    elapsed_ms: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.clean_content, str) or not self.clean_content:
            raise ValueError("locator clean_content is required")
        if type(self.elapsed_ms) is not int or self.elapsed_ms < 0:
            raise ValueError("locator elapsed_ms must be nonnegative")


@dataclass(frozen=True, slots=True)
class LateRepairResult:
    """Scenario marker: a provider result arrives after the bounded call ended."""

    result: ExtractionResultDTO
    elapsed_ms: int = 20_000

    def __post_init__(self) -> None:
        if not isinstance(self.result, ExtractionResultDTO):
            raise ValueError("late repair result is required")  # noqa: TRY004
        if type(self.elapsed_ms) is not int or self.elapsed_ms < 0:
            raise ValueError("late repair elapsed_ms must be nonnegative")


class FakeEvidenceExtractorV2:
    """Core-owned fake; never performs network, AWS, storage, or model I/O."""

    non_production = True

    def __init__(
        self,
        clock: FakeClock,
        *,
        provider: str = "fake_extractor",
        model_version: str = "fake-2.0.0",
    ) -> None:
        self._clock = clock
        self._provider = provider
        self._model_version = model_version
        self._v1 = FakeEvidenceExtractor(
            clock,
            provider=provider,
            model_version=model_version,
        )
        self._repair_scenarios: dict[str, object] = {}
        self._locator_scenarios: dict[str, object] = {}
        self._completed_repairs: dict[
            tuple[str, str],
            ExtractionResultDTO | ErrorResultDTO,
        ] = {}
        self._operation_authorizations: dict[str, str] = {}
        self._lock = RLock()
        self.provider_invocation_count = 0
        self.locator_resolution_count = 0

    def configure_extract(self, operation_id: str, response: object) -> None:
        self._v1.configure_extract(operation_id, response)

    def configure_repair(self, operation_id: str, response: object) -> None:
        with self._lock:
            self._repair_scenarios[operation_id] = response

    def configure_locator(self, locator: str, response: object) -> None:
        with self._lock:
            self._locator_scenarios[locator] = response

    @property
    def invocation_count(self) -> int:
        return self._v1.invocation_count + self.provider_invocation_count

    def _category(self, code: str) -> PortErrorCategory:
        if code in {"extractor_timeout", "deadline_exceeded"}:
            return PortErrorCategory.TIMEOUT
        if code == "extractor_rate_limited":
            return PortErrorCategory.RATE_LIMITED
        if code in {"extractor_unavailable", "repair_content_unavailable"}:
            return PortErrorCategory.UNAVAILABLE
        if code == "repair_content_hash_mismatch":
            return PortErrorCategory.INTEGRITY
        if code in {
            "repair_asset_scope_violation",
            "repair_event_taxonomy_violation",
            "invalid_extraction_schema",
        }:
            return PortErrorCategory.INVALID_PROVIDER_OUTPUT
        if code == "unexpected_provider_error":
            return PortErrorCategory.UNEXPECTED
        return PortErrorCategory.VALIDATION

    def _error(
        self,
        operation_id: str,
        code: str,
        *,
        category: PortErrorCategory | None = None,
        details: dict[str, str | int | bool | None] | None = None,
    ) -> ErrorResultDTO:
        return ErrorResultDTO(
            PortErrorDTO(
                schema_version="1.0.0",
                code=code,
                category=category or self._category(code),
                retryable=code
                in {
                    "extractor_rate_limited",
                    "extractor_timeout",
                    "extractor_unavailable",
                },
                safe_message="Evidence repair request failed.",
                provider=self._provider,
                operation_id=operation_id,
                details=details or {},
                occurred_at=self._clock.current_utc(),
            )
        )

    def _conflict(self, operation_id: str) -> ErrorResultDTO:
        return self._error(
            operation_id,
            "invalid_extraction_schema",
            category=PortErrorCategory.CONFLICT,
            details={"reason_code": "payload_conflict"},
        )

    def _local_deadline(
        self,
        request: RepairRequestV2DTO,
        timeout_ms: int,
    ) -> LocalDeadline | ErrorResultDTO:
        try:
            return build_local_deadline(
                request.deadline,
                provider_timeout_ms=timeout_ms,
                now_utc=self._clock.current_utc().as_datetime(),
                now_monotonic_ms=self._clock.current_monotonic_ms(),
                runtime_id=self._clock.runtime_id,
            )
        except DeadlineExceededError:
            return self._error(request.operation_id, "deadline_exceeded")

    def _expired(self, local_deadline: LocalDeadline, operation_id: str) -> ErrorResultDTO | None:
        if self._clock.current_monotonic_ms() >= local_deadline.deadline_monotonic_ms:
            return self._error(operation_id, "deadline_exceeded")
        return None

    def _resolve_content(
        self,
        request: RepairRequestV2DTO,
        local_deadline: LocalDeadline,
    ) -> str | ErrorResultDTO:
        if isinstance(request.content, InlineContentInputDTO):
            return request.content.clean_content
        if not isinstance(request.content, LocatorContentInputDTO):
            return self._error(request.operation_id, "invalid_extraction_schema")

        self.locator_resolution_count += 1
        configured = self._locator_scenarios.get(request.content.locator)
        try:
            if isinstance(configured, BaseException):
                raise configured
            if not isinstance(configured, LocatorResolution):
                return self._error(request.operation_id, "repair_content_unavailable")
            if configured.elapsed_ms:
                self._clock.advance(
                    wall_seconds=configured.elapsed_ms / 1_000,
                    monotonic_ms=configured.elapsed_ms,
                )
            expired = self._expired(local_deadline, request.operation_id)
            if expired is not None:
                return expired
            return configured.clean_content
        except BaseException:  # noqa: BLE001
            return self._error(request.operation_id, "repair_content_unavailable")

    def _default_result(
        self,
        request: RepairRequestV2DTO,
        clean_content: str,
    ) -> ExtractionResultDTO:
        now = self._clock.current_utc()
        return ExtractionResultDTO(
            outcome="valid",
            raw_record_id=request.raw_record_id,
            provider=ExtractionProviderDTO(
                self._provider,
                self._model_version,
                f"INV-{request.operation_id[3:]}",
            ),
            claims=(
                ExtractedClaimDTO(
                    extracted_claim_id=f"XCL-{request.operation_id[3:]}",
                    text="Deterministic repaired fake claim.",
                    quote=clean_content[:4_096],
                    related_assets=(request.assets[0],),
                    event_type=request.allowed_event_taxonomy[0],
                    sentiment="neutral",
                    relevance="high",
                ),
            ),
            validation_errors=(),
            usage=UsageDTO(None, None),
            started_at=now,
            finished_at=now,
        )

    def _resolve_provider(
        self,
        request: RepairRequestV2DTO,
        default: ExtractionResultDTO,
    ) -> ExtractionResultDTO | ErrorResultDTO:
        configured = self._repair_scenarios.get(request.operation_id)
        try:
            if isinstance(configured, LateRepairResult):
                if configured.elapsed_ms:
                    self._clock.advance(
                        wall_seconds=configured.elapsed_ms / 1_000,
                        monotonic_ms=configured.elapsed_ms,
                    )
                return configured.result
            if isinstance(configured, BaseException):
                raise configured
            if isinstance(configured, str):
                if configured in REPAIR_V2_ERROR_CODES:
                    return self._error(request.operation_id, configured)
                return self._error(request.operation_id, "invalid_extraction_schema")
            if isinstance(configured, ErrorResultDTO):
                if configured.error.code in REPAIR_V2_ERROR_CODES:
                    return configured
                return self._error(request.operation_id, "invalid_extraction_schema")
            if isinstance(configured, ExtractionResultDTO):
                return configured
            if configured is None:
                return default
            return self._error(request.operation_id, "invalid_extraction_schema")
        except BaseException:  # noqa: BLE001
            return self._error(request.operation_id, "unexpected_provider_error")

    def _validate_result(
        self,
        request: RepairRequestV2DTO,
        clean_content: str,
        result: ExtractionResultDTO | ErrorResultDTO,
    ) -> ExtractionResultDTO | ErrorResultDTO:
        if isinstance(result, ErrorResultDTO):
            return result
        if result.raw_record_id != request.raw_record_id:
            return self._error(request.operation_id, "invalid_extraction_schema")
        if result.outcome != "valid" or not result.claims or result.validation_errors:
            return self._error(request.operation_id, "invalid_extraction_schema")
        for claim in result.claims:
            if not set(claim.related_assets).issubset(request.assets):
                return self._error(request.operation_id, "repair_asset_scope_violation")
            if claim.event_type not in request.allowed_event_taxonomy:
                return self._error(
                    request.operation_id,
                    "repair_event_taxonomy_violation",
                )
            if claim.quote not in clean_content:
                return self._error(request.operation_id, "invalid_extraction_schema")
        return result

    def extract(self, request: ExtractRequestDTO) -> ExtractionResultDTO | ErrorResultDTO:
        return self._v1.extract(request)

    def repair(self, request: RepairRequestV2DTO) -> ExtractionResultDTO | ErrorResultDTO:
        with self._lock:
            if not isinstance(request, RepairRequestV2DTO):
                operation_id = getattr(request, "operation_id", "OP-INVALID-V2")
                return self._error(operation_id, "invalid_extraction_schema")

            # A supplied digest is never trusted as a replay lookup key until it
            # has been recomputed from the request's Core-owned authority fields.
            if request.repair_authorization_hash != request.expected_authorization_hash():
                return self._error(request.operation_id, "invalid_extraction_schema")

            identity = (request.operation_id, request.repair_authorization_hash)
            completed = self._completed_repairs.get(identity)
            if completed is not None:
                return completed

            prior_hash = self._operation_authorizations.get(request.operation_id)
            if prior_hash is not None and prior_hash != request.repair_authorization_hash:
                return self._conflict(request.operation_id)

            self._operation_authorizations[request.operation_id] = (
                request.repair_authorization_hash
            )

            # Only a fresh, validated replay identity receives a new local
            # deadline. Completed replays above intentionally ignore envelope
            # changes, including an already-expired replay deadline.
            local_deadline = self._local_deadline(request, 20_000)
            if isinstance(local_deadline, ErrorResultDTO):
                self._completed_repairs[identity] = local_deadline
                return local_deadline

            clean_content = self._resolve_content(request, local_deadline)
            if isinstance(clean_content, ErrorResultDTO):
                self._completed_repairs[identity] = clean_content
                return clean_content

            encoded_content = clean_content.encode("utf-8")
            if len(encoded_content) > REPAIR_CONTENT_MAX_UTF8_BYTES:
                result = self._error(request.operation_id, "input_too_large")
                self._completed_repairs[identity] = result
                return result

            actual_hash = "sha256:" + hashlib.sha256(encoded_content).hexdigest()
            if actual_hash != request.clean_content_hash:
                result = self._error(
                    request.operation_id,
                    "repair_content_hash_mismatch",
                )
                self._completed_repairs[identity] = result
                return result

            expired = self._expired(local_deadline, request.operation_id)
            if expired is not None:
                self._completed_repairs[identity] = expired
                return expired

            self.provider_invocation_count += 1
            provider_result = self._resolve_provider(
                request,
                self._default_result(request, clean_content),
            )
            expired = self._expired(local_deadline, request.operation_id)
            if expired is not None:
                self._completed_repairs[identity] = expired
                return expired
            terminal_result = self._validate_result(
                request,
                clean_content,
                provider_result,
            )
            self._completed_repairs[identity] = terminal_result
            return terminal_result

    def health_check(
        self,
        request: ExtractorHealthCheckRequestDTO,
    ) -> ProviderHealthDTO | ErrorResultDTO:
        return self._v1.health_check(request)


__all__ = (
    "FakeEvidenceExtractorV2",
    "LateRepairResult",
    "LocatorResolution",
)
