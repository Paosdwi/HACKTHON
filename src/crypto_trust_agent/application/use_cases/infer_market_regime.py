"""Market regime inference orchestration with Core-owned deterministic fallback."""

from __future__ import annotations

from dataclasses import dataclass

from crypto_trust_agent.application.dto.common import ErrorResultDTO
from crypto_trust_agent.application.dto.market_regime import InferRequestDTO
from crypto_trust_agent.application.dto.repositories import ClockReadRequestDTO
from crypto_trust_agent.application.ports import Clock
from crypto_trust_agent.application.ports.market_regime import MarketRegimeProvider
from crypto_trust_agent.domain.evidence import (
    AnalysisProducer,
    AnalysisQuality,
    AnalysisResult,
)


@dataclass(frozen=True, slots=True)
class InferMarketRegimeCommand:
    analysis_id: str
    request: InferRequestDTO
    source_refs: tuple[str, ...]
    deterministic_regime: str
    formula_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_refs", tuple(self.source_refs))
        if self.deterministic_regime not in {"bullish", "bearish", "sideways"}:
            raise ValueError("invalid deterministic regime")
        if not self.source_refs or not self.formula_version:
            raise ValueError("fallback lineage/version are required")


class InferMarketRegimeUseCase:
    def __init__(self, provider: MarketRegimeProvider, clock: Clock) -> None:
        self._provider = provider
        self._clock = clock

    def execute(self, command: InferMarketRegimeCommand) -> AnalysisResult:
        result = self._provider.infer(command.request)
        now_result = self._clock.now_utc(ClockReadRequestDTO("OP-CLOCK-MARKET-RESULT"))
        if isinstance(now_result, ErrorResultDTO):
            raise RuntimeError(now_result.error.code)
        now = now_result.utc
        if isinstance(result, ErrorResultDTO):
            values = {
                "bullish_probability": "1" if command.deterministic_regime == "bullish" else "0",
                "bearish_probability": "1" if command.deterministic_regime == "bearish" else "0",
                "sideways_probability": "1" if command.deterministic_regime == "sideways" else "0",
                "anomaly_score": "0",
            }
            quality = AnalysisQuality(
                "fallback", ("market_regime_provider_absent", result.error.code)
            )
            producer = AnalysisProducer(
                "deterministic_fallback",
                "core-historical-market-fallback",
                "1.0.0",
                command.formula_version,
            )
        else:
            values = {
                "bullish_probability": result.probabilities.bullish,
                "bearish_probability": result.probabilities.bearish,
                "sideways_probability": result.probabilities.sideways,
                "anomaly_score": result.anomaly_score,
            }
            quality = AnalysisQuality(
                "valid" if result.quality == "valid" else "degraded",
                result.limitations,
            )
            producer = AnalysisProducer(
                "model",
                result.model.name,
                result.model.version,
                command.request.expected_model.contract_version,
            )
        return AnalysisResult(
            command.analysis_id,
            command.request.task_id,
            command.request.execution_id,
            "market_regime",
            command.request.asset,
            command.request.as_of,
            (command.request.input_feature_hash,),
            command.source_refs,
            values,
            quality,
            producer,
            now,
        )


__all__ = (
    "InferMarketRegimeCommand",
    "InferMarketRegimeUseCase",
)
