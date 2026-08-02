"""Deterministic non-production ReasoningProvider fake."""

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
from crypto_trust_agent.application.dto.reasoning import (
    GENERATE_ERROR_CODES,
    HEALTH_ERROR_CODES,
    REPAIR_ERROR_CODES,
    ConclusionDTO,
    ConfidenceComponentsDTO,
    FactDTO,
    GenerateRequestDTO,
    InferenceDTO,
    ProviderDTO,
    ReasoningHealthCheckRequestDTO,
    ReasoningResultDTO,
    RepairRequestDTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock


def _category(code: str) -> PortErrorCategory:
    if code in {"reasoning_timeout", "deadline_exceeded"}:
        return PortErrorCategory.TIMEOUT
    if code == "reasoning_rate_limited":
        return PortErrorCategory.RATE_LIMITED
    if code in {"model_unavailable", "fallback_unavailable"}:
        return PortErrorCategory.UNAVAILABLE
    if code in {"reasoning_schema_invalid", "citation_invalid", "numeric_inconsistency"}:
        return PortErrorCategory.INVALID_PROVIDER_OUTPUT
    return PortErrorCategory.VALIDATION


class FakeReasoningProvider:
    """One-attempt scenario fake with no network, SDK, secrets, tools, or hidden retry."""

    non_production = True

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self._generate_scenarios: dict[str, object] = {}
        self._repair_scenarios: dict[str, object] = {}
        self._health_scenarios: dict[str, object] = {}
        self._completed_generate: dict[str, tuple[GenerateRequestDTO, ReasoningResultDTO | ErrorResultDTO]] = {}
        self._completed_repair: dict[str, tuple[RepairRequestDTO, ReasoningResultDTO | ErrorResultDTO]] = {}
        self._completed_health: dict[str, tuple[ReasoningHealthCheckRequestDTO, ProviderHealthDTO | ErrorResultDTO]] = {}
        self._repairable: dict[
            tuple[str, str, str], tuple[GenerateRequestDTO, ReasoningResultDTO]
        ] = {}
        self._repaired_contexts: set[tuple[str, str, str]] = set()
        self._lock = RLock()
        self.invocation_count = 0

    def configure_generate(self, operation_id: str, response: object) -> None:
        with self._lock:
            self._generate_scenarios[operation_id] = response

    def configure_repair(self, operation_id: str, response: object) -> None:
        with self._lock:
            self._repair_scenarios[operation_id] = response

    def configure_health(self, operation_id: str, response: object) -> None:
        with self._lock:
            self._health_scenarios[operation_id] = response

    def _error(self, operation_id: str, code: str) -> ErrorResultDTO:
        return ErrorResultDTO(PortErrorDTO(
            "1.0.0",
            code,
            _category(code),
            False,
            "Reasoning request failed.",
            "reasoning_provider",
            operation_id,
            {},
            self._clock.current_utc(),
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

    def _default_result(self, request: GenerateRequestDTO) -> ReasoningResultDTO:
        evidence_refs: tuple[str, ...] = ()
        analysis_refs: tuple[str, ...] = ()
        if request.context.evidence_refs:
            evidence_refs = (request.context.evidence_refs[0].evidence_id,)
        elif request.context.analysis_refs:
            analysis_refs = (request.context.analysis_refs[0].analysis_id,)
        facts: tuple[FactDTO, ...] = ()
        inferences: tuple[InferenceDTO, ...] = ()
        conclusions: tuple[ConclusionDTO, ...] = ()
        if evidence_refs or analysis_refs:
            facts = (FactDTO("FACT-001", "A bounded cited fact.", evidence_refs, analysis_refs),)
            inferences = (InferenceDTO("INFER-001", "A bounded inference.", ("FACT-001",), "0.8"),)
            conclusions = (ConclusionDTO("CONCL-001", "A bounded conclusion.", ("FACT-001",), ("INFER-001",), "0.75"),)
        now = self._clock.current_utc()
        return ReasoningResultDTO(
            "valid",
            ProviderDTO("fake_reasoning_model", "fake-v1", request.model_role, f"INV-{request.operation_id[3:]}"),
            facts,
            inferences,
            conclusions,
            request.context.limitations,
            (),
            ConfidenceComponentsDTO("0.8", "0.8", "0.8", "0.8"),
            (),
            now,
            now,
        )

    def _default_repair(self, request: RepairRequestDTO) -> ReasoningResultDTO:
        now = self._clock.current_utc()
        return ReasoningResultDTO(
            "valid",
            ProviderDTO("fake_reasoning_model", "fake-v1", "primary", f"INV-{request.operation_id[3:]}"),
            (), (), (), (), (),
            ConfidenceComponentsDTO("0.7", "0.7", "0.7", "0.7"),
            (), now, now,
        )

    @staticmethod
    def _citations_valid(request: GenerateRequestDTO, result: ReasoningResultDTO) -> bool:
        if result.provider.model_role != request.model_role:
            return False
        evidence_ids = {item.evidence_id for item in request.context.evidence_refs}
        analysis_ids = {item.analysis_id for item in request.context.analysis_refs}
        return all(
            set(fact.evidence_refs) <= evidence_ids and set(fact.analysis_refs) <= analysis_ids
            for fact in result.facts
        )

    def generate(self, request: GenerateRequestDTO) -> ReasoningResultDTO | ErrorResultDTO:
        with self._lock:
            completed = self._completed_generate.get(request.operation_id)
            if completed is not None:
                if completed[0] == request:
                    return completed[1]
                return self._error(request.operation_id, "context_invalid")
            expired = self._deadline_error(request, 60_000)
            if expired is not None:
                return expired
            self.invocation_count += 1
            configured = self._generate_scenarios.get(request.operation_id)
            try:
                if isinstance(configured, BaseException):
                    raise configured
                if configured is None:
                    result: ReasoningResultDTO | ErrorResultDTO = self._default_result(request)
                elif isinstance(configured, str):
                    result = self._error(request.operation_id, configured if configured in GENERATE_ERROR_CODES else "reasoning_schema_invalid")
                elif isinstance(configured, ErrorResultDTO):
                    result = configured if configured.error.code in GENERATE_ERROR_CODES else self._error(request.operation_id, "reasoning_schema_invalid")
                elif isinstance(configured, ReasoningResultDTO):
                    result = configured
                else:
                    result = self._error(request.operation_id, "reasoning_schema_invalid")
                if isinstance(result, ReasoningResultDTO) and not self._citations_valid(request, result):
                    result = self._error(request.operation_id, "citation_invalid")
            except BaseException as exception:
                result = map_unexpected_exception(
                    exception,
                    provider="reasoning_provider",
                    operation_id=request.operation_id,
                    occurred_at=self._clock.current_utc().as_datetime(),
                ).as_result()
            if isinstance(result, ReasoningResultDTO) and result.outcome == "invalid" and request.model_role == "primary":
                identity = (request.task_id, request.execution_id, request.context_hash)
                self._repairable[identity] = (request, result)
            self._completed_generate[request.operation_id] = (request, result)
            return result

    def repair(self, request: RepairRequestDTO) -> ReasoningResultDTO | ErrorResultDTO:
        with self._lock:
            completed = self._completed_repair.get(request.operation_id)
            if completed is not None:
                if completed[0] == request:
                    return completed[1]
                return self._error(request.operation_id, "reasoning_schema_invalid")
            identity = (request.task_id, request.execution_id, request.context_hash)
            repairable = self._repairable.get(identity)
            if (
                identity in self._repaired_contexts
                or repairable is None
                or repairable[1] != request.original_result
            ):
                return self._error(request.operation_id, "reasoning_schema_invalid")
            expired = self._deadline_error(request, 60_000)
            if expired is not None:
                return expired
            self.invocation_count += 1
            configured = self._repair_scenarios.get(request.operation_id)
            try:
                if isinstance(configured, BaseException):
                    raise configured
                if configured is None:
                    result: ReasoningResultDTO | ErrorResultDTO = self._default_repair(request)
                elif isinstance(configured, str):
                    result = self._error(request.operation_id, configured if configured in REPAIR_ERROR_CODES else "reasoning_schema_invalid")
                elif isinstance(configured, ErrorResultDTO):
                    result = configured if configured.error.code in REPAIR_ERROR_CODES else self._error(request.operation_id, "reasoning_schema_invalid")
                elif isinstance(configured, ReasoningResultDTO) and configured.provider.model_role == "primary":
                    result = configured
                else:
                    result = self._error(request.operation_id, "reasoning_schema_invalid")
                if isinstance(result, ReasoningResultDTO) and not self._citations_valid(repairable[0], result):
                    result = self._error(request.operation_id, "citation_invalid")
            except BaseException as exception:
                result = map_unexpected_exception(
                    exception,
                    provider="reasoning_provider",
                    operation_id=request.operation_id,
                    occurred_at=self._clock.current_utc().as_datetime(),
                ).as_result()
            self._repaired_contexts.add(identity)
            self._completed_repair[request.operation_id] = (request, result)
            return result

    def health_check(self, request: ReasoningHealthCheckRequestDTO) -> ProviderHealthDTO | ErrorResultDTO:
        with self._lock:
            completed = self._completed_health.get(request.operation_id)
            if completed is not None:
                if completed[0] == request:
                    return completed[1]
                return self._error(request.operation_id, "unexpected_provider_error")
            expired = self._deadline_error(request, 3_000)
            if expired is not None:
                return expired
            configured = self._health_scenarios.get(request.operation_id)
            try:
                if isinstance(configured, BaseException):
                    raise configured
                if configured is None:
                    result: ProviderHealthDTO | ErrorResultDTO = ProviderHealthDTO(
                        "reasoning_provider",
                        f"{request.model_role}_reasoning",
                        "healthy",
                        self._clock.current_utc(),
                        0,
                        None,
                        None,
                    )
                elif isinstance(configured, str):
                    result = self._error(
                        request.operation_id,
                        configured if configured in HEALTH_ERROR_CODES else "unexpected_provider_error",
                    )
                elif isinstance(configured, ErrorResultDTO):
                    result = configured if configured.error.code in HEALTH_ERROR_CODES else self._error(request.operation_id, "unexpected_provider_error")
                elif isinstance(configured, ProviderHealthDTO):
                    result = configured
                else:
                    result = self._error(request.operation_id, "unexpected_provider_error")
            except BaseException as exception:
                result = map_unexpected_exception(
                    exception,
                    provider="reasoning_provider",
                    operation_id=request.operation_id,
                    occurred_at=self._clock.current_utc().as_datetime(),
                ).as_result()
            self._completed_health[request.operation_id] = (request, result)
            return result

    @staticmethod
    def is_publishable(result: ReasoningResultDTO | ErrorResultDTO) -> bool:
        return isinstance(result, ReasoningResultDTO) and result.outcome == "valid"


__all__ = ("FakeReasoningProvider",)
