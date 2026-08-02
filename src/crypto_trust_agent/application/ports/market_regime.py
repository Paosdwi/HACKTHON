"""Application-owned frozen MarketRegimeProvider Port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from crypto_trust_agent.application.dto.common import ErrorResultDTO
from crypto_trust_agent.application.dto.market_regime import (
    HealthCheckRequestDTO,
    InferRequestDTO,
    MarketRegimeResultDTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO


@runtime_checkable
class MarketRegimeProvider(Protocol):
    def infer(self, request: InferRequestDTO) -> MarketRegimeResultDTO | ErrorResultDTO: ...

    def health_check(self, request: HealthCheckRequestDTO) -> ProviderHealthDTO | ErrorResultDTO: ...


__all__ = ("MarketRegimeProvider",)
