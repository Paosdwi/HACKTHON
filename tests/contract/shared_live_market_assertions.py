from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.live_market import (
    LiveMarketCapabilitiesRequestDTO,
    LiveMarketDataRequestDTO,
    LiveMarketDataResultDTO,
    LiveMarketHealthRequestDTO,
)
from crypto_trust_agent.application.ports.live_market import LiveMarketDataProvider


def deadline(operation_id: str, at: str = "2026-08-02T12:00:30Z") -> DeadlineDTO:
    return DeadlineDTO("1.0.0", operation_id, at, 30_000, "2026-08-02T12:00:00Z", 100)


def request(operation_id: str = "OP-LIVE-MARKET-01") -> LiveMarketDataRequestDTO:
    return LiveMarketDataRequestDTO(
        operation_id=operation_id,
        asset="BTC",
        pair="BTCUSDT",
        start_date="2026-05-29",
        end_date="2026-06-03",
        as_of="2026-08-02T12:00:00Z",
        deadline=deadline(operation_id),
    )


class LiveMarketContractAssertions:
    def make_provider(self):
        raise NotImplementedError

    def test_contract_ids_and_method_policy(self) -> None:
        root = Path(__file__).resolve().parents[2]
        schema = json.loads((root / "docs/architecture/schemas/live_market_data_provider/contract.schema.json").read_text(encoding="utf-8"))
        self.assertEqual("1.0.0", schema["x-contract-version"])
        expected = {
            "FetchOperation": ("CT-LIVE-MARKET-FETCH-01", 30_000),
            "HealthOperation": ("CT-LIVE-MARKET-HEALTH-01", 3_000),
            "CapabilitiesOperation": ("CT-LIVE-MARKET-CAPABILITIES-01", 3_000),
        }
        for name, (test_id, timeout) in expected.items():
            operation = schema["$defs"][name]
            policy = operation["x-method-policy"]
            self.assertEqual(test_id, operation["x-contract-test-id"])
            self.assertEqual(timeout, policy["timeout_ms"])
            self.assertEqual("core", policy["retry_owner"])
            self.assertEqual(1, policy["max_attempts"])
            self.assertFalse(policy["hidden_adapter_retries"])

    def test_protocol_wire_shape_and_replay(self) -> None:
        provider = self.make_provider()
        self.assertIsInstance(provider, LiveMarketDataProvider)
        value = request()
        with self.assertRaises(FrozenInstanceError):
            value.asset = "ETH"
        first = provider.fetch_daily_ohlcv(value)
        self.assertIsInstance(first, LiveMarketDataResultDTO)
        self.assertIs(first, provider.fetch_daily_ohlcv(value))
        self.assertEqual(1, provider.invocation_count)
        conflict = provider.fetch_daily_ohlcv(replace(value, end_date="2026-06-04"))
        self.assertIsInstance(conflict, ErrorResultDTO)
        self.assertEqual("payload_conflict", conflict.error.code)
        self.assertEqual(1, provider.invocation_count)

    def test_deadline_health_and_capabilities(self) -> None:
        provider = self.make_provider()
        expired = request("OP-LIVE-EXPIRED")
        expired = replace(expired, deadline=deadline(expired.operation_id, "2026-08-02T12:00:00Z"))
        result = provider.fetch_daily_ohlcv(expired)
        self.assertEqual("deadline_exceeded", result.error.code)
        self.assertEqual(0, provider.invocation_count)

        health_id = "OP-LIVE-HEALTH"
        health = provider.health_check(LiveMarketHealthRequestDTO(health_id, deadline(health_id)))
        self.assertEqual("live_market_data", health.capability)
        cap_id = "OP-LIVE-CAP"
        capabilities = provider.capabilities(LiveMarketCapabilitiesRequestDTO(cap_id, deadline(cap_id)))
        self.assertEqual(("BTC", "ETH", "SOL", "BNB", "XRP"), capabilities.assets)
        self.assertEqual("none", capabilities.authentication)
