"""Application-owned LiveMarketDataProvider Port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from crypto_trust_agent.application.dto.common import ErrorResultDTO
from crypto_trust_agent.application.dto.live_market import (
    LiveMarketCapabilitiesDTO,
    LiveMarketCapabilitiesRequestDTO,
    LiveMarketDataRequestDTO,
    LiveMarketDataResultDTO,
    LiveMarketHealthRequestDTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO


@runtime_checkable
class LiveMarketDataProvider(Protocol):
    def fetch_daily_ohlcv(self, request: LiveMarketDataRequestDTO) -> LiveMarketDataResultDTO | ErrorResultDTO: ...
    def health_check(self, request: LiveMarketHealthRequestDTO) -> ProviderHealthDTO | ErrorResultDTO: ...
    def capabilities(self, request: LiveMarketCapabilitiesRequestDTO) -> LiveMarketCapabilitiesDTO | ErrorResultDTO: ...


__all__ = ("LiveMarketDataProvider",)
