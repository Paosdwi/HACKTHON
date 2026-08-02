"""Explicit opt-in public Binance integration; skipped in normal CI."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.live_market import LiveMarketDataRequestDTO, LiveMarketHealthRequestDTO
from crypto_trust_agent.infrastructure.collectors.binance_live_market import BinanceLiveMarketDataProvider, PinnedBinanceTransport

LIVE = os.getenv("LIVE_MARKET_LIVE_INTEGRATION") == "1"


@unittest.skipUnless(LIVE, "live market integration requires LIVE_MARKET_LIVE_INTEGRATION=1")
class LiveBinanceIntegrationTests(unittest.TestCase):
    def test_health_and_last_closed_daily_bar(self) -> None:
        now = datetime.now(UTC)
        provider = BinanceLiveMarketDataProvider(PinnedBinanceTransport(), now_utc=lambda: now)
        health_id = "OP-LIVE-NETWORK-HEALTH"
        health_deadline = DeadlineDTO("1.0.0", health_id, (now + timedelta(seconds=10)).isoformat().replace("+00:00", "Z"), 10_000, now.isoformat().replace("+00:00", "Z"), 100)
        health = provider.health_check(LiveMarketHealthRequestDTO(health_id, health_deadline))
        self.assertNotIsInstance(health, ErrorResultDTO)
        target = now.date() - timedelta(days=1)
        fetch_id = "OP-LIVE-NETWORK-FETCH"
        fetch_deadline = DeadlineDTO("1.0.0", fetch_id, (now + timedelta(seconds=30)).isoformat().replace("+00:00", "Z"), 30_000, now.isoformat().replace("+00:00", "Z"), 100)
        result = provider.fetch_daily_ohlcv(LiveMarketDataRequestDTO(fetch_id, "BTC", "BTCUSDT", target, target, now.isoformat().replace("+00:00", "Z"), fetch_deadline))
        self.assertNotIsInstance(result, ErrorResultDTO)
        self.assertEqual(target, result.bars[0].day)
        self.assertTrue(result.complete)


if __name__ == "__main__":
    unittest.main()
