"""Application-owned frozen ReasoningProvider Port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from crypto_trust_agent.application.dto.common import ErrorResultDTO
from crypto_trust_agent.application.dto.reasoning import (
    GenerateRequestDTO,
    ReasoningHealthCheckRequestDTO,
    ReasoningResultDTO,
    RepairRequestDTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO


@runtime_checkable
class ReasoningProvider(Protocol):
    def generate(self, request: GenerateRequestDTO) -> ReasoningResultDTO | ErrorResultDTO: ...

    def repair(self, request: RepairRequestDTO) -> ReasoningResultDTO | ErrorResultDTO: ...

    def health_check(self, request: ReasoningHealthCheckRequestDTO) -> ProviderHealthDTO | ErrorResultDTO: ...


__all__ = ("ReasoningProvider",)
