"""Application-owned typed outputs returned by Formal Run stages.

The DTOs in this module are immutable, bounded, and serializable.  They cross
an executor boundary; mutable pipeline state never does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from crypto_trust_agent.application.dto.reasoning import (
    AnalysisRefDTO,
    ContradictionDTO,
    ReasoningResultDTO,
)
from crypto_trust_agent.application.dto.repositories import (
    EvidenceAssessmentDTO,
    EvidenceClaimLinkDTO,
    EvidenceDTO,
    ExecutionEventDTO,
)
from crypto_trust_agent.domain.primitives import SCHEMA_VERSION

MAX_RAW_RECORDS = 200
MAX_EVIDENCE = 200
MAX_CLAIM_LINKS = 2_000
MAX_ASSESSMENTS = 1_000
MAX_ANALYSIS_REFS = 32
MAX_CONTRADICTIONS = 64
MAX_EVENTS = 500
MAX_LIMITATIONS = 50
MAX_LIMITATION_LENGTH = 512
MAX_CONTRIBUTIONS_PER_STEP = 64
_IDENTITY_PATTERN = re.compile(r"^(TASK|EXEC)-[A-Za-z0-9._:-]{1,123}$")


def _schema_version(value: str) -> None:
    if value != SCHEMA_VERSION:
        raise ValueError("unsupported contribution schema_version")


def _identity(task_id: str, execution_id: str, schema_version: str) -> None:
    _schema_version(schema_version)
    if not _IDENTITY_PATTERN.fullmatch(task_id) or not task_id.startswith("TASK-"):
        raise ValueError("invalid contribution task_id")
    if not _IDENTITY_PATTERN.fullmatch(execution_id) or not execution_id.startswith("EXEC-"):
        raise ValueError("invalid contribution execution_id")


def _bounded_unique(items: tuple[object, ...], maximum: int, key, name: str) -> tuple[object, ...]:
    normalized = tuple(items)
    if len(normalized) > maximum:
        raise ValueError(f"{name} exceeds maximum {maximum}")
    keys = tuple(key(item) for item in normalized)
    if len(keys) != len(set(keys)):
        raise ValueError(f"duplicate {name}")
    return normalized


def _wire(value: object) -> object:
    to_wire = getattr(value, "to_wire", None)
    if callable(to_wire):
        return to_wire()
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    return value


class PipelineContributionKind(str, Enum):
    COLLECTION = "collection"
    EVIDENCE = "evidence"
    ASSESSMENT = "assessment"
    ANALYSIS = "analysis"
    STRATEGY = "strategy"
    REASONING = "reasoning"
    EVENT = "event"


@dataclass(frozen=True, slots=True)
class CollectionContributionDTO:
    task_id: str
    execution_id: str
    raw_record_ids: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _identity(self.task_id, self.execution_id, self.schema_version)
        records = _bounded_unique(
            tuple(self.raw_record_ids), MAX_RAW_RECORDS, lambda item: item, "raw_record_ids"
        )
        if any(not isinstance(item, str) or not item.startswith("RAW-") or len(item) > 128 for item in records):
            raise ValueError("invalid raw_record_id")
        object.__setattr__(self, "raw_record_ids", records)

    def to_wire(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "raw_record_ids": list(self.raw_record_ids),
        }


@dataclass(frozen=True, slots=True)
class EvidenceContributionDTO:
    task_id: str
    execution_id: str
    evidence: tuple[EvidenceDTO, ...]
    claim_links: tuple[EvidenceClaimLinkDTO, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _identity(self.task_id, self.execution_id, self.schema_version)
        evidence = _bounded_unique(
            tuple(self.evidence), MAX_EVIDENCE, lambda item: item.evidence_id, "evidence"
        )
        links = _bounded_unique(
            tuple(self.claim_links), MAX_CLAIM_LINKS, lambda item: item.link_id, "claim_links"
        )
        if any(not isinstance(item, EvidenceDTO) for item in evidence):
            raise ValueError("invalid evidence contribution")
        if any(not isinstance(item, EvidenceClaimLinkDTO) for item in links):
            raise ValueError("invalid claim link contribution")
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "claim_links", links)

    def to_wire(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "evidence": _wire(self.evidence),
            "claim_links": _wire(self.claim_links),
        }


@dataclass(frozen=True, slots=True)
class AssessmentContributionDTO:
    task_id: str
    execution_id: str
    assessments: tuple[EvidenceAssessmentDTO, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _identity(self.task_id, self.execution_id, self.schema_version)
        assessments = _bounded_unique(
            tuple(self.assessments),
            MAX_ASSESSMENTS,
            lambda item: item.assessment_id,
            "assessments",
        )
        if any(not isinstance(item, EvidenceAssessmentDTO) for item in assessments):
            raise ValueError("invalid assessment contribution")
        object.__setattr__(self, "assessments", assessments)

    def to_wire(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "assessments": _wire(self.assessments),
        }


@dataclass(frozen=True, slots=True)
class AnalysisContributionDTO:
    task_id: str
    execution_id: str
    analysis_refs: tuple[AnalysisRefDTO, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _identity(self.task_id, self.execution_id, self.schema_version)
        refs = _bounded_unique(
            tuple(self.analysis_refs),
            MAX_ANALYSIS_REFS,
            lambda item: item.analysis_id,
            "analysis_refs",
        )
        if any(not isinstance(item, AnalysisRefDTO) for item in refs):
            raise ValueError("invalid analysis contribution")
        object.__setattr__(self, "analysis_refs", refs)

    def to_wire(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "analysis_refs": _wire(self.analysis_refs),
        }


@dataclass(frozen=True, slots=True)
class StrategyContributionDTO:
    task_id: str
    execution_id: str
    contradictions: tuple[ContradictionDTO, ...] = ()
    limitations: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _identity(self.task_id, self.execution_id, self.schema_version)
        contradictions = _bounded_unique(
            tuple(self.contradictions),
            MAX_CONTRADICTIONS,
            lambda item: item.contradiction_id,
            "contradictions",
        )
        limitations = tuple(self.limitations)
        if any(not isinstance(item, ContradictionDTO) for item in contradictions):
            raise ValueError("invalid contradiction contribution")
        if len(limitations) > MAX_LIMITATIONS or any(
            not isinstance(item, str) or not 1 <= len(item) <= MAX_LIMITATION_LENGTH
            for item in limitations
        ):
            raise ValueError("invalid strategy limitations")
        object.__setattr__(self, "contradictions", contradictions)
        object.__setattr__(self, "limitations", limitations)

    def to_wire(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "contradictions": _wire(self.contradictions),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class ReasoningContributionDTO:
    task_id: str
    execution_id: str
    reasoning_result: ReasoningResultDTO
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _identity(self.task_id, self.execution_id, self.schema_version)
        if not isinstance(self.reasoning_result, ReasoningResultDTO):
            raise ValueError("invalid reasoning contribution")

    def to_wire(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "reasoning_result": _wire(self.reasoning_result),
        }


@dataclass(frozen=True, slots=True)
class EventContributionDTO:
    task_id: str
    execution_id: str
    events: tuple[ExecutionEventDTO, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _identity(self.task_id, self.execution_id, self.schema_version)
        events = _bounded_unique(
            tuple(self.events), MAX_EVENTS, lambda item: item.event_id, "events"
        )
        if any(not isinstance(item, ExecutionEventDTO) for item in events):
            raise ValueError("invalid event contribution")
        object.__setattr__(self, "events", events)

    def to_wire(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "events": _wire(self.events),
        }


ContributionPayload: TypeAlias = (
    CollectionContributionDTO
    | EvidenceContributionDTO
    | AssessmentContributionDTO
    | AnalysisContributionDTO
    | StrategyContributionDTO
    | ReasoningContributionDTO
    | EventContributionDTO
)

_EXPECTED_PAYLOAD = {
    PipelineContributionKind.COLLECTION: CollectionContributionDTO,
    PipelineContributionKind.EVIDENCE: EvidenceContributionDTO,
    PipelineContributionKind.ASSESSMENT: AssessmentContributionDTO,
    PipelineContributionKind.ANALYSIS: AnalysisContributionDTO,
    PipelineContributionKind.STRATEGY: StrategyContributionDTO,
    PipelineContributionKind.REASONING: ReasoningContributionDTO,
    PipelineContributionKind.EVENT: EventContributionDTO,
}


@dataclass(frozen=True, slots=True)
class PipelineContributionDTO:
    kind: PipelineContributionKind | str
    payload: ContributionPayload
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version)
        kind = PipelineContributionKind(self.kind)
        if not isinstance(self.payload, _EXPECTED_PAYLOAD[kind]):
            raise ValueError("contribution kind/payload mismatch")
        object.__setattr__(self, "kind", kind)

    def to_wire(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "payload": self.payload.to_wire(),
        }


__all__ = (
    "AnalysisContributionDTO",
    "AssessmentContributionDTO",
    "CollectionContributionDTO",
    "ContributionPayload",
    "EventContributionDTO",
    "EvidenceContributionDTO",
    "MAX_ANALYSIS_REFS",
    "MAX_ASSESSMENTS",
    "MAX_CLAIM_LINKS",
    "MAX_CONTRADICTIONS",
    "MAX_CONTRIBUTIONS_PER_STEP",
    "MAX_EVENTS",
    "MAX_EVIDENCE",
    "MAX_LIMITATIONS",
    "MAX_LIMITATION_LENGTH",
    "MAX_RAW_RECORDS",
    "PipelineContributionDTO",
    "PipelineContributionKind",
    "ReasoningContributionDTO",
    "StrategyContributionDTO",
)
