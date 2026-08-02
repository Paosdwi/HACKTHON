"""Core-internal strategy interfaces; these are not frozen external provider Ports."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from crypto_trust_agent.application.dto.evidence_strategies import (
    ConfidenceCompositionRequestDTO,
    ConfidenceCompositionResultDTO,
    ContradictionDetectionRequestDTO,
    ContradictionDetectionResultDTO,
    DuplicateDetectionRequestDTO,
    DuplicateDetectionResultDTO,
    IndependenceGroupingRequestDTO,
    IndependenceGroupingResultDTO,
    TrustScoringRequestDTO,
    TrustScoringResultDTO,
)


@runtime_checkable
class DuplicateDetectionStrategy(Protocol):
    def detect(self, request: DuplicateDetectionRequestDTO) -> DuplicateDetectionResultDTO: ...


@runtime_checkable
class IndependenceGroupingStrategy(Protocol):
    def group(self, request: IndependenceGroupingRequestDTO) -> IndependenceGroupingResultDTO: ...


@runtime_checkable
class TrustComponentStrategy(Protocol):
    def score(self, request: TrustScoringRequestDTO) -> TrustScoringResultDTO: ...


@runtime_checkable
class ContradictionDetectionStrategy(Protocol):
    def detect(self, request: ContradictionDetectionRequestDTO) -> ContradictionDetectionResultDTO: ...


@runtime_checkable
class ConfidenceCompositionStrategy(Protocol):
    def compose(self, request: ConfidenceCompositionRequestDTO) -> ConfidenceCompositionResultDTO: ...


__all__ = (
    "ConfidenceCompositionStrategy",
    "ContradictionDetectionStrategy",
    "DuplicateDetectionStrategy",
    "IndependenceGroupingStrategy",
    "TrustComponentStrategy",
)
