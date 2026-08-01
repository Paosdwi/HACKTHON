"""Application-owned EvidenceExtractor Port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from crypto_trust_agent.application.dto.common import ErrorResultDTO
from crypto_trust_agent.application.dto.evidence_extractor import (
    ExtractRequestDTO,
    ExtractionResultDTO,
    ExtractorHealthCheckRequestDTO,
    RepairRequestDTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO


@runtime_checkable
class EvidenceExtractor(Protocol):
    def extract(self, request: ExtractRequestDTO) -> ExtractionResultDTO | ErrorResultDTO: ...

    def repair(self, request: RepairRequestDTO) -> ExtractionResultDTO | ErrorResultDTO: ...

    def health_check(self, request: ExtractorHealthCheckRequestDTO) -> ProviderHealthDTO | ErrorResultDTO: ...


__all__ = ("EvidenceExtractor",)
