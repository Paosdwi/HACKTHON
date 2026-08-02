"""Frozen v1 contract primitives that are safe to use in the Domain layer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Self


SCHEMA_VERSION = "1.0.0"

_CANONICAL_DECIMAL_PATTERN = re.compile(
    r"^(?:(?=(?:[^0-9]*[0-9]){1,38}[^0-9]*$)"
    r"(?:0|[1-9][0-9]*)(?:\.[0-9]{0,17}[1-9])?|"
    r"-(?=(?:[^0-9]*[0-9]){1,38}[^0-9]*$)(?!0$)"
    r"(?:0|[1-9][0-9]*)(?:\.[0-9]{0,17}[1-9])?)$"
)
_UTC_INSTANT_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)


class ContractValidationError(ValueError):
    """表示輸入不符合 frozen v1 contract。"""


@dataclass(frozen=True, slots=True)
class SchemaVersion:
    """Frozen contract set 的精確版本。"""

    value: str

    def __post_init__(self) -> None:
        if self.value != SCHEMA_VERSION:
            raise ContractValidationError("unsupported schema_version")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class UtcInstant:
    """保留 RFC 3339 UTC `Z` wire representation。"""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _UTC_INSTANT_PATTERN.fullmatch(
            self.value
        ):
            raise ContractValidationError("timestamp must be RFC 3339 UTC with Z")
        try:
            parsed = datetime.fromisoformat(self.value[:-1] + "+00:00")
        except ValueError as error:
            raise ContractValidationError("invalid UTC timestamp") from error
        if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
            raise ContractValidationError("timestamp must be UTC")

    def as_datetime(self) -> datetime:
        return datetime.fromisoformat(self.value[:-1] + "+00:00")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CanonicalDecimal:
    """ADR-004 canonical decimal string，絕不經過 binary float。"""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _CANONICAL_DECIMAL_PATTERN.fullmatch(
            self.value
        ):
            raise ContractValidationError("invalid canonical decimal string")

    def as_decimal(self) -> Decimal:
        return Decimal(self.value)

    def require_nonnegative(self) -> Self:
        if self.as_decimal() < 0:
            raise ContractValidationError("decimal must be nonnegative")
        return self

    def require_probability(self) -> Self:
        decimal_value = self.as_decimal()
        if decimal_value < 0 or decimal_value > 1:
            raise ContractValidationError("probability must be in [0, 1]")
        return self

    def __str__(self) -> str:
        return self.value
