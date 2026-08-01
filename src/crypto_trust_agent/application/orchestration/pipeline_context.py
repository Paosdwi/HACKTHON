"""Execution-local, bounded Formal Run stage-output accumulator."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Sequence

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
from crypto_trust_agent.application.orchestration.stage_contributions import (
    MAX_ANALYSIS_REFS,
    MAX_ASSESSMENTS,
    MAX_CLAIM_LINKS,
    MAX_CONTRADICTIONS,
    MAX_EVENTS,
    MAX_EVIDENCE,
    MAX_LIMITATIONS,
    MAX_LIMITATION_LENGTH,
    MAX_RAW_RECORDS,
)

MAX_PARTIAL_REASONS = 50
MAX_PARTIAL_REASON_LENGTH = 64


class PipelineOverflowError(RuntimeError):
    """A bounded collection exceeded its hard maximum."""

    def __init__(self, collection: str, maximum: int) -> None:
        self.collection = collection
        self.maximum = maximum
        super().__init__(f"pipeline {collection} exceeded maximum {maximum}")


class PipelineDuplicateError(RuntimeError):
    """A canonical output ID was supplied more than once."""

    def __init__(self, collection: str, identifier: str) -> None:
        self.collection = collection
        self.identifier = identifier
        super().__init__(f"duplicate pipeline {collection} identifier")


@dataclass(frozen=True, slots=True)
class PipelineSnapshot:
    """Immutable bounded input for a later stage or artifact assembler."""

    task_id: str
    execution_id: str
    question: str
    assets: tuple[str, ...]
    raw_record_ids: tuple[str, ...]
    evidence: tuple[EvidenceDTO, ...]
    claim_links: tuple[EvidenceClaimLinkDTO, ...]
    assessments: tuple[EvidenceAssessmentDTO, ...]
    analysis_refs: tuple[AnalysisRefDTO, ...]
    reasoning_result: ReasoningResultDTO | None
    contradictions: tuple[ContradictionDTO, ...]
    events: tuple[ExecutionEventDTO, ...]
    limitations: tuple[str, ...]
    partial_reasons: tuple[str, ...]
    official_dataset_used: bool
    live_extension_used: bool


class FormalRunPipelineContext:
    """Thread-safe accumulator owned by one orchestrator execution."""

    def __init__(
        self,
        *,
        task_id: str,
        execution_id: str,
        question: str,
        assets: tuple[str, ...],
    ) -> None:
        if not task_id.startswith("TASK-"):
            raise ValueError("invalid task_id")
        if not execution_id.startswith("EXEC-"):
            raise ValueError("invalid execution_id")
        if not isinstance(question, str) or not 1 <= len(question) <= 2_000:
            raise ValueError("invalid question")
        if not assets or any(not isinstance(item, str) or not 1 <= len(item) <= 32 for item in assets):
            raise ValueError("invalid assets")

        self._task_id = task_id
        self._execution_id = execution_id
        self._question = question
        self._assets = tuple(assets)
        self._raw_record_ids: list[str] = []
        self._evidence: list[EvidenceDTO] = []
        self._claim_links: list[EvidenceClaimLinkDTO] = []
        self._assessments: list[EvidenceAssessmentDTO] = []
        self._analysis_refs: list[AnalysisRefDTO] = []
        self._reasoning_result: ReasoningResultDTO | None = None
        self._contradictions: list[ContradictionDTO] = []
        self._events: list[ExecutionEventDTO] = []
        self._limitations: list[str] = []
        self._partial_reasons: list[str] = []
        self._official_dataset_used = True
        self._live_extension_used = False
        self._lock = RLock()

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def execution_id(self) -> str:
        return self._execution_id

    @property
    def question(self) -> str:
        return self._question

    @property
    def assets(self) -> tuple[str, ...]:
        return self._assets

    @staticmethod
    def _ensure_capacity(collection: str, current: int, incoming: int, maximum: int) -> None:
        if current + incoming > maximum:
            raise PipelineOverflowError(collection, maximum)

    @staticmethod
    def _ensure_unique(
        collection: str,
        existing: set[str],
        incoming: tuple[str, ...],
    ) -> None:
        seen: set[str] = set()
        for identifier in incoming:
            if identifier in existing or identifier in seen:
                raise PipelineDuplicateError(collection, identifier)
            seen.add(identifier)

    def append_raw_record_ids(self, items: Sequence[str]) -> None:
        values = tuple(items)
        with self._lock:
            self._ensure_capacity("raw_record_ids", len(self._raw_record_ids), len(values), MAX_RAW_RECORDS)
            self._ensure_unique("raw_record_ids", set(self._raw_record_ids), values)
            self._raw_record_ids.extend(values)

    def append_evidence(self, item: EvidenceDTO) -> None:
        with self._lock:
            self._ensure_capacity("evidence", len(self._evidence), 1, MAX_EVIDENCE)
            self._ensure_unique(
                "evidence", {value.evidence_id for value in self._evidence}, (item.evidence_id,)
            )
            self._evidence.append(item)

    def append_claim_links(self, items: Sequence[EvidenceClaimLinkDTO]) -> None:
        values = tuple(items)
        with self._lock:
            self._ensure_capacity("claim_links", len(self._claim_links), len(values), MAX_CLAIM_LINKS)
            self._ensure_unique(
                "claim_links",
                {value.link_id for value in self._claim_links},
                tuple(value.link_id for value in values),
            )
            self._claim_links.extend(values)

    def append_assessments(self, items: Sequence[EvidenceAssessmentDTO]) -> None:
        values = tuple(items)
        with self._lock:
            self._ensure_capacity("assessments", len(self._assessments), len(values), MAX_ASSESSMENTS)
            self._ensure_unique(
                "assessments",
                {value.assessment_id for value in self._assessments},
                tuple(value.assessment_id for value in values),
            )
            self._assessments.extend(values)

    def append_analysis(self, item: AnalysisRefDTO) -> None:
        with self._lock:
            self._ensure_capacity("analysis_refs", len(self._analysis_refs), 1, MAX_ANALYSIS_REFS)
            self._ensure_unique(
                "analysis_refs",
                {value.analysis_id for value in self._analysis_refs},
                (item.analysis_id,),
            )
            self._analysis_refs.append(item)

    def set_reasoning_result(self, result: ReasoningResultDTO) -> None:
        with self._lock:
            if self._reasoning_result is not None:
                raise PipelineDuplicateError("reasoning_result", "reasoning_result")
            self._reasoning_result = result

    def append_contradiction(self, item: ContradictionDTO) -> None:
        with self._lock:
            self._ensure_capacity("contradictions", len(self._contradictions), 1, MAX_CONTRADICTIONS)
            self._ensure_unique(
                "contradictions",
                {value.contradiction_id for value in self._contradictions},
                (item.contradiction_id,),
            )
            self._contradictions.append(item)

    def append_event(self, event: ExecutionEventDTO) -> None:
        with self._lock:
            self._ensure_capacity("events", len(self._events), 1, MAX_EVENTS)
            self._ensure_unique("events", {value.event_id for value in self._events}, (event.event_id,))
            self._events.append(event)

    def append_limitation(self, text: str) -> None:
        if not isinstance(text, str) or not 1 <= len(text) <= MAX_LIMITATION_LENGTH:
            raise ValueError("invalid limitation")
        with self._lock:
            self._ensure_capacity("limitations", len(self._limitations), 1, MAX_LIMITATIONS)
            if text in self._limitations:
                raise PipelineDuplicateError("limitations", text)
            self._limitations.append(text)

    def append_partial_reason(self, reason: str) -> None:
        if not isinstance(reason, str) or not 1 <= len(reason) <= MAX_PARTIAL_REASON_LENGTH:
            raise ValueError("invalid partial reason")
        with self._lock:
            self._ensure_capacity(
                "partial_reasons", len(self._partial_reasons), 1, MAX_PARTIAL_REASONS
            )
            if reason in self._partial_reasons:
                raise PipelineDuplicateError("partial_reasons", reason)
            self._partial_reasons.append(reason)

    def set_official_dataset_used(self, value: bool) -> None:
        if type(value) is not bool:
            raise ValueError("official_dataset_used must be boolean")
        with self._lock:
            self._official_dataset_used = value

    def set_live_extension_used(self, value: bool) -> None:
        if type(value) is not bool:
            raise ValueError("live_extension_used must be boolean")
        with self._lock:
            self._live_extension_used = value

    def snapshot(self) -> PipelineSnapshot:
        with self._lock:
            return PipelineSnapshot(
                task_id=self._task_id,
                execution_id=self._execution_id,
                question=self._question,
                assets=self._assets,
                raw_record_ids=tuple(self._raw_record_ids),
                evidence=tuple(self._evidence),
                claim_links=tuple(self._claim_links),
                assessments=tuple(self._assessments),
                analysis_refs=tuple(self._analysis_refs),
                reasoning_result=self._reasoning_result,
                contradictions=tuple(self._contradictions),
                events=tuple(self._events),
                limitations=tuple(self._limitations),
                partial_reasons=tuple(self._partial_reasons),
                official_dataset_used=self._official_dataset_used,
                live_extension_used=self._live_extension_used,
            )


__all__ = (
    "FormalRunPipelineContext",
    "MAX_ANALYSIS_REFS",
    "MAX_ASSESSMENTS",
    "MAX_CLAIM_LINKS",
    "MAX_CONTRADICTIONS",
    "MAX_EVENTS",
    "MAX_EVIDENCE",
    "MAX_LIMITATIONS",
    "MAX_PARTIAL_REASONS",
    "MAX_RAW_RECORDS",
    "PipelineDuplicateError",
    "PipelineOverflowError",
    "PipelineSnapshot",
)
