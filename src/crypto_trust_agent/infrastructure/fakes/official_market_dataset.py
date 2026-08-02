"""Core-owned local reader and live-extension fake for official market test data."""

from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath

from crypto_trust_agent.domain.market import (
    ExtendedMarketSeries,
    MarketBar,
    MarketSeries,
    OfficialMarketDataset,
)
from crypto_trust_agent.domain.primitives import UtcInstant

_COLUMNS = ("date", "open", "high", "low", "close", "volume")
_SUPPORTED_ASSETS = {"BTC", "ETH", "SOL", "BNB", "XRP"}
_FORBIDDEN_PARTS = {"__MACOSX", ".DS_Store"}


class LocalOfficialMarketDatasetReader:
    """Read only metadata-declared relative CSV paths; never discovers directories."""

    non_production = True

    def read(self, metadata_path: str | Path) -> OfficialMarketDataset:
        path = Path(metadata_path)
        with path.open("r", encoding="utf-8") as stream:
            metadata = json.load(stream, parse_float=Decimal)
        self._validate_metadata(metadata)
        root = path.parent.resolve()
        period_start = date.fromisoformat(metadata["period"]["start_date"])
        period_end = date.fromisoformat(metadata["period"]["end_date"])
        series = tuple(
            self._read_symbol(root, path.name, symbol, period_start, period_end)
            for symbol in metadata["symbols"]
        )
        return OfficialMarketDataset(
            metadata["dataset_name"],
            metadata["source"]["name"],
            metadata["source"]["type"],
            metadata["time_basis"],
            metadata["interval"],
            metadata["price_unit"],
            period_start,
            period_end,
            series,
        )

    @staticmethod
    def _validate_metadata(metadata: object) -> None:
        if not isinstance(metadata, dict):
            raise ValueError("dataset metadata must be an object")
        required = {
            "dataset_name", "generated_at", "source", "time_basis", "interval",
            "price_unit", "period", "symbols", "columns",
        }
        if set(metadata) != required:
            raise ValueError("unexpected dataset metadata fields")
        UtcInstant(metadata["generated_at"])
        if metadata["source"] != {"name": "public_market_data", "type": "public_market_data"}:
            raise ValueError("invalid source metadata")
        if metadata["time_basis"] != "UTC" or metadata["interval"] != "1d" or metadata["price_unit"] != "USDT":
            raise ValueError("unsupported market dataset basis")
        if [column.get("name") for column in metadata["columns"]] != list(_COLUMNS):
            raise ValueError("invalid OHLCV columns")
        symbols = metadata["symbols"]
        if not isinstance(symbols, list) or not symbols:
            raise ValueError("symbols are required")
        assets = [symbol.get("asset") for symbol in symbols]
        if len(assets) != len(set(assets)) or not set(assets).issubset(_SUPPORTED_ASSETS):
            raise ValueError("invalid or duplicate assets")

    def _read_symbol(
        self,
        root: Path,
        metadata_name: str,
        symbol: dict[str, object],
        period_start: date,
        period_end: date,
    ) -> MarketSeries:
        required = {"asset", "pair", "file", "rows", "start_date", "end_date"}
        if set(symbol) != required:
            raise ValueError("unexpected symbol metadata fields")
        asset = symbol["asset"]
        pair = symbol["pair"]
        relative_text = symbol["file"]
        if asset not in _SUPPORTED_ASSETS or pair != f"{asset}USDT":
            raise ValueError("asset/pair/USDT mismatch")
        if not isinstance(relative_text, str):
            raise ValueError("invalid dataset file path")
        relative = PurePosixPath(relative_text)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or any(part in _FORBIDDEN_PARTS or part.startswith("._") for part in relative.parts)
            or relative.suffix.lower() != ".csv"
        ):
            raise ValueError("unsafe or forbidden dataset file path")
        csv_path = (root / Path(*relative.parts)).resolve()
        if root not in csv_path.parents:
            raise ValueError("dataset file escapes metadata root")
        bars = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != _COLUMNS:
                raise ValueError("CSV columns do not match metadata")
            for row_number, row in enumerate(reader, start=2):
                try:
                    day = date.fromisoformat(row["date"])
                    values = tuple(Decimal(row[name]) for name in _COLUMNS[1:])
                except (InvalidOperation, TypeError, ValueError) as error:
                    raise ValueError("invalid OHLCV row") from error
                bars.append(MarketBar(
                    asset,
                    pair,
                    day,
                    *values,
                    "official_dataset",
                    f"official-dataset:{relative.as_posix()}:row:{row_number}",
                ))
        expected_rows = symbol["rows"]
        symbol_start = date.fromisoformat(symbol["start_date"])
        symbol_end = date.fromisoformat(symbol["end_date"])
        if (
            type(expected_rows) is not int
            or len(bars) != expected_rows
            or not bars
            or bars[0].day != symbol_start
            or bars[-1].day != symbol_end
            or symbol_start != period_start
            or symbol_end != period_end
        ):
            raise ValueError("row count or period mismatch")
        for previous, current in zip(bars, bars[1:]):
            if current.day != previous.day + timedelta(days=1):
                raise ValueError("duplicate, unordered, or missing UTC daily interval")
        return MarketSeries(asset, pair, tuple(bars), metadata_name)


class FakeLiveMarketExtension:
    """Configured live points only; gaps remain explicit and are never forward-filled."""

    non_production = True

    def __init__(self, points: tuple[MarketBar, ...]) -> None:
        self._points = tuple(points)
        if any(point.provenance != "live_extension" for point in self._points):
            raise ValueError("fake live points require live_extension provenance")

    def extend(self, official: MarketSeries, reporting_end: date) -> ExtendedMarketSeries:
        points = tuple(
            sorted(
                (
                    point for point in self._points
                    if point.asset == official.asset
                    and point.pair == official.pair
                    and official.bars[-1].day < point.day <= reporting_end
                ),
                key=lambda point: point.day,
            )
        )
        if not points:
            return ExtendedMarketSeries(
                official.bars,
                None,
                reporting_end > official.bars[-1].day,
                ("live_extension_unavailable",) if reporting_end > official.bars[-1].day else (),
            )
        limitations = []
        expected = official.bars[-1].day + timedelta(days=1)
        for point in points:
            if point.day != expected:
                limitations.append("live_extension_gap")
            expected = point.day + timedelta(days=1)
        if points[-1].day < reporting_end:
            limitations.append("live_extension_gap")
        return ExtendedMarketSeries(
            (*official.bars, *points),
            points[0].day,
            bool(limitations),
            tuple(dict.fromkeys(limitations)),
        )


__all__ = ("FakeLiveMarketExtension", "LocalOfficialMarketDatasetReader")
