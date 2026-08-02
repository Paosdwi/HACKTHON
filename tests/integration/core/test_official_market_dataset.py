from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.infrastructure.fakes.official_market_dataset import (
    LocalOfficialMarketDatasetReader,
)

DATASET_METADATA = (
    PROJECT_ROOT / "數據" / "HOYA_BIT_crypto_market_dataset" / "dataset_metadata.json"
)


class FullOfficialMarketDatasetIntegrationTests(unittest.TestCase):
    def test_all_five_assets_are_loaded_from_metadata_relative_csv_paths(self) -> None:
        dataset = LocalOfficialMarketDatasetReader().read(DATASET_METADATA)
        self.assertEqual("Crypto Market Dataset", dataset.dataset_name)
        self.assertEqual("hackathon-provided official dataset", dataset.lineage_label)
        self.assertEqual("public_market_data", dataset.source_name)
        self.assertEqual("public_market_data", dataset.source_type)
        self.assertEqual("UTC", dataset.time_basis)
        self.assertEqual("1d", dataset.interval)
        self.assertEqual("USDT", dataset.price_unit)
        self.assertEqual(
            {"BTC", "ETH", "SOL", "BNB", "XRP"},
            {series.asset for series in dataset.series},
        )
        for series in dataset.series:
            with self.subTest(asset=series.asset):
                self.assertEqual(f"{series.asset}USDT", series.pair)
                self.assertEqual(1826, len(series.bars))
                self.assertEqual("2021-06-01", series.bars[0].day.isoformat())
                self.assertEqual("2026-05-31", series.bars[-1].day.isoformat())
                self.assertTrue(all(
                    isinstance(value, Decimal)
                    for bar in series.bars
                    for value in (bar.open, bar.high, bar.low, bar.close, bar.volume)
                ))
                self.assertTrue(all(bar.provenance == "official_dataset" for bar in series.bars))
                self.assertIn(f"data/{series.asset}_daily_ohlcv.csv", series.bars[0].source_ref)


if __name__ == "__main__":
    unittest.main()
