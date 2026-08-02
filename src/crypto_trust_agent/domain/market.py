"""Deterministic historical market data models and formulas."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from types import MappingProxyType
from typing import Mapping

from crypto_trust_agent.domain.primitives import CanonicalDecimal

FORMULA_VERSION = "market-formulas-1.0.0"
_SUPPORTED_ASSETS = {"BTC", "ETH", "SOL", "BNB", "XRP"}
_QUANTUM = Decimal("0.000000000000000001")


def _decimal(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"invalid {name}")
    return value


def _canonical(value: Decimal) -> CanonicalDecimal:
    quantized = value.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)
    if quantized == 0:
        return CanonicalDecimal("0")
    rendered = format(quantized, "f").rstrip("0").rstrip(".")
    return CanonicalDecimal(rendered)


@dataclass(frozen=True, slots=True)
class MarketBar:
    asset: str
    pair: str
    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    provenance: str
    source_ref: str

    def __post_init__(self) -> None:
        if self.asset not in _SUPPORTED_ASSETS or self.pair != f"{self.asset}USDT":
            raise ValueError("invalid asset/pair")
        if not isinstance(self.day, date):
            raise ValueError("invalid UTC date")
        for name in ("open", "high", "low", "close", "volume"):
            _decimal(getattr(self, name), name)
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("invalid OHLC range")
        if self.high < self.low:
            raise ValueError("high must not be below low")
        if self.provenance not in {"official_dataset", "live_extension"}:
            raise ValueError("invalid market provenance")
        if not isinstance(self.source_ref, str) or not self.source_ref:
            raise ValueError("source_ref is required")

    @property
    def interval_start_utc(self) -> str:
        return f"{self.day.isoformat()}T00:00:00Z"


@dataclass(frozen=True, slots=True)
class MarketSeries:
    asset: str
    pair: str
    bars: tuple[MarketBar, ...]
    metadata_file: str

    def __post_init__(self) -> None:
        bars = tuple(self.bars)
        if not bars or any(bar.asset != self.asset or bar.pair != self.pair for bar in bars):
            raise ValueError("market series lineage mismatch")
        days = tuple(bar.day for bar in bars)
        if days != tuple(sorted(days)) or len(days) != len(set(days)):
            raise ValueError("market series dates must be unique and ordered")
        object.__setattr__(self, "bars", bars)


@dataclass(frozen=True, slots=True)
class OfficialMarketDataset:
    dataset_name: str
    source_name: str
    source_type: str
    time_basis: str
    interval: str
    price_unit: str
    period_start: date
    period_end: date
    series: tuple[MarketSeries, ...]
    lineage_label: str = "hackathon-provided official dataset"

    def __post_init__(self) -> None:
        series = tuple(self.series)
        if (
            not self.dataset_name
            or self.source_name != "public_market_data"
            or self.source_type != "public_market_data"
            or self.time_basis != "UTC"
            or self.interval != "1d"
            or self.price_unit != "USDT"
            or self.period_start > self.period_end
            or not series
            or len({item.asset for item in series}) != len(series)
        ):
            raise ValueError("invalid official dataset metadata")
        object.__setattr__(self, "series", series)


@dataclass(frozen=True, slots=True)
class HistoricalMarketAnalysis:
    asset: str
    formula_version: str
    warmup_start: date
    reporting_start: date
    reporting_end: date
    values: Mapping[str, CanonicalDecimal]
    trend: str
    regime: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.formula_version != FORMULA_VERSION:
            raise ValueError("unsupported market formula version")
        if not self.warmup_start <= self.reporting_start <= self.reporting_end:
            raise ValueError("invalid analysis ranges")
        if self.trend not in {"up", "down", "flat"} or self.regime not in {"bullish", "bearish", "sideways"}:
            raise ValueError("invalid deterministic market classification")
        values = dict(self.values)
        required = {"return_period", "high", "low", "volume_change", "volatility", "max_drawdown", "sma"}
        if set(values) != required or not all(isinstance(value, CanonicalDecimal) for value in values.values()):
            raise ValueError("incomplete historical analysis values")
        object.__setattr__(self, "values", MappingProxyType(values))
        object.__setattr__(self, "source_refs", tuple(self.source_refs))


@dataclass(frozen=True, slots=True)
class ExtendedMarketSeries:
    bars: tuple[MarketBar, ...]
    transition_date: date | None
    partial: bool
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "bars", tuple(self.bars))
        object.__setattr__(self, "limitations", tuple(self.limitations))


def analyze_historical_market(
    series: MarketSeries,
    *,
    warmup_start: date,
    reporting_start: date,
    reporting_end: date,
) -> HistoricalMarketAnalysis:
    if not warmup_start <= reporting_start <= reporting_end:
        raise ValueError("invalid warm-up/reporting range")
    warmup = tuple(bar for bar in series.bars if warmup_start <= bar.day <= reporting_end)
    reporting = tuple(bar for bar in warmup if reporting_start <= bar.day <= reporting_end)
    if not warmup or warmup[0].day != warmup_start or len(reporting) < 2:
        raise ValueError("insufficient market warm-up/reporting coverage")
    if reporting[0].day != reporting_start or reporting[-1].day != reporting_end:
        raise ValueError("reporting range is not fully covered")

    first, last = reporting[0], reporting[-1]
    period_return = last.close / first.close - Decimal("1")
    volume_change = last.volume / first.volume - Decimal("1") if first.volume != 0 else Decimal("0")
    returns = tuple(
        current.close / previous.close - Decimal("1")
        for previous, current in zip(reporting, reporting[1:])
    )
    mean_return = sum(returns, Decimal("0")) / Decimal(len(returns))
    variance = sum(((value - mean_return) ** 2 for value in returns), Decimal("0")) / Decimal(len(returns))
    volatility = variance.sqrt()
    peak = reporting[0].close
    max_drawdown = Decimal("0")
    for bar in reporting:
        peak = max(peak, bar.close)
        if peak != 0:
            max_drawdown = max(max_drawdown, (peak - bar.close) / peak)
    sma = sum((bar.close for bar in reporting), Decimal("0")) / Decimal(len(reporting))
    trend = "up" if last.close > first.close else "down" if last.close < first.close else "flat"
    regime = "bullish" if last.close > sma else "bearish" if last.close < sma else "sideways"
    values = {
        "return_period": _canonical(period_return),
        "high": _canonical(max(bar.high for bar in reporting)),
        "low": _canonical(min(bar.low for bar in reporting)),
        "volume_change": _canonical(volume_change),
        "volatility": _canonical(volatility),
        "max_drawdown": _canonical(max_drawdown),
        "sma": _canonical(sma),
    }
    return HistoricalMarketAnalysis(
        series.asset,
        FORMULA_VERSION,
        warmup_start,
        reporting_start,
        reporting_end,
        values,
        trend,
        regime,
        tuple(bar.source_ref for bar in reporting),
    )


__all__ = (
    "ExtendedMarketSeries",
    "FORMULA_VERSION",
    "HistoricalMarketAnalysis",
    "MarketBar",
    "MarketSeries",
    "OfficialMarketDataset",
    "analyze_historical_market",
)
