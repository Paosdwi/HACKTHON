"""Frozen ReasoningProvider DTOs for contract version 1.0.0."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields
from typing import Any

from crypto_trust_agent.application.dto.common import DeadlineDTO
from crypto_trust_agent.domain.primitives import CanonicalDecimal, ContractValidationError, UtcInstant

SCHEMA_VERSION = "1.0.0"
GENERATE_ERROR_CODES = (
    "context_invalid", "context_too_large", "guardrail_rejected",
    "reasoning_schema_invalid", "citation_invalid", "numeric_inconsistency",
    "reasoning_timeout", "reasoning_rate_limited", "model_unavailable",
    "fallback_unavailable", "deadline_exceeded", "unexpected_provider_error",
)
REPAIR_ERROR_CODES = (
    "guardrail_rejected", "reasoning_schema_invalid", "citation_invalid",
    "numeric_inconsistency", "reasoning_timeout", "reasoning_rate_limited",
    "model_unavailable", "deadline_exceeded", "unexpected_provider_error",
)
HEALTH_ERROR_CODES = (
    "reasoning_timeout", "model_unavailable", "fallback_unavailable",
    "deadline_exceeded", "unexpected_provider_error",
)

_OPERATION = re.compile(r"^OP-[A-Z0-9][A-Z0-9._:-]{0,126}$")
_TASK = re.compile(r"^TASK-[A-Za-z0-9._:-]{1,123}$")
_EXECUTION = re.compile(r"^EXEC-[A-Za-z0-9._:-]{1,123}$")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_POLICY_VERSION = re.compile(r"^[a-z0-9_-]+-[0-9]+\.[0-9]+\.[0-9]+$")
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractValidationError(message)


def _bounded_text(value: str, maximum: int, name: str) -> None:
    _require(isinstance(value, str) and 1 <= len(value) <= maximum, f"invalid {name}")


def _unique(values: tuple[Any, ...], key: Any, name: str) -> None:
    keys = [key(item) for item in values]
    _require(len(keys) == len(set(keys)), f"duplicate {name}")


def _wire(value: Any) -> Any:
    if hasattr(value, "to_wire"):
        return value.to_wire()
    if isinstance(value, (CanonicalDecimal, UtcInstant)):
        return str(value)
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    return value


class WireDTO:
    def to_wire(self) -> dict[str, object]:
        values = {field.name: _wire(getattr(self, field.name)) for field in fields(self)}
        if "schema_version" in values:
            return {"schema_version": values.pop("schema_version"), **values}
        return values


@dataclass(frozen=True, slots=True)
class EvidenceRefDTO(WireDTO):
    evidence_id: str
    assessment_id: str
    excerpt: str
    stance: str
    confidence: CanonicalDecimal | str

    def __post_init__(self) -> None:
        _require(isinstance(self.evidence_id, str) and self.evidence_id.startswith("EVID-"), "invalid evidence_id")
        _require(isinstance(self.assessment_id, str) and self.assessment_id.startswith("ASSESS-"), "invalid assessment_id")
        _bounded_text(self.excerpt, 2_000, "excerpt")
        _require(self.stance in {"supports", "contradicts", "context"}, "invalid stance")
        confidence = self.confidence if isinstance(self.confidence, CanonicalDecimal) else CanonicalDecimal(self.confidence)
        confidence.require_probability()
        object.__setattr__(self, "confidence", confidence)


@dataclass(frozen=True, slots=True)
class AnalysisRefDTO(WireDTO):
    analysis_id: str
    analysis_version: str
    summary: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _require(isinstance(self.analysis_id, str) and self.analysis_id.startswith("ANALYSIS-"), "invalid analysis_id")
        _require(isinstance(self.analysis_version, str) and _VERSION.fullmatch(self.analysis_version) is not None, "invalid analysis_version")
        _bounded_text(self.summary, 2_000, "analysis summary")
        refs = tuple(self.source_refs)
        _require(1 <= len(refs) <= 100 and len(set(refs)) == len(refs), "invalid analysis source_refs")
        _require(all(isinstance(item, str) and 1 <= len(item) <= 256 for item in refs), "invalid analysis source_ref")
        object.__setattr__(self, "source_refs", refs)


@dataclass(frozen=True, slots=True)
class ContradictionDTO(WireDTO):
    contradiction_id: str
    evidence_refs: tuple[str, ...]
    severity: str
    summary: str

    def __post_init__(self) -> None:
        _require(isinstance(self.contradiction_id, str) and self.contradiction_id.startswith("CONTRA-"), "invalid contradiction_id")
        refs = tuple(self.evidence_refs)
        _require(2 <= len(refs) <= 20 and len(set(refs)) == len(refs), "invalid contradiction evidence_refs")
        _require(all(isinstance(item, str) and item.startswith("EVID-") for item in refs), "invalid contradiction evidence_ref")
        _require(self.severity in {"low", "medium", "high"}, "invalid contradiction severity")
        _bounded_text(self.summary, 2_000, "contradiction summary")
        object.__setattr__(self, "evidence_refs", refs)


@dataclass(frozen=True, slots=True)
class OmissionDTO(WireDTO):
    kind: str
    count: int
    reason: str = "deterministic_context_limit"

    def __post_init__(self) -> None:
        _require(self.kind in {"evidence", "analysis", "contradiction", "limitation"}, "invalid omission kind")
        _require(type(self.count) is int and self.count >= 1, "invalid omission count")
        _require(self.reason == "deterministic_context_limit", "invalid omission reason")


@dataclass(frozen=True, slots=True)
class ReasoningContextDTO(WireDTO):
    question: str
    evidence_refs: tuple[EvidenceRefDTO, ...]
    analysis_refs: tuple[AnalysisRefDTO, ...]
    contradictions: tuple[ContradictionDTO, ...]
    limitations: tuple[str, ...]
    omissions: tuple[OmissionDTO, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _bounded_text(self.question, 2_000, "question")
        evidence = tuple(self.evidence_refs)
        analyses = tuple(self.analysis_refs)
        contradictions = tuple(self.contradictions)
        limitations = tuple(self.limitations)
        omissions = tuple(self.omissions)
        _require(len(evidence) <= 120 and all(isinstance(item, EvidenceRefDTO) for item in evidence), "invalid evidence_refs")
        _require(len(analyses) <= 32 and all(isinstance(item, AnalysisRefDTO) for item in analyses), "invalid analysis_refs")
        _require(len(contradictions) <= 64 and all(isinstance(item, ContradictionDTO) for item in contradictions), "invalid contradictions")
        _require(len(limitations) <= 50 and all(isinstance(item, str) and 1 <= len(item) <= 512 for item in limitations), "invalid limitations")
        _require(len(omissions) <= 100 and all(isinstance(item, OmissionDTO) for item in omissions), "invalid omissions")
        _unique(evidence, lambda item: item.evidence_id, "evidence_ref")
        _unique(analyses, lambda item: item.analysis_id, "analysis_ref")
        _unique(contradictions, lambda item: item.contradiction_id, "contradiction")
        _require(len(limitations) == len(set(limitations)), "duplicate limitation")
        _unique(omissions, lambda item: item.kind, "omission kind")
        object.__setattr__(self, "evidence_refs", evidence)
        object.__setattr__(self, "analysis_refs", analyses)
        object.__setattr__(self, "contradictions", contradictions)
        object.__setattr__(self, "limitations", limitations)
        object.__setattr__(self, "omissions", omissions)
        _require(len(self.canonical_json()) <= 524_288, "context_too_large")

    def canonical_json(self) -> bytes:
        return json.dumps(self.to_wire(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def context_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_json()).hexdigest()


@dataclass(frozen=True, slots=True)
class GenerateRequestDTO(WireDTO):
    operation_id: str
    task_id: str
    execution_id: str
    model_role: str
    context: ReasoningContextDTO
    context_hash: str
    output_schema_version: str
    guardrail_policy_version: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _require(isinstance(self.operation_id, str) and _OPERATION.fullmatch(self.operation_id) is not None, "invalid operation_id")
        _require(isinstance(self.task_id, str) and _TASK.fullmatch(self.task_id) is not None, "invalid task_id")
        _require(isinstance(self.execution_id, str) and _EXECUTION.fullmatch(self.execution_id) is not None, "invalid execution_id")
        _require(self.model_role in {"primary", "fallback"}, "invalid model_role")
        _require(isinstance(self.context, ReasoningContextDTO), "invalid context")
        _require(isinstance(self.context_hash, str) and _HASH.fullmatch(self.context_hash) is not None, "invalid context_hash")
        _require(self.context_hash == self.context.context_hash(), "context_hash mismatch")
        _require(self.output_schema_version == SCHEMA_VERSION, "invalid output_schema_version")
        _require(isinstance(self.guardrail_policy_version, str) and _POLICY_VERSION.fullmatch(self.guardrail_policy_version) is not None, "invalid guardrail_policy_version")
        _require(isinstance(self.deadline, DeadlineDTO) and self.deadline.operation_id == self.operation_id, "deadline operation_id mismatch")


@dataclass(frozen=True, slots=True)
class ProviderDTO(WireDTO):
    name: str
    model_version: str
    model_role: str
    invocation_id: str

    def __post_init__(self) -> None:
        for name, value in (("name", self.name), ("model_version", self.model_version), ("invocation_id", self.invocation_id)):
            _bounded_text(value, 128, name)
        _require(self.model_role in {"primary", "fallback"}, "invalid model_role")


@dataclass(frozen=True, slots=True)
class FactDTO(WireDTO):
    fact_id: str
    statement: str
    evidence_refs: tuple[str, ...]
    analysis_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _require(isinstance(self.fact_id, str) and self.fact_id.startswith("FACT-"), "invalid fact_id")
        _bounded_text(self.statement, 2_000, "fact statement")
        evidence = tuple(self.evidence_refs)
        analyses = tuple(self.analysis_refs)
        _require(len(evidence) <= 50 and len(evidence) == len(set(evidence)) and all(item.startswith("EVID-") for item in evidence), "invalid fact evidence_refs")
        _require(len(analyses) <= 50 and len(analyses) == len(set(analyses)) and all(item.startswith("ANALYSIS-") for item in analyses), "invalid fact analysis_refs")
        _require(bool(evidence or analyses), "fact requires a citation")
        object.__setattr__(self, "evidence_refs", evidence)
        object.__setattr__(self, "analysis_refs", analyses)


@dataclass(frozen=True, slots=True)
class InferenceDTO(WireDTO):
    inference_id: str
    statement: str
    fact_refs: tuple[str, ...]
    confidence: CanonicalDecimal | str

    def __post_init__(self) -> None:
        _require(isinstance(self.inference_id, str) and self.inference_id.startswith("INFER-"), "invalid inference_id")
        _bounded_text(self.statement, 2_000, "inference statement")
        refs = tuple(self.fact_refs)
        _require(1 <= len(refs) <= 50 and len(refs) == len(set(refs)) and all(item.startswith("FACT-") for item in refs), "invalid inference fact_refs")
        confidence = self.confidence if isinstance(self.confidence, CanonicalDecimal) else CanonicalDecimal(self.confidence)
        confidence.require_probability()
        object.__setattr__(self, "fact_refs", refs)
        object.__setattr__(self, "confidence", confidence)


@dataclass(frozen=True, slots=True)
class ConclusionDTO(WireDTO):
    conclusion_id: str
    statement: str
    fact_refs: tuple[str, ...]
    inference_refs: tuple[str, ...]
    confidence: CanonicalDecimal | str

    def __post_init__(self) -> None:
        _require(isinstance(self.conclusion_id, str) and self.conclusion_id.startswith("CONCL-"), "invalid conclusion_id")
        _bounded_text(self.statement, 2_000, "conclusion statement")
        facts = tuple(self.fact_refs)
        inferences = tuple(self.inference_refs)
        _require(len(facts) <= 50 and len(facts) == len(set(facts)) and all(item.startswith("FACT-") for item in facts), "invalid conclusion fact_refs")
        _require(len(inferences) <= 50 and len(inferences) == len(set(inferences)) and all(item.startswith("INFER-") for item in inferences), "invalid conclusion inference_refs")
        _require(bool(facts or inferences), "conclusion requires a citation")
        confidence = self.confidence if isinstance(self.confidence, CanonicalDecimal) else CanonicalDecimal(self.confidence)
        confidence.require_probability()
        object.__setattr__(self, "fact_refs", facts)
        object.__setattr__(self, "inference_refs", inferences)
        object.__setattr__(self, "confidence", confidence)


@dataclass(frozen=True, slots=True)
class ConfidenceComponentsDTO(WireDTO):
    evidence_quality: CanonicalDecimal | str
    consistency: CanonicalDecimal | str
    coverage: CanonicalDecimal | str
    overall: CanonicalDecimal | str

    def __post_init__(self) -> None:
        for name in ("evidence_quality", "consistency", "coverage", "overall"):
            raw = getattr(self, name)
            value = raw if isinstance(raw, CanonicalDecimal) else CanonicalDecimal(raw)
            value.require_probability()
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class DiagnosticDTO(WireDTO):
    path: str
    code: str
    safe_message: str

    def __post_init__(self) -> None:
        _bounded_text(self.path, 256, "diagnostic path")
        _require(isinstance(self.code, str) and _SAFE_CODE.fullmatch(self.code) is not None, "invalid diagnostic code")
        _bounded_text(self.safe_message, 512, "diagnostic safe_message")


@dataclass(frozen=True, slots=True)
class ReasoningResultDTO(WireDTO):
    outcome: str
    provider: ProviderDTO
    facts: tuple[FactDTO, ...]
    inferences: tuple[InferenceDTO, ...]
    conclusions: tuple[ConclusionDTO, ...]
    limitations: tuple[str, ...]
    watchpoints: tuple[str, ...]
    confidence_components: ConfidenceComponentsDTO
    validation_diagnostics: tuple[DiagnosticDTO, ...]
    started_at: UtcInstant | str
    finished_at: UtcInstant | str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _require(self.outcome in {"valid", "invalid"}, "invalid reasoning outcome")
        _require(isinstance(self.provider, ProviderDTO), "invalid provider")
        facts = tuple(self.facts)
        inferences = tuple(self.inferences)
        conclusions = tuple(self.conclusions)
        limitations = tuple(self.limitations)
        watchpoints = tuple(self.watchpoints)
        diagnostics = tuple(self.validation_diagnostics)
        _require(len(facts) <= 200 and all(isinstance(item, FactDTO) for item in facts), "invalid facts")
        _require(len(inferences) <= 100 and all(isinstance(item, InferenceDTO) for item in inferences), "invalid inferences")
        _require(len(conclusions) <= 50 and all(isinstance(item, ConclusionDTO) for item in conclusions), "invalid conclusions")
        _require(len(limitations) <= 50 and all(isinstance(item, str) and 1 <= len(item) <= 512 for item in limitations), "invalid limitations")
        _require(len(watchpoints) <= 50 and all(isinstance(item, str) and 1 <= len(item) <= 512 for item in watchpoints), "invalid watchpoints")
        _require(len(diagnostics) <= 100 and all(isinstance(item, DiagnosticDTO) for item in diagnostics), "invalid diagnostics")
        _require(isinstance(self.confidence_components, ConfidenceComponentsDTO), "invalid confidence_components")
        _unique(facts, lambda item: item.fact_id, "fact")
        _unique(inferences, lambda item: item.inference_id, "inference")
        _unique(conclusions, lambda item: item.conclusion_id, "conclusion")
        fact_ids = {item.fact_id for item in facts}
        inference_ids = {item.inference_id for item in inferences}
        _require(all(set(item.fact_refs) <= fact_ids for item in inferences), "inference citation_invalid")
        _require(all(set(item.fact_refs) <= fact_ids and set(item.inference_refs) <= inference_ids for item in conclusions), "conclusion citation_invalid")
        started = self.started_at if isinstance(self.started_at, UtcInstant) else UtcInstant(self.started_at)
        finished = self.finished_at if isinstance(self.finished_at, UtcInstant) else UtcInstant(self.finished_at)
        _require(started.as_datetime() <= finished.as_datetime(), "invalid reasoning timestamps")
        object.__setattr__(self, "facts", facts)
        object.__setattr__(self, "inferences", inferences)
        object.__setattr__(self, "conclusions", conclusions)
        object.__setattr__(self, "limitations", limitations)
        object.__setattr__(self, "watchpoints", watchpoints)
        object.__setattr__(self, "validation_diagnostics", diagnostics)
        object.__setattr__(self, "started_at", started)
        object.__setattr__(self, "finished_at", finished)


@dataclass(frozen=True, slots=True)
class RepairRequestDTO(WireDTO):
    operation_id: str
    task_id: str
    execution_id: str
    context_hash: str
    original_result: ReasoningResultDTO
    validator_errors: tuple[DiagnosticDTO, ...]
    output_schema_version: str
    guardrail_policy_version: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _require(isinstance(self.operation_id, str) and _OPERATION.fullmatch(self.operation_id) is not None, "invalid operation_id")
        _require(isinstance(self.task_id, str) and _TASK.fullmatch(self.task_id) is not None, "invalid task_id")
        _require(isinstance(self.execution_id, str) and _EXECUTION.fullmatch(self.execution_id) is not None, "invalid execution_id")
        _require(isinstance(self.context_hash, str) and _HASH.fullmatch(self.context_hash) is not None, "invalid context_hash")
        _require(isinstance(self.original_result, ReasoningResultDTO) and self.original_result.outcome == "invalid" and self.original_result.provider.model_role == "primary", "repair requires invalid primary result")
        errors = tuple(self.validator_errors)
        _require(1 <= len(errors) <= 100 and all(isinstance(item, DiagnosticDTO) for item in errors), "invalid validator_errors")
        _require(self.output_schema_version == SCHEMA_VERSION, "invalid output_schema_version")
        _require(isinstance(self.guardrail_policy_version, str) and _POLICY_VERSION.fullmatch(self.guardrail_policy_version) is not None, "invalid guardrail_policy_version")
        _require(isinstance(self.deadline, DeadlineDTO) and self.deadline.operation_id == self.operation_id, "deadline operation_id mismatch")
        object.__setattr__(self, "validator_errors", errors)


@dataclass(frozen=True, slots=True)
class ReasoningHealthCheckRequestDTO(WireDTO):
    operation_id: str
    model_role: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _require(isinstance(self.operation_id, str) and _OPERATION.fullmatch(self.operation_id) is not None, "invalid operation_id")
        _require(self.model_role in {"primary", "fallback"}, "invalid model_role")
        _require(isinstance(self.deadline, DeadlineDTO) and self.deadline.operation_id == self.operation_id, "deadline operation_id mismatch")


__all__ = (
    "AnalysisRefDTO", "ConclusionDTO", "ConfidenceComponentsDTO", "ContradictionDTO",
    "DiagnosticDTO", "EvidenceRefDTO", "FactDTO", "GENERATE_ERROR_CODES",
    "GenerateRequestDTO", "HEALTH_ERROR_CODES", "InferenceDTO", "OmissionDTO",
    "ProviderDTO", "REPAIR_ERROR_CODES", "ReasoningContextDTO",
    "ReasoningHealthCheckRequestDTO", "ReasoningResultDTO", "RepairRequestDTO",
)
