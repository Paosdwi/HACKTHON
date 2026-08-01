from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.common import DeadlineDTO
from crypto_trust_agent.application.dto.market_regime import (
    ExpectedModelDTO,
    FeatureDTO,
    FeatureWindowDTO,
    InferRequestDTO,
)
from crypto_trust_agent.application.use_cases.infer_market_regime import (
    InferMarketRegimeCommand,
    InferMarketRegimeUseCase,
)
from crypto_trust_agent.domain.market import MarketBar, analyze_historical_market
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock
from crypto_trust_agent.infrastructure.fakes.market_regime import FakeMarketRegimeProvider
from crypto_trust_agent.infrastructure.fakes.official_market_dataset import (
    FakeLiveMarketExtension,
    LocalOfficialMarketDatasetReader,
)

FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "market"
HASH_A = "sha256:" + "a" * 64


def deadline(operation_id: str) -> DeadlineDTO:
    return DeadlineDTO(
        "1.0.0", operation_id, "2026-08-01T02:00:25Z", 25_000,
        "2026-08-01T02:00:00Z", 100,
    )


class OfficialMarketDatasetUnitTests(unittest.TestCase):
    def test_reader_uses_metadata_relative_file_and_decimal_utc_lineage(self) -> None:
        dataset = LocalOfficialMarketDatasetReader().read(FIXTURE_ROOT / "dataset_metadata.json")
        self.assertEqual("public_market_data", dataset.source_name)
        self.assertEqual("public_market_data", dataset.source_type)
        self.assertEqual("hackathon-provided official dataset", dataset.lineage_label)
        self.assertEqual("UTC", dataset.time_basis)
        self.assertEqual("1d", dataset.interval)
        self.assertEqual("USDT", dataset.price_unit)
        series = dataset.series[0]
        self.assertEqual("BTC", series.asset)
        self.assertEqual("BTCUSDT", series.pair)
        self.assertEqual(4, len(series.bars))
        self.assertIsInstance(series.bars[0].open, Decimal)
        self.assertEqual("2026-01-01T00:00:00Z", series.bars[0].interval_start_utc)
        self.assertEqual("official_dataset", series.bars[0].provenance)
        self.assertIn("data/BTC_daily_ohlcv.csv", series.bars[0].source_ref)

    def test_reader_rejects_macos_artifact_path_before_file_access(self) -> None:
        metadata = json.loads((FIXTURE_ROOT / "dataset_metadata.json").read_text(encoding="utf-8"))
        metadata["symbols"][0]["file"] = "__MACOSX/._BTC_daily_ohlcv.csv"
        with tempfile.TemporaryDirectory() as directory:
            metadata_path = Path(directory) / "dataset_metadata.json"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "forbidden"):
                LocalOfficialMarketDatasetReader().read(metadata_path)

    def test_historical_formula_has_warmup_reporting_ranges_and_canonical_results(self) -> None:
        dataset = LocalOfficialMarketDatasetReader().read(FIXTURE_ROOT / "dataset_metadata.json")
        analysis = analyze_historical_market(
            dataset.series[0],
            warmup_start=date(2026, 1, 1),
            reporting_start=date(2026, 1, 2),
            reporting_end=date(2026, 1, 4),
        )
        self.assertEqual("market-formulas-1.0.0", analysis.formula_version)
        self.assertEqual(date(2026, 1, 1), analysis.warmup_start)
        self.assertEqual(date(2026, 1, 2), analysis.reporting_start)
        self.assertEqual(date(2026, 1, 4), analysis.reporting_end)
        self.assertEqual("0.2", str(analysis.values["return_period"]))
        self.assertEqual("125", str(analysis.values["high"]))
        self.assertEqual("95", str(analysis.values["low"]))
        self.assertEqual("1", str(analysis.values["volume_change"]))
        self.assertEqual("110", str(analysis.values["sma"]))
        self.assertEqual("0", str(analysis.values["max_drawdown"]))
        self.assertEqual("up", analysis.trend)
        self.assertEqual("bullish", analysis.regime)

    def test_fake_live_extension_preserves_transition_and_never_forward_fills_gap(self) -> None:
        dataset = LocalOfficialMarketDatasetReader().read(FIXTURE_ROOT / "dataset_metadata.json")
        live = MarketBar(
            "BTC", "BTCUSDT", date(2026, 1, 6), Decimal("121"), Decimal("126"),
            Decimal("120"), Decimal("125"), Decimal("50"), "live_extension",
            "fake-live:BTC:2026-01-06",
        )
        merged = FakeLiveMarketExtension((live,)).extend(dataset.series[0], date(2026, 1, 6))
        self.assertTrue(merged.partial)
        self.assertEqual(date(2026, 1, 6), merged.transition_date)
        self.assertEqual("live_extension", merged.bars[-1].provenance)
        self.assertNotIn(date(2026, 1, 5), {bar.day for bar in merged.bars})
        self.assertIn("live_extension_gap", merged.limitations)


