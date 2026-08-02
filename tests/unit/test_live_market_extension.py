from __future__ import annotations

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from crypto_trust_agent.application.dto.common import DeadlineDTO
from crypto_trust_agent.application.use_cases.extend_market_series import ExtendMarketSeries
from crypto_trust_agent.domain.market import MarketBar, MarketSeries
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock
from crypto_trust_agent.infrastructure.fakes.live_market import FakeLiveMarketDataProvider


class LiveMarketExtensionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock("2026-08-02T12:00:00Z")
        self.provider = FakeLiveMarketDataProvider(self.clock)
        bars = tuple(
            MarketBar("BTC", "BTCUSDT", date(2026, 5, day), Decimal("100"), Decimal("105"),
                      Decimal("95"), Decimal("101"), Decimal("1234.5"), "official_dataset", f"official:{day}")
            for day in (29, 30, 31)
        )
        self.official = MarketSeries("BTC", "BTCUSDT", bars, "dataset_metadata.json")

    @staticmethod
    def deadline(operation_id: str) -> DeadlineDTO:
        return DeadlineDTO("1.0.0", operation_id, "2026-08-02T12:00:30Z", 30_000, "2026-08-02T12:00:00Z", 100)

    def test_verified_overlap_keeps_official_and_appends_live_with_transition(self) -> None:
        operation = "OP-LIVE-MERGE"
        result = ExtendMarketSeries(self.provider).execute(
            self.official, reporting_end=date(2026, 6, 2), operation_id=operation,
            as_of="2026-08-02T12:00:00Z", deadline=self.deadline(operation),
        )
        self.assertEqual(5, len(result.bars))
        self.assertEqual(date(2026, 5, 31), result.transition_date)
        self.assertFalse(result.partial)
        self.assertEqual("official_dataset", result.bars[2].provenance)
        self.assertEqual("live_extension", result.bars[3].provenance)
        self.assertIn("binance-live:2026-06-01:sha256:", result.bars[3].source_ref)

    def test_historical_only_range_does_not_call_provider(self) -> None:
        operation = "OP-LIVE-NOT-NEEDED"
        result = ExtendMarketSeries(self.provider).execute(
            self.official, reporting_end=date(2026, 5, 31), operation_id=operation,
            as_of="2026-08-02T12:00:00Z", deadline=self.deadline(operation),
        )
        self.assertFalse(result.partial)
        self.assertIsNone(result.transition_date)
        self.assertEqual(0, self.provider.invocation_count)

    def test_expired_live_call_is_partial_and_never_forward_fills(self) -> None:
        operation = "OP-LIVE-MERGE-EXPIRED"
        expired = DeadlineDTO("1.0.0", operation, "2026-08-02T12:00:00Z", 30_000, "2026-08-02T12:00:00Z", 100)
        result = ExtendMarketSeries(self.provider).execute(
            self.official, reporting_end=date(2026, 6, 2), operation_id=operation,
            as_of="2026-08-02T12:00:00Z", deadline=expired,
        )
        self.assertTrue(result.partial)
        self.assertEqual(3, len(result.bars))
        self.assertEqual(("live_extension_deadline_exceeded",), result.limitations)

    def test_reconciliation_failure_never_replaces_official_rows(self) -> None:
        provider = FakeLiveMarketDataProvider(self.clock)
        original = provider.fetch_daily_ohlcv

        def mismatched(request):
            result = original(request)
            first = result.bars[0]
            from dataclasses import replace
            return replace(result, bars=(replace(first, close="200", high="205"), *result.bars[1:]))

        provider.fetch_daily_ohlcv = mismatched
        operation = "OP-LIVE-RECONCILE-FAIL"
        result = ExtendMarketSeries(provider).execute(
            self.official, reporting_end=date(2026, 6, 2), operation_id=operation,
            as_of="2026-08-02T12:00:00Z", deadline=self.deadline(operation),
        )
        self.assertTrue(result.partial)
        self.assertEqual(self.official.bars, result.bars)
        self.assertEqual(("live_extension_reconciliation_failed",), result.limitations)


if __name__ == "__main__":
    unittest.main()
