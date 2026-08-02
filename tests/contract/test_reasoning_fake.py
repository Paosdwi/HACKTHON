from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.infrastructure.fakes.clock import FakeClock  # noqa: E402
from crypto_trust_agent.infrastructure.fakes.reasoning import FakeReasoningProvider  # noqa: E402
from tests.contract.shared_reasoning_assertions import ReasoningProviderContractAssertions  # noqa: E402


class FakeReasoningProviderContractTests(ReasoningProviderContractAssertions, unittest.TestCase):
    def make_provider(self) -> FakeReasoningProvider:
        return FakeReasoningProvider(FakeClock("2026-08-01T02:00:00Z"))

    def configure_generate(self, provider: FakeReasoningProvider, operation_id: str, response: object) -> None:
        provider.configure_generate(operation_id, response)

    def configure_repair(self, provider: FakeReasoningProvider, operation_id: str, response: object) -> None:
        provider.configure_repair(operation_id, response)

    def configure_health(self, provider: FakeReasoningProvider, operation_id: str, response: object) -> None:
        provider.configure_health(operation_id, response)


if __name__ == "__main__":
    unittest.main()
