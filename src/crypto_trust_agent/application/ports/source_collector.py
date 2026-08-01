"""Application-owned SourceCollector Port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from crypto_trust_agent.application.dto.common import ErrorResultDTO
from crypto_trust_agent.application.dto.source_collector import (
    CapabilitiesRequestDTO,
    CollectionResultDTO,
    CollectRequestDTO,
    CollectorCapabilitiesDTO,
    CollectorHealthCheckRequestDTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO


@runtime_checkable
class SourceCollector(Protocol):
    def collect(self, request: CollectRequestDTO) -> CollectionResultDTO | ErrorResultDTO: ...

    def health_check(self, request: CollectorHealthCheckRequestDTO) -> ProviderHealthDTO | ErrorResultDTO: ...

    def capabilities(self, request: CapabilitiesRequestDTO) -> CollectorCapabilitiesDTO | ErrorResultDTO: ...


__all__ = ("SourceCollector",)
