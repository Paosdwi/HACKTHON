from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from crypto_trust_agent.infrastructure.fakes.clock import FakeClock
from crypto_trust_agent.infrastructure.fakes.live_market import FakeLiveMarketDataProvider
from tests.contract.shared_live_market_assertions import LiveMarketContractAssertions


class FakeLiveMarketContractTests(LiveMarketContractAssertions, unittest.TestCase):
    def make_provider(self) -> FakeLiveMarketDataProvider:
        return FakeLiveMarketDataProvider(FakeClock("2026-08-02T12:00:00Z"))


if __name__ == "__main__":
    unittest.main()
