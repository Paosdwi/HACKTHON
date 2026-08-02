from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from crypto_trust_agent.application.dto.common import ErrorResultDTO
from crypto_trust_agent.infrastructure.collectors.binance_live_market import (
    BinanceLiveMarketDataProvider,
    StubBinanceTransport,
)
from tests.contract.shared_live_market_assertions import LiveMarketContractAssertions, request


def row(day_open_ms: int, close_ms: int, *, close: str = "101") -> list[object]:
    return [day_open_ms, "100.00000000", "105.00000000", "95.00000000", close,
            "1234.50000000", close_ms, "0", 10, "0", "0", "0"]


class BinanceSharedContractTests(LiveMarketContractAssertions, unittest.TestCase):
    def make_provider(self) -> BinanceLiveMarketDataProvider:
        transport = StubBinanceTransport()
        first = int(datetime(2026, 5, 29, tzinfo=UTC).timestamp() * 1000)
        transport.default_payload = json.dumps([
            row(first + index * 86_400_000, first + (index + 1) * 86_400_000 - 1)
            for index in range(6)
        ]).encode()
        return BinanceLiveMarketDataProvider(
            transport,
            now_utc=lambda: datetime(2026, 8, 2, 12, tzinfo=UTC),
            monotonic_ms=lambda: 100_000,
        )


