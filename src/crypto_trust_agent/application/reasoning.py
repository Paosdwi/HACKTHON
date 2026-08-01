"""Deterministic Structured Reasoning Context construction."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import timedelta
import re
from threading import Event, Thread

from crypto_trust_agent.application.dto.common import (
    DeadlineDTO,
    DeadlineExceededError,
    ErrorResultDTO,
    PortErrorCategory,
    PortErrorDTO,
    build_local_deadline,
    map_unexpected_exception,
)
from crypto_trust_agent.application.dto.repositories import ClockReadRequestDTO
from crypto_trust_agent.application.dto.reasoning import (
    AnalysisRefDTO,
    ContradictionDTO,
    DiagnosticDTO,
    EvidenceRefDTO,
    GenerateRequestDTO,
    OmissionDTO,
    ReasoningContextDTO,
    ReasoningResultDTO,
    RepairRequestDTO,
)
from crypto_trust_agent.application.ports.reasoning import ReasoningProvider
from crypto_trust_agent.application.ports.repositories import Clock
from crypto_trust_agent.domain.errors import LineageViolation
from crypto_trust_agent.domain.evidence import (
    AnalysisResult,
    Evidence,
    EvidenceAssessment,
    ValidationStatus,
    select_latest_assessments,
)
from crypto_trust_agent.domain.primitives import ContractValidationError
from crypto_trust_agent.domain.trust import Contradiction

_CONTEXT_BYTE_LIMIT = 524_288
_CONTEXT_TOKEN_LIMIT = 64_000
_SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}
_TOKENIZER_VERSION = re.compile(r"^[a-z0-9_-]+-[0-9]+\.[0-9]+\.[0-9]+$")
_FORBIDDEN_CONTEXT_PATTERNS = (
    re.compile(r"<!--|<!doctype|<[^>]+>|&(?:lt|gt|#x?[0-9a-f]+);", re.IGNORECASE),
    re.compile(r"\bauthorization\s*:", re.IGNORECASE),
    re.compile(r"\bbearer\s+[a-z0-9._~+/=-]{4,}", re.IGNORECASE),
    re.compile(r"\b(?:aws_secret_access_key|aws_access_key_id|api[_-]?key|secret|token)\s*[:=]", re.IGNORECASE),
    re.compile(r"[?&](?:x-amz-(?:credential|signature|security-token)|x-goog-(?:credential|signature|security-token|algorithm)|signature|credential|key-pair-id|policy)=", re.IGNORECASE),
    re.compile(r"\b(?:raw_prompt|prompt_history|hidden_reasoning|chain_of_thought)\s*:", re.IGNORECASE),
)
_REQUIREMENT_PRIORITY = {"required": 0, "required_if_available": 1, "optional": 2}
_RANKING_VERSION = "reasoning-ranking-1.0.0"


@dataclass(frozen=True, slots=True)
class ReasoningTokenCounter:
    """Versioned tokenizer binding for one frozen model role."""

    model_role: str
    model_version: str
    tokenizer_version: str
    count_tokens: Callable[[str], int] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.model_role not in {"primary", "fallback"}:
            raise ContractValidationError("invalid tokenizer model_role")
        if not isinstance(self.model_version, str) or not 1 <= len(self.model_version) <= 128:
            raise ContractValidationError("invalid tokenizer model_version")
        if not isinstance(self.tokenizer_version, str) or _TOKENIZER_VERSION.fullmatch(self.tokenizer_version) is None:
            raise ContractValidationError("invalid tokenizer_version")
        if not callable(self.count_tokens):
            raise ContractValidationError("token counter must be callable")


@dataclass(frozen=True, slots=True)
class EvidenceRankingInput:
    """Versioned strategy projection used only for deterministic context ranking."""

    evidence_id: str
    requirement: str
    independence_representative: bool
    ranking_version: str = _RANKING_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not self.evidence_id.startswith("EVID-"):
            raise ContractValidationError("invalid ranking evidence_id")
        if self.requirement not in _REQUIREMENT_PRIORITY:
            raise ContractValidationError("invalid ranking requirement")
        if type(self.independence_representative) is not bool:
            raise ContractValidationError("invalid independence representative")
        if self.ranking_version != _RANKING_VERSION:
            raise ContractValidationError("unsupported reasoning ranking version")


class StructuredReasoningContextBuilder:
    """Projects Core entities into the frozen, bounded provider context."""

    def __init__(self, *, token_counters: tuple[ReasoningTokenCounter, ...]) -> None:
        counters = tuple(token_counters)
        if {item.model_role for item in counters} != {"primary", "fallback"} or len(counters) != 2:
            raise ContractValidationError("primary and fallback tokenizer bindings are required")
        self._token_counters = counters

    def token_count(self, context: ReasoningContextDTO) -> int:
        canonical = context.canonical_json().decode("utf-8")
        counts = tuple(item.count_tokens(canonical) for item in self._token_counters)
        if any(type(value) is not int or value < 0 for value in counts):
            raise ContractValidationError("token counter returned an invalid value")
        return max(counts)

    @staticmethod
    def _assert_safe_text(value: str) -> None:
        if any(pattern.search(value) for pattern in _FORBIDDEN_CONTEXT_PATTERNS):
            raise ContractValidationError("unsafe content is forbidden in reasoning context")

    def build(
        self,
        *,
        task_id: str,
        question: str,
        evidence: Iterable[Evidence],
        assessments: Iterable[EvidenceAssessment],
        stances: Mapping[str, str],
        ranking_inputs: Mapping[str, EvidenceRankingInput],
        analyses: Iterable[AnalysisResult],
        contradictions: Iterable[Contradiction],
        limitations: Iterable[str],
    ) -> ReasoningContextDTO:
        self._assert_safe_text(question)
        evidence_items = tuple(evidence)
        assessment_items = tuple(assessments)
        analysis_items = tuple(sorted(tuple(analyses), key=lambda item: item.analysis_id))
        contradiction_items = tuple(sorted(tuple(contradictions), key=lambda item: item.contradiction_id))
        limitation_items = tuple(limitations)
        for item in limitation_items:
            self._assert_safe_text(item)

        evidence_ids = {item.evidence_id for item in evidence_items}
        if len(evidence_ids) != len(evidence_items):
            raise LineageViolation("duplicate Evidence in reasoning context")
        for item in evidence_items:
            if item.task_id != task_id:
                raise LineageViolation("reasoning Evidence must be task-scoped")
            if item.validation_status is not ValidationStatus.ACTIVE:
                raise LineageViolation("quarantined Evidence cannot enter reasoning context")
        if any(item.task_id != task_id or item.evidence_id not in evidence_ids for item in assessment_items):
            raise LineageViolation("reasoning assessment must reference task-scoped Evidence")
        if any(evidence_id not in evidence_ids for evidence_id in stances):
            raise LineageViolation("reasoning stance references missing Evidence")
        ranking_by_id = dict(ranking_inputs)
        if set(ranking_by_id) != evidence_ids or any(
            not isinstance(item, EvidenceRankingInput) or item.evidence_id != evidence_id
            for evidence_id, item in ranking_by_id.items()
        ):
            raise LineageViolation("every reasoning Evidence requires a matching ranking input")
        latest = select_latest_assessments(assessment_items)
        if set(latest) != evidence_ids:
            raise LineageViolation("every reasoning Evidence requires a latest assessment")
        for item in evidence_items:
            self._assert_safe_text(item.content_reference.value)

        evidence_refs = [
            EvidenceRefDTO(
                item.evidence_id,
                latest[item.evidence_id].assessment_id,
                item.content_reference.value[:2_000],
                stances.get(item.evidence_id, "context"),
                latest[item.evidence_id].overall_confidence,
            )
            for item in evidence_items
        ]

        analysis_refs: list[AnalysisRefDTO] = []
        derived_limitations = list(limitation_items)
        for item in analysis_items:
            if item.task_id != task_id:
                raise LineageViolation("reasoning Analysis must be task-scoped")
            if any(ref.startswith("EVID-") and ref not in evidence_ids for ref in item.input_refs):
                raise LineageViolation("reasoning Analysis references missing Evidence")
            for source_ref in item.source_refs:
                self._assert_safe_text(source_ref)
            analysis_refs.append(AnalysisRefDTO(
                self._analysis_id(item.analysis_id),
                item.producer.version,
                f"{item.asset} {item.analysis_type} analysis ({item.quality.status})."[:2_000],
                item.source_refs,
            ))
            derived_limitations.extend(item.quality.limitations)

        contradiction_refs: list[ContradictionDTO] = []
        for item in contradiction_items:
            if item.task_id != task_id:
                raise LineageViolation("reasoning contradiction must be task-scoped")
            refs = (item.left_ref, item.right_ref)
            if any(ref not in evidence_ids for ref in refs):
                raise LineageViolation("reasoning contradiction references missing Evidence")
            severity = max(
                (latest[ref].contradiction_severity.value for ref in refs),
                key=lambda value: _SEVERITY_ORDER[value],
            )
            if severity == "none":
                severity = "low"
            contradiction_refs.append(ContradictionDTO(
                self._contradiction_id(item.contradiction_id),
                refs,
                severity,
                f"{item.conflict_type.value} conflict between cited Evidence."[:2_000],
            ))

        unique_limitations = sorted(set(derived_limitations))
        if any(not isinstance(item, str) or not 1 <= len(item) <= 512 for item in unique_limitations):
            raise ContractValidationError("invalid reasoning limitation")
        for item in unique_limitations:
            self._assert_safe_text(item)

        protected_evidence_ids = {ref for item in contradiction_refs for ref in item.evidence_refs}
        evidence_refs.sort(key=lambda item: (
            _REQUIREMENT_PRIORITY[ranking_by_id[item.evidence_id].requirement],
            0 if (
                item.evidence_id in protected_evidence_ids
                or item.stance == "contradicts"
            ) else 1,
            -latest[item.evidence_id].relevance.as_decimal(),
            -latest[item.evidence_id].source_trust.as_decimal(),
            -latest[item.evidence_id].freshness.as_decimal(),
            0 if ranking_by_id[item.evidence_id].independence_representative else 1,
            item.evidence_id,
        ))

        omitted: dict[str, int] = {}
        evidence_refs = self._limit(evidence_refs, 120, "evidence", omitted)
        selected_evidence = {item.evidence_id for item in evidence_refs}
        kept_contradictions = [
            item for item in contradiction_refs
            if set(item.evidence_refs) <= selected_evidence
        ]
        if len(kept_contradictions) != len(contradiction_refs):
            omitted["contradiction"] = omitted.get("contradiction", 0) + len(contradiction_refs) - len(kept_contradictions)
        contradiction_refs = self._limit(kept_contradictions, 64, "contradiction", omitted)
        analysis_refs = self._limit(analysis_refs, 32, "analysis", omitted)
        unique_limitations = self._limit(unique_limitations, 50, "limitation", omitted)

        while True:
            try:
                context = self._context(
                    question,
                    evidence_refs,
                    analysis_refs,
                    contradiction_refs,
                    unique_limitations,
                    omitted,
                )
            except ContractValidationError as error:
                if str(error) != "context_too_large" or not self._drop_one(
                    evidence_refs, analysis_refs, contradiction_refs, unique_limitations, omitted
                ):
                    raise
                continue
            if len(context.canonical_json()) <= _CONTEXT_BYTE_LIMIT and self.token_count(context) <= _CONTEXT_TOKEN_LIMIT:
                return context
            if not self._drop_one(evidence_refs, analysis_refs, contradiction_refs, unique_limitations, omitted):
                raise ContractValidationError("context_too_large")

    @staticmethod
    def _analysis_id(value: str) -> str:
        if not value.startswith("AN-"):
            raise LineageViolation("unsupported Analysis ID projection")
        return "ANALYSIS-" + value[3:]

    @staticmethod
    def _contradiction_id(value: str) -> str:
        if not value.startswith("CON-"):
            raise LineageViolation("unsupported contradiction ID projection")
        return "CONTRA-" + value[4:]

    @staticmethod
    def _limit(values: list, maximum: int, kind: str, omitted: dict[str, int]) -> list:
        if len(values) > maximum:
            omitted[kind] = omitted.get(kind, 0) + len(values) - maximum
            return values[:maximum]
        return values

    @staticmethod
    def _context(
        question: str,
        evidence: list[EvidenceRefDTO],
        analyses: list[AnalysisRefDTO],
        contradictions: list[ContradictionDTO],
        limitations: list[str],
        omitted: dict[str, int],
    ) -> ReasoningContextDTO:
        omissions = tuple(OmissionDTO(kind, count) for kind, count in sorted(omitted.items()))
        return ReasoningContextDTO(
            question,
            tuple(evidence),
            tuple(analyses),
            tuple(contradictions),
            tuple(limitations),
            omissions,
        )

    @staticmethod
    def _drop_one(
        evidence: list[EvidenceRefDTO],
        analyses: list[AnalysisRefDTO],
        contradictions: list[ContradictionDTO],
        limitations: list[str],
        omitted: dict[str, int],
    ) -> bool:
        for kind, values in (
            ("limitation", limitations),
            ("analysis", analyses),
            ("evidence", evidence),
            ("contradiction", contradictions),
        ):
            if values:
                removed = values.pop()
                omitted[kind] = omitted.get(kind, 0) + 1
                if kind == "evidence":
                    removed_id = removed.evidence_id
                    retained = [item for item in contradictions if removed_id not in item.evidence_refs]
                    omitted_count = len(contradictions) - len(retained)
                    if omitted_count:
                        contradictions[:] = retained
                        omitted["contradiction"] = omitted.get("contradiction", 0) + omitted_count
                return True
        return False


@dataclass(frozen=True, slots=True)
class ReasoningSequenceOutcome:
    result: ReasoningResultDTO | ErrorResultDTO
    attempts: tuple[str, ...]
    publishable: bool


class ReasoningSequenceRunner:
    """Owns primary/repair/fallback order with receiver-local runtime guards."""

    def __init__(
        self,
        provider: ReasoningProvider,
        clock: Clock,
        *,
        token_counters: tuple[ReasoningTokenCounter, ...],
    ) -> None:
        counters = tuple(token_counters)
        if {item.model_role for item in counters} != {"primary", "fallback"} or len(counters) != 2:
            raise ContractValidationError("primary and fallback tokenizer bindings are required")
        self._provider = provider
        self._clock = clock
        self._expected_model_versions = {
            item.model_role: item.model_version for item in counters
        }

    def run(
        self,
        primary: GenerateRequestDTO,
        repair_operation_id: str,
        repair_deadline: DeadlineDTO,
        fallback: GenerateRequestDTO,
    ) -> ReasoningSequenceOutcome:
        self._validate_boundaries(primary, repair_operation_id, repair_deadline, fallback)
        attempts = ["primary_generate"]
        primary_result = self._validated_result(
            self._guarded_call(
                primary.operation_id,
                primary.deadline,
                lambda: self._provider.generate(primary),
            ),
            primary.context,
            "primary",
            self._expected_model_versions["primary"],
        )
        if self._publishable(primary_result):
            return ReasoningSequenceOutcome(primary_result, tuple(attempts), True)

        if (
            isinstance(primary_result, ReasoningResultDTO)
            and primary_result.outcome == "invalid"
            and primary_result.validation_diagnostics
        ):
            attempts.append("primary_repair")
            repair = RepairRequestDTO(
                repair_operation_id,
                primary.task_id,
                primary.execution_id,
                primary.context_hash,
                primary_result,
                primary_result.validation_diagnostics,
                primary.output_schema_version,
                primary.guardrail_policy_version,
                repair_deadline,
            )
            repaired = self._validated_result(
                self._guarded_call(
                    repair.operation_id,
                    repair.deadline,
                    lambda: self._provider.repair(repair),
                ),
                primary.context,
                "primary",
                self._expected_model_versions["primary"],
            )
            if self._publishable(repaired):
                return ReasoningSequenceOutcome(repaired, tuple(attempts), True)

        attempts.append("fallback_generate")
        fallback_result = self._validated_result(
            self._guarded_call(
                fallback.operation_id,
                fallback.deadline,
                lambda: self._provider.generate(fallback),
            ),
            fallback.context,
            "fallback",
            self._expected_model_versions["fallback"],
        )
        return ReasoningSequenceOutcome(
            fallback_result,
            tuple(attempts),
            self._publishable(fallback_result),
        )

    def _guarded_call(
        self,
        operation_id: str,
        deadline: DeadlineDTO,
        invoke: Callable[[], object],
    ) -> ReasoningResultDTO | ErrorResultDTO:
        try:
            utc = self._clock.now_utc(ClockReadRequestDTO(operation_id))
            monotonic = self._clock.monotonic_ms(ClockReadRequestDTO(operation_id))
        except BaseException as exception:
            return map_unexpected_exception(
                exception,
                provider="reasoning_provider",
                operation_id=operation_id,
                occurred_at=deadline.sent_at_utc.as_datetime(),
            ).as_result()
        if isinstance(utc, ErrorResultDTO) or isinstance(monotonic, ErrorResultDTO):
            return self._runtime_error(
                operation_id,
                "unexpected_provider_error",
                PortErrorCategory.UNEXPECTED,
                deadline.sent_at_utc,
            )
        try:
            local = build_local_deadline(
                deadline,
                provider_timeout_ms=60_000,
                now_utc=utc.utc.as_datetime(),
                now_monotonic_ms=monotonic.monotonic_ms,
                runtime_id=monotonic.runtime_id,
            )
        except DeadlineExceededError:
            return self._runtime_error(
                operation_id,
                "deadline_exceeded",
                PortErrorCategory.TIMEOUT,
                utc.utc,
            )

        completed = Event()
        payload: list[ReasoningResultDTO | ErrorResultDTO] = []

        def guarded_invoke() -> None:
            try:
                result = invoke()
                if not isinstance(result, (ReasoningResultDTO, ErrorResultDTO)):
                    result = self._runtime_error(
                        operation_id,
                        "unexpected_provider_error",
                        PortErrorCategory.UNEXPECTED,
                        utc.utc,
                    )
            except BaseException as exception:
                result = map_unexpected_exception(
                    exception,
                    provider="reasoning_provider",
                    operation_id=operation_id,
                    occurred_at=utc.utc.as_datetime(),
                ).as_result()
            payload.append(result)
            completed.set()

        Thread(
            target=guarded_invoke,
            name=f"reasoning-{operation_id}",
            daemon=True,
        ).start()
        if not completed.wait(local.effective_timeout_ms / 1_000):
            return self._runtime_error(
                operation_id,
                "deadline_exceeded",
                PortErrorCategory.TIMEOUT,
                utc.utc,
            )
        return payload[0]

    @staticmethod
    def _runtime_error(
        operation_id: str,
        code: str,
        category: PortErrorCategory,
        occurred_at: object,
    ) -> ErrorResultDTO:
        return ErrorResultDTO(PortErrorDTO(
            "1.0.0",
            code,
            category,
            False,
            "Reasoning request did not complete safely.",
            "reasoning_provider",
            operation_id,
            {},
            occurred_at,
        ))

    @staticmethod
    def _validate_boundaries(
        primary: GenerateRequestDTO,
        repair_operation_id: str,
        repair_deadline: DeadlineDTO,
        fallback: GenerateRequestDTO,
    ) -> None:
        if primary.model_role != "primary" or fallback.model_role != "fallback":
            raise ContractValidationError("invalid reasoning sequence roles")
        if (
            fallback.task_id != primary.task_id
            or fallback.execution_id != primary.execution_id
            or fallback.context_hash != primary.context_hash
            or fallback.context != primary.context
        ):
            raise ContractValidationError("reasoning fallback context mismatch")
        if repair_deadline.operation_id != repair_operation_id:
            raise ContractValidationError("repair deadline operation_id mismatch")
        primary_window = (
            primary.deadline.deadline_at_utc.as_datetime()
            - primary.deadline.sent_at_utc.as_datetime()
        )
        if (
            primary.deadline.budget_ms > 60_000
            or primary_window <= timedelta(0)
            or primary_window > timedelta(seconds=60)
            or repair_deadline.budget_ms > 60_000
            or repair_deadline.deadline_at_utc != primary.deadline.deadline_at_utc
            or repair_deadline.sent_at_utc.as_datetime()
            < primary.deadline.sent_at_utc.as_datetime()
        ):
            raise ContractValidationError("repair must share the primary 60 second window")

    @staticmethod
    def _validated_result(
        result: ReasoningResultDTO | ErrorResultDTO,
        context: ReasoningContextDTO,
        expected_role: str,
        expected_model_version: str,
    ) -> ReasoningResultDTO | ErrorResultDTO:
        if not isinstance(result, ReasoningResultDTO):
            return result
        diagnostics = list(result.validation_diagnostics)
        if (
            result.provider.model_role != expected_role
            or result.provider.model_version != expected_model_version
        ):
            diagnostics.append(DiagnosticDTO(
                "/provider",
                "provider_model_mismatch",
                "Provider role or model version does not match the tokenizer binding.",
            ))
        evidence_ids = {item.evidence_id for item in context.evidence_refs}
        analysis_ids = {item.analysis_id for item in context.analysis_refs}
        if not all(
            set(fact.evidence_refs) <= evidence_ids
            and set(fact.analysis_refs) <= analysis_ids
            for fact in result.facts
        ):
            diagnostics.append(DiagnosticDTO(
                "/facts",
                "citation_invalid",
                "A result citation does not resolve to the bounded context.",
            ))
        if len(diagnostics) == len(result.validation_diagnostics):
            return result
        return replace(
            result,
            outcome="invalid",
            validation_diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def _publishable(result: ReasoningResultDTO | ErrorResultDTO) -> bool:
        return isinstance(result, ReasoningResultDTO) and result.outcome == "valid"


__all__ = (
    "EvidenceRankingInput",
    "ReasoningSequenceOutcome",
    "ReasoningSequenceRunner",
    "ReasoningTokenCounter",
    "StructuredReasoningContextBuilder",
)
