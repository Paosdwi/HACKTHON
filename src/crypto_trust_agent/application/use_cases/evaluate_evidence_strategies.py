"""Application orchestration for injected evidence strategy boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from crypto_trust_agent.application.dto.evidence_strategies import (
    ConfidenceCompositionRequestDTO,
    ConfidenceCompositionResultDTO,
    ContradictionCandidateDTO,
    ContradictionDetectionRequestDTO,
    ContradictionDetectionResultDTO,
    DuplicateDetectionRequestDTO,
    DuplicateDetectionResultDTO,
    EvidenceStrategyItemDTO,
    IndependenceGroupingRequestDTO,
    IndependenceGroupingResultDTO,
    TrustScoringRequestDTO,
    TrustScoringResultDTO,
)
from crypto_trust_agent.application.ports.evidence_strategies import (
    ConfidenceCompositionStrategy,
    ContradictionDetectionStrategy,
    DuplicateDetectionStrategy,
    IndependenceGroupingStrategy,
    TrustComponentStrategy,
)


@dataclass(frozen=True, slots=True)
class EvaluateEvidenceStrategiesCommand:
    task_id: str
    ruleset_version: str
    items: tuple[EvidenceStrategyItemDTO, ...]
    contradiction_candidates: tuple[ContradictionCandidateDTO, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "contradiction_candidates", tuple(self.contradiction_candidates))


@dataclass(frozen=True, slots=True)
class EvaluateEvidenceStrategiesResult:
    duplicates: DuplicateDetectionResultDTO
    independence: IndependenceGroupingResultDTO
    trust: TrustScoringResultDTO
    contradictions: ContradictionDetectionResultDTO
    confidence: ConfidenceCompositionResultDTO


class EvaluateEvidenceStrategiesUseCase:
    def __init__(
        self,
        duplicate_detection: DuplicateDetectionStrategy,
        independence_grouping: IndependenceGroupingStrategy,
        trust_scoring: TrustComponentStrategy,
        contradiction_detection: ContradictionDetectionStrategy,
        confidence_composition: ConfidenceCompositionStrategy,
    ) -> None:
        self._duplicate_detection = duplicate_detection
        self._independence_grouping = independence_grouping
        self._trust_scoring = trust_scoring
        self._contradiction_detection = contradiction_detection
        self._confidence_composition = confidence_composition

    def execute(self, command: EvaluateEvidenceStrategiesCommand) -> EvaluateEvidenceStrategiesResult:
        duplicates = self._duplicate_detection.detect(
            DuplicateDetectionRequestDTO(command.task_id, command.ruleset_version, command.items)
        )
        independence = self._independence_grouping.group(
            IndependenceGroupingRequestDTO(
                command.task_id,
                command.ruleset_version,
                command.items,
                duplicates.groups,
            )
        )
        trust = self._trust_scoring.score(
            TrustScoringRequestDTO(
                command.task_id,
                command.ruleset_version,
                command.items,
                independence.groups,
            )
        )
        contradictions = self._contradiction_detection.detect(
            ContradictionDetectionRequestDTO(
                command.task_id,
                command.ruleset_version,
                command.contradiction_candidates,
            )
        )
        confidence = self._confidence_composition.compose(
            ConfidenceCompositionRequestDTO(
                command.task_id,
                command.ruleset_version,
                trust.items,
                tuple(item.contradiction_id for item in contradictions.items),
            )
        )
        return EvaluateEvidenceStrategiesResult(
            duplicates, independence, trust, contradictions, confidence
        )


__all__ = (
    "EvaluateEvidenceStrategiesCommand",
    "EvaluateEvidenceStrategiesResult",
    "EvaluateEvidenceStrategiesUseCase",
)