class MarketRegimeFallbackUnitTests(unittest.TestCase):
    def test_provider_timeout_calls_once_and_core_builds_versioned_fallback_analysis(self) -> None:
        clock = FakeClock("2026-08-01T02:00:00Z")
        provider = FakeMarketRegimeProvider(clock)
        provider.configure_infer("OP-MR-FALLBACK", "market_regime_timeout")
        request = InferRequestDTO(
            "OP-MR-FALLBACK", "TASK-001", "EXEC-001", "BTC",
            "2026-08-01T00:00:00Z", FeatureWindowDTO("2026-07-03", "2026-08-01"),
            (FeatureDTO("return_14d", "-0.0312", "market-formulas-1.0.0", ("DATASET:BTC",)),),
            HASH_A, ExpectedModelDTO("market-regime-xgboost", "1.0.0"),
            deadline("OP-MR-FALLBACK"),
        )
        result = InferMarketRegimeUseCase(provider, clock).execute(
            InferMarketRegimeCommand(
                "AN-001", request, ("DATASET:BTC",), "bullish",
                "market-formulas-1.0.0",
            )
        )
        self.assertEqual(1, provider.invocation_count)
        self.assertEqual("deterministic_fallback", result.producer.kind.value)
        self.assertEqual("market-formulas-1.0.0", result.producer.ruleset_version)
        self.assertEqual("fallback", result.quality.status)
        self.assertIn("market_regime_timeout", result.quality.limitations)
        self.assertEqual("1", str(result.values["bullish_probability"]))
        self.assertEqual("0", str(result.values["bearish_probability"]))

    def test_schema_valid_opaque_provider_model_version_maps_to_analysis_result(self) -> None:
        clock = FakeClock("2026-08-01T02:00:00Z")
        provider = FakeMarketRegimeProvider(clock, model_version="v1")
        request = InferRequestDTO(
            "OP-MR-SUCCESS", "TASK-001", "EXEC-001", "BTC",
            "2026-08-01T00:00:00Z", FeatureWindowDTO("2026-07-03", "2026-08-01"),
            (FeatureDTO("return_14d", "-0.0312", "market-formulas-1.0.0", ("DATASET:BTC",)),),
            HASH_A, ExpectedModelDTO("market-regime-xgboost", "1.0.0"),
            deadline("OP-MR-SUCCESS"),
        )
        result = InferMarketRegimeUseCase(provider, clock).execute(
            InferMarketRegimeCommand(
                "AN-002", request, ("DATASET:BTC",), "bearish",
                "market-formulas-1.0.0",
            )
        )
        self.assertEqual("model", result.producer.kind.value)
        self.assertEqual("v1", result.producer.version)
        self.assertEqual("valid", result.quality.status)
        self.assertEqual(1, provider.invocation_count)


if __name__ == "__main__":
    unittest.main()
