from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.infrastructure.fakes.clock import FakeClock
from crypto_trust_agent.infrastructure.fakes.market_regime import FakeMarketRegimeProvider
from tests.contract.shared_market_regime_assertions import MarketRegimeContractAssertions


class FakeMarketRegimeProviderContractTests(MarketRegimeContractAssertions, unittest.TestCase):
    def make_provider(self) -> FakeMarketRegimeProvider:
        return FakeMarketRegimeProvider(FakeClock("2026-08-01T02:00:00Z"))

    def configure_infer(
        self, provider: FakeMarketRegimeProvider, operation_id: str, response: object
    ) -> None:
        provider.configure_infer(operation_id, response)


if __name__ == "__main__":
    unittest.main()
