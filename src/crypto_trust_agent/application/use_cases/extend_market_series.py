"""Core-owned official/live market merge and reconciliation policy."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.live_market import LiveMarketDataRequestDTO
from crypto_trust_agent.application.ports.live_market import LiveMarketDataProvider
from crypto_trust_agent.domain.market import ExtendedMarketSeries, MarketBar, MarketSeries

RECONCILIATION_RULESET_VERSION = "live-market-reconciliation-1.0.0"
OVERLAP_DAYS = 3
PRICE_RELATIVE_TOLERANCE = Decimal("0.01")
VOLUME_RELATIVE_TOLERANCE = Decimal("0.25")


def _within_tolerance(reference: Decimal, candidate: Decimal, tolerance: Decimal) -> bool:
    if reference == 0:
        return candidate == 0
    return abs(candidate - reference) / abs(reference) <= tolerance


class ExtendMarketSeries:
    """Use live data only after a verified overlap; never overwrite official rows."""

    def __init__(self, provider: LiveMarketDataProvider) -> None:
        self._provider = provider

    def execute(
        self,
        official: MarketSeries,
        *,
        reporting_end: date,
        operation_id: str,
        as_of: str,
        deadline: DeadlineDTO,
    ) -> ExtendedMarketSeries:
        official_end = official.bars[-1].day
        if reporting_end <= official_end:
            return ExtendedMarketSeries(official.bars, None, False, ())
        overlap_start = official_end - timedelta(days=OVERLAP_DAYS - 1)
        request = LiveMarketDataRequestDTO(
            operation_id=operation_id,
            asset=official.asset,
            pair=official.pair,
            start_date=overlap_start,
            end_date=reporting_end,
            as_of=as_of,
            deadline=deadline,
        )
        result = self._provider.fetch_daily_ohlcv(request)
        if isinstance(result, ErrorResultDTO):
            return ExtendedMarketSeries(
                official.bars, None, True, (f"live_extension_{result.error.code}",),
            )
        official_overlap = {bar.day: bar for bar in official.bars if bar.day >= overlap_start}
        live_overlap = {bar.day: bar for bar in result.bars if bar.day <= official_end}
        if set(live_overlap) != set(official_overlap):
            return ExtendedMarketSeries(official.bars, None, True, ("live_extension_reconciliation_failed",))
        for day, reference in official_overlap.items():
            candidate = live_overlap[day]
            price_matches = all(
                _within_tolerance(getattr(reference, field), getattr(candidate, field).as_decimal(), PRICE_RELATIVE_TOLERANCE)
                for field in ("open", "high", "low", "close")
            )
            volume_matches = _within_tolerance(reference.volume, candidate.volume.as_decimal(), VOLUME_RELATIVE_TOLERANCE)
            if not price_matches or not volume_matches:
                return ExtendedMarketSeries(official.bars, None, True, ("live_extension_reconciliation_failed",))
        live_bars = tuple(
            MarketBar(
                bar.asset,
                bar.pair,
                bar.day,
                bar.open.as_decimal(),
                bar.high.as_decimal(),
                bar.low.as_decimal(),
                bar.close.as_decimal(),
                bar.volume.as_decimal(),
                "live_extension",
                f"binance-live:{bar.day.isoformat()}:{bar.content_hash}:{bar.source_url}",
            )
            for bar in result.bars
            if official_end < bar.day <= reporting_end
        )
        limitations: list[str] = []
        if result.missing_dates:
            limitations.append("live_extension_gap")
        if result.incomplete_dates:
            limitations.append("live_extension_incomplete_candle")
        expected = official_end + timedelta(days=1)
        for bar in live_bars:
            if bar.day != expected:
                limitations.append("live_extension_gap")
            expected = bar.day + timedelta(days=1)
        if not live_bars or live_bars[-1].day < reporting_end:
            limitations.append("live_extension_gap")
        return ExtendedMarketSeries(
            (*official.bars, *live_bars),
            official_end if live_bars else None,
            bool(limitations),
            tuple(dict.fromkeys(limitations)),
        )


__all__ = (
    "ExtendMarketSeries", "OVERLAP_DAYS", "PRICE_RELATIVE_TOLERANCE",
    "RECONCILIATION_RULESET_VERSION", "VOLUME_RELATIVE_TOLERANCE",
)