class BinanceBoundaryTests(unittest.TestCase):
    class RecordingEvents:
        def __init__(self) -> None:
            self.items: list[dict[str, object]] = []

        def emit(self, event) -> None:
            self.items.append(dict(event))

    def test_maps_decimal_strings_utc_days_and_source_lineage(self) -> None:
        transport = StubBinanceTransport()
        transport.default_payload = json.dumps([row(1780185600000, 1780271999999)]).encode()
        provider = BinanceLiveMarketDataProvider(transport, now_utc=lambda: datetime(2026, 8, 2, 12, tzinfo=UTC), monotonic_ms=lambda: 100_000)
        value = request()
        value = type(value)(value.operation_id, value.asset, value.pair, "2026-05-31", "2026-05-31", value.as_of, value.deadline)
        result = provider.fetch_daily_ohlcv(value)
        self.assertEqual("100", str(result.bars[0].open))
        self.assertEqual("1234.5", str(result.bars[0].volume))
        self.assertEqual("2026-05-31", result.bars[0].day.isoformat())
        self.assertEqual("live_extension", result.bars[0].provenance)
        self.assertTrue(result.bars[0].source_url.startswith("https://api.binance.com/api/v3/klines"))
        self.assertNotIn("float", repr(result.to_wire()).lower())

    def test_rejects_bad_shape_timeout_rate_limit_and_unknown_without_leak(self) -> None:
        cases = (
            (b'{"secret":"x"}', "invalid_provider_schema"),
            (TimeoutError("Authorization Bearer secret"), "provider_timeout"),
            ("rate_limit", "provider_rate_limited"),
            (RuntimeError("Authorization Bearer secret"), "unexpected_provider_error"),
        )
        for index, (configured, expected) in enumerate(cases):
            with self.subTest(expected=expected):
                transport = StubBinanceTransport()
                transport.configure(configured)
                provider = BinanceLiveMarketDataProvider(transport, now_utc=lambda: datetime(2026, 8, 2, 12, tzinfo=UTC), monotonic_ms=lambda: 100_000)
                value = request(f"OP-LIVE-FAIL-{index}")
                result = provider.fetch_daily_ohlcv(value)
                self.assertIsInstance(result, ErrorResultDTO)
                self.assertEqual(expected, result.error.code)
                self.assertNotIn("secret", repr(result.to_wire()).lower())

    def test_excludes_incomplete_candle_and_reports_gaps_without_fill(self) -> None:
        first = int(datetime(2026, 6, 1, tzinfo=UTC).timestamp() * 1000)
        transport = StubBinanceTransport()
        transport.default_payload = json.dumps([
            row(first, first + 86_400_000 - 1),
            row(first + 2 * 86_400_000, first + 3 * 86_400_000 - 1),
        ]).encode()
        provider = BinanceLiveMarketDataProvider(
            transport,
            now_utc=lambda: datetime(2026, 6, 3, 12, tzinfo=UTC),
            monotonic_ms=lambda: 100_000,
        )
        value = request("OP-LIVE-GAP")
        value = replace(value, start_date=datetime(2026, 6, 1, tzinfo=UTC).date(),
                        end_date=datetime(2026, 6, 3, tzinfo=UTC).date(),
                        as_of=type(value.as_of)("2026-06-03T12:00:00Z"))
        result = provider.fetch_daily_ohlcv(value)
        self.assertFalse(result.complete)
        self.assertEqual((datetime(2026, 6, 2, tzinfo=UTC).date(),), result.missing_dates)
        self.assertEqual((datetime(2026, 6, 3, tzinfo=UTC).date(),), result.incomplete_dates)
        self.assertEqual(1, len(result.bars))

    def test_rejects_duplicate_unordered_wrong_utc_and_invalid_ohlc(self) -> None:
        first = int(datetime(2026, 5, 29, tzinfo=UTC).timestamp() * 1000)
        cases = (
            [row(first, first + 86_400_000 - 1), row(first, first + 86_400_000 - 1)],
            [row(first + 86_400_000, first + 2 * 86_400_000 - 1), row(first, first + 86_400_000 - 1)],
            [row(first + 1, first + 86_400_000)],
            [[first, "100", "90", "95", "101", "1", first + 86_400_000 - 1, "0", 1, "0", "0", "0"]],
        )
        for index, payload in enumerate(cases):
            with self.subTest(index=index):
                transport = StubBinanceTransport()
                transport.default_payload = json.dumps(payload).encode()
                provider = BinanceLiveMarketDataProvider(transport, now_utc=lambda: datetime(2026, 8, 2, 12, tzinfo=UTC), monotonic_ms=lambda: 100_000)
                value = request(f"OP-LIVE-BAD-{index}")
                result = provider.fetch_daily_ohlcv(value)
                self.assertIsInstance(result, ErrorResultDTO)
                self.assertEqual("invalid_market_bar", result.error.code)

    def test_all_five_assets_map_to_fixed_usdt_symbols_and_one_attempt(self) -> None:
        first = int(datetime(2026, 5, 29, tzinfo=UTC).timestamp() * 1000)
        for asset in ("BTC", "ETH", "SOL", "BNB", "XRP"):
            with self.subTest(asset=asset):
                transport = StubBinanceTransport()
                transport.default_payload = json.dumps([row(first, first + 86_400_000 - 1)]).encode()
                provider = BinanceLiveMarketDataProvider(transport, now_utc=lambda: datetime(2026, 8, 2, 12, tzinfo=UTC), monotonic_ms=lambda: 100_000)
                base = request(f"OP-LIVE-ASSET-{asset}")
                value = replace(base, asset=asset, pair=f"{asset}USDT",
                                start_date=datetime(2026, 5, 29, tzinfo=UTC).date(),
                                end_date=datetime(2026, 5, 29, tzinfo=UTC).date())
                result = provider.fetch_daily_ohlcv(value)
                self.assertEqual(asset, result.bars[0].asset)
                self.assertIn(f"symbol={asset}USDT", transport.calls[0][0])
                self.assertEqual(1, len(transport.calls))

    def test_capabilities_are_no_io_and_health_is_bounded(self) -> None:
        provider = BinanceSharedContractTests().make_provider()
        cap_id = "OP-LIVE-CAP-NO-IO"
        from crypto_trust_agent.application.dto.live_market import LiveMarketCapabilitiesRequestDTO, LiveMarketHealthRequestDTO
        from tests.contract.shared_live_market_assertions import deadline
        capabilities = provider.capabilities(LiveMarketCapabilitiesRequestDTO(cap_id, deadline(cap_id)))
        self.assertEqual("none", capabilities.authentication)
        self.assertEqual([], provider._transport.calls)
        health_id = "OP-LIVE-HEALTH-BOUND"
        health = provider.health_check(LiveMarketHealthRequestDTO(health_id, deadline(health_id)))
        self.assertEqual("healthy", health.status)
        self.assertEqual(1, len(provider._transport.calls))
        self.assertLessEqual(provider._transport.calls[0][1], 3_000)

    def test_safe_observable_event_and_production_factory(self) -> None:
        events = self.RecordingEvents()
        transport = StubBinanceTransport()
        transport.configure(RuntimeError("Authorization Bearer TOP-SECRET"))
        provider = BinanceLiveMarketDataProvider(
            transport, now_utc=lambda: datetime(2026, 8, 2, 12, tzinfo=UTC),
            monotonic_ms=lambda: 100_000, event_sink=events,
        )
        result = provider.fetch_daily_ohlcv(request("OP-LIVE-EVENT"))
        self.assertEqual("unexpected_provider_error", result.error.code)
        self.assertEqual(1, len(events.items))
        rendered = json.dumps(events.items).lower()
        self.assertNotIn("top-secret", rendered)
        self.assertNotIn("authorization", rendered)
        self.assertEqual(0, events.items[0]["retry_count"])
        production = BinanceLiveMarketDataProvider.production()
        self.assertFalse(production.non_production)


if __name__ == "__main__":
    unittest.main()
