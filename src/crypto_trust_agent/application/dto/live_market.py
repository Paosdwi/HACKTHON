"""Application-owned LiveMarketDataProvider DTOs, contract version 1.0.0."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from datetime import date
from typing import Any

from crypto_trust_agent.application.dto.common import DeadlineDTO
from crypto_trust_agent.domain.primitives import CanonicalDecimal, ContractValidationError, UtcInstant

SCHEMA_VERSION = "1.0.0"
PROVIDER = "binance_spot"
RULESET_VERSION = "binance-live-market-1.0.0"
SUPPORTED_ASSETS = ("BTC", "ETH", "SOL", "BNB", "XRP")
FETCH_ERROR_CODES = (
    "invalid_request", "payload_conflict", "deadline_exceeded", "provider_timeout",
    "provider_rate_limited", "provider_unavailable", "provider_access_denied",
    "invalid_provider_schema", "invalid_market_bar", "coverage_gap",
    "reconciliation_failed", "payload_too_large", "unexpected_provider_error",
)
_OPERATION = re.compile(r"^OP-[A-Z0-9][A-Z0-9._:-]{0,126}$")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractValidationError(message)


def _day(value: str | date, name: str) -> date:
    try:
        return value if isinstance(value, date) else date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ContractValidationError(f"invalid {name}") from error


def _wire(value: Any) -> Any:
    if hasattr(value, "to_wire"):
        return value.to_wire()
    if isinstance(value, (UtcInstant, CanonicalDecimal)):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    return value


class WireDTO:
    def to_wire(self) -> dict[str, object]:
        values = {field.name: _wire(getattr(self, field.name)) for field in fields(self)}
        if "schema_version" in values:
            return {"schema_version": values.pop("schema_version"), **values}
        return values


@dataclass(frozen=True, slots=True)
class LiveMarketDataRequestDTO(WireDTO):
    operation_id: str
    asset: str
    pair: str
    start_date: str | date
    end_date: str | date
    as_of: str | UtcInstant
    deadline: DeadlineDTO
    interval: str = "1d"
    expected_provider: str = PROVIDER
    provider_ruleset_version: str = RULESET_VERSION
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _require(isinstance(self.operation_id, str) and _OPERATION.fullmatch(self.operation_id) is not None, "invalid operation_id")
        _require(self.asset in SUPPORTED_ASSETS and self.pair == f"{self.asset}USDT", "invalid asset/pair")
        start, end = _day(self.start_date, "start_date"), _day(self.end_date, "end_date")
        _require(start <= end and (end - start).days <= 399, "invalid or oversized date range")
        _require(self.interval == "1d", "unsupported interval")
        _require(self.expected_provider == PROVIDER, "unsupported provider")
        _require(self.provider_ruleset_version == RULESET_VERSION, "unsupported provider ruleset")
        _require(isinstance(self.deadline, DeadlineDTO) and self.deadline.operation_id == self.operation_id, "deadline operation_id mismatch")
        object.__setattr__(self, "start_date", start)
        object.__setattr__(self, "end_date", end)
        object.__setattr__(self, "as_of", self.as_of if isinstance(self.as_of, UtcInstant) else UtcInstant(self.as_of))


@dataclass(frozen=True, slots=True)
class LiveMarketBarDTO(WireDTO):
    asset: str
    pair: str
    day: str | date
    open: str | CanonicalDecimal
    high: str | CanonicalDecimal
    low: str | CanonicalDecimal
    close: str | CanonicalDecimal
    volume: str | CanonicalDecimal
    source_url: str
    fetched_at: str | UtcInstant
    source_open_time_ms: int
    source_close_time_ms: int
    content_hash: str
    interval: str = "1d"
    provider: str = PROVIDER
    provider_ruleset_version: str = RULESET_VERSION
    provenance: str = "live_extension"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _require(self.asset in SUPPORTED_ASSETS and self.pair == f"{self.asset}USDT", "invalid asset/pair")
        object.__setattr__(self, "day", _day(self.day, "day"))
        values: dict[str, CanonicalDecimal] = {}
        for name in ("open", "high", "low", "close", "volume"):
            raw = getattr(self, name)
            value = raw if isinstance(raw, CanonicalDecimal) else CanonicalDecimal(raw)
            values[name] = value.require_nonnegative()
            object.__setattr__(self, name, value)
        _require(values["high"].as_decimal() >= max(values["open"].as_decimal(), values["close"].as_decimal(), values["low"].as_decimal()), "invalid OHLC high")
        _require(values["low"].as_decimal() <= min(values["open"].as_decimal(), values["close"].as_decimal(), values["high"].as_decimal()), "invalid OHLC low")
        _require(self.interval == "1d" and self.provider == PROVIDER and self.provider_ruleset_version == RULESET_VERSION, "invalid provider metadata")
        _require(self.provenance == "live_extension", "invalid provenance")
        _require(isinstance(self.source_url, str) and self.source_url.startswith("https://api.binance.com/api/v3/klines?"), "invalid source_url")
        _require(type(self.source_open_time_ms) is int and type(self.source_close_time_ms) is int and self.source_close_time_ms > self.source_open_time_ms, "invalid source time")
        _require(isinstance(self.content_hash, str) and _HASH.fullmatch(self.content_hash) is not None, "invalid content_hash")
        object.__setattr__(self, "fetched_at", self.fetched_at if isinstance(self.fetched_at, UtcInstant) else UtcInstant(self.fetched_at))


@dataclass(frozen=True, slots=True)
class LiveMarketDataResultDTO(WireDTO):
    operation_id: str
    asset: str
    pair: str
    requested_start_date: str | date
    requested_end_date: str | date
    bars: tuple[LiveMarketBarDTO, ...]
    complete: bool
    missing_dates: tuple[str | date, ...]
    incomplete_dates: tuple[str | date, ...]
    fetched_at: str | UtcInstant
    provider: str = PROVIDER
    provider_ruleset_version: str = RULESET_VERSION
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION and _OPERATION.fullmatch(self.operation_id) is not None, "invalid result identity")
        _require(self.asset in SUPPORTED_ASSETS and self.pair == f"{self.asset}USDT", "invalid asset/pair")
        start, end = _day(self.requested_start_date, "requested_start_date"), _day(self.requested_end_date, "requested_end_date")
        bars = tuple(self.bars)
        _require(all(isinstance(bar, LiveMarketBarDTO) and bar.asset == self.asset and bar.pair == self.pair for bar in bars), "invalid result bars")
        days = tuple(bar.day for bar in bars)
        _require(days == tuple(sorted(days)) and len(days) == len(set(days)), "bars must be ordered and unique")
        missing = tuple(_day(value, "missing_date") for value in self.missing_dates)
        incomplete = tuple(_day(value, "incomplete_date") for value in self.incomplete_dates)
        _require(type(self.complete) is bool and self.complete == (not missing and not incomplete), "complete flag mismatch")
        _require(self.provider == PROVIDER and self.provider_ruleset_version == RULESET_VERSION, "invalid provider metadata")
        object.__setattr__(self, "requested_start_date", start)
        object.__setattr__(self, "requested_end_date", end)
        object.__setattr__(self, "bars", bars)
        object.__setattr__(self, "missing_dates", missing)
        object.__setattr__(self, "incomplete_dates", incomplete)
        object.__setattr__(self, "fetched_at", self.fetched_at if isinstance(self.fetched_at, UtcInstant) else UtcInstant(self.fetched_at))


@dataclass(frozen=True, slots=True)
class LiveMarketHealthRequestDTO(WireDTO):
    operation_id: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION and _OPERATION.fullmatch(self.operation_id) is not None, "invalid health request")
        _require(isinstance(self.deadline, DeadlineDTO) and self.deadline.operation_id == self.operation_id, "deadline operation_id mismatch")


@dataclass(frozen=True, slots=True)
class LiveMarketCapabilitiesRequestDTO(LiveMarketHealthRequestDTO):
    pass


@dataclass(frozen=True, slots=True)
class LiveMarketCapabilitiesDTO(WireDTO):
    assets: tuple[str, ...] = SUPPORTED_ASSETS
    pairs: tuple[str, ...] = tuple(f"{asset}USDT" for asset in SUPPORTED_ASSETS)
    interval: str = "1d"
    timezone: str = "UTC"
    quote_asset: str = "USDT"
    max_page_size: int = 1000
    authentication: str = "none"
    provider: str = PROVIDER
    provider_ruleset_version: str = RULESET_VERSION
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION and self.assets == SUPPORTED_ASSETS, "invalid capabilities")
        _require(self.pairs == tuple(f"{asset}USDT" for asset in SUPPORTED_ASSETS), "invalid pairs")
        _require(self.interval == "1d" and self.timezone == "UTC" and self.quote_asset == "USDT", "invalid market basis")
        _require(self.max_page_size == 1000 and self.authentication == "none", "invalid provider capability")
        _require(self.provider == PROVIDER and self.provider_ruleset_version == RULESET_VERSION, "invalid provider metadata")


__all__ = (
    "FETCH_ERROR_CODES", "LiveMarketBarDTO", "LiveMarketCapabilitiesDTO",
    "LiveMarketCapabilitiesRequestDTO", "LiveMarketDataRequestDTO",
    "LiveMarketDataResultDTO", "LiveMarketHealthRequestDTO", "PROVIDER",
    "RULESET_VERSION", "SCHEMA_VERSION", "SUPPORTED_ASSETS",
)
