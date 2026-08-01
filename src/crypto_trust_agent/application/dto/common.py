"""Frozen common boundary DTOs and receiver-local deadline enforcement."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from crypto_trust_agent.domain.primitives import (
    SCHEMA_VERSION,
    ContractValidationError,
    SchemaVersion,
    UtcInstant,
)


_OPERATION_ID_PATTERN = re.compile(r"^OP-[A-Z0-9][A-Z0-9._:-]{0,126}$")
_SAFE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DETAIL_VALUE_TYPES = (str, int, bool, type(None))


class DeadlineExceededError(RuntimeError):
    """表示 receiver 在開始 I/O 前已沒有可用 deadline budget。"""


class PortErrorCategory(str, Enum):
    VALIDATION = "validation"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    INTEGRITY = "integrity"
    QUOTA_EXHAUSTED = "quota_exhausted"
    UNSAFE_SOURCE = "unsafe_source"
    INVALID_PROVIDER_OUTPUT = "invalid_provider_output"
    UNEXPECTED = "unexpected"


def _validate_operation_id(value: str) -> None:
    if not isinstance(value, str) or not _OPERATION_ID_PATTERN.fullmatch(value):
        raise ContractValidationError("invalid operation_id")


def _validate_safe_code(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _SAFE_CODE_PATTERN.fullmatch(value):
        raise ContractValidationError(f"invalid {field_name}")


def _require_json_integer(value: int, minimum: int, maximum: int, name: str) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ContractValidationError(f"{name} must be between {minimum} and {maximum}")


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ContractValidationError("datetime must be timezone-aware UTC")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class DeadlineDTO:
    schema_version: str
    operation_id: str
    deadline_at_utc: str | UtcInstant
    budget_ms: int
    sent_at_utc: str | UtcInstant
    safety_margin_ms: int = 1_000

    def __post_init__(self) -> None:
        SchemaVersion(self.schema_version)
        _validate_operation_id(self.operation_id)
        deadline = (
            self.deadline_at_utc
            if isinstance(self.deadline_at_utc, UtcInstant)
            else UtcInstant(self.deadline_at_utc)
        )
        sent = (
            self.sent_at_utc
            if isinstance(self.sent_at_utc, UtcInstant)
            else UtcInstant(self.sent_at_utc)
        )
        _require_json_integer(self.budget_ms, 1, 900_000, "budget_ms")
        _require_json_integer(
            self.safety_margin_ms,
            100,
            5_000,
            "safety_margin_ms",
        )
        object.__setattr__(self, "deadline_at_utc", deadline)
        object.__setattr__(self, "sent_at_utc", sent)

    def to_wire(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "deadline_at_utc": str(self.deadline_at_utc),
            "budget_ms": self.budget_ms,
            "sent_at_utc": str(self.sent_at_utc),
            "safety_margin_ms": self.safety_margin_ms,
        }


@dataclass(frozen=True, slots=True)
class LocalDeadline:
    """只在單一 runtime 內有效，永不序列化至 Port wire。"""

    runtime_id: str
    deadline_monotonic_ms: int
    effective_timeout_ms: int

    def __post_init__(self) -> None:
        if not self.runtime_id:
            raise ContractValidationError("runtime_id is required")
        if type(self.deadline_monotonic_ms) is not int:
            raise ContractValidationError("deadline_monotonic_ms must be an integer")
        if type(self.effective_timeout_ms) is not int or self.effective_timeout_ms <= 0:
            raise ContractValidationError("effective_timeout_ms must be positive")


def build_local_deadline(
    deadline: DeadlineDTO,
    *,
    provider_timeout_ms: int,
    now_utc: datetime,
    now_monotonic_ms: int,
    runtime_id: str,
) -> LocalDeadline:
    """依 ADR-003 在 receiver runtime 建立 local monotonic deadline。"""

    if type(provider_timeout_ms) is not int or provider_timeout_ms < 0:
        raise ContractValidationError("provider_timeout_ms must be nonnegative")
    if type(now_monotonic_ms) is not int or now_monotonic_ms < 0:
        raise ContractValidationError("now_monotonic_ms must be nonnegative")
    if now_utc.tzinfo is None or now_utc.utcoffset() != timedelta(0):
        raise ContractValidationError("now_utc must be timezone-aware UTC")

    remaining = deadline.deadline_at_utc.as_datetime() - now_utc
    utc_remaining_ms = remaining // timedelta(milliseconds=1)
    usable_utc_ms = max(0, utc_remaining_ms - deadline.safety_margin_ms)
    effective_ms = max(
        0,
        min(provider_timeout_ms, deadline.budget_ms, usable_utc_ms),
    )
    if effective_ms <= 0:
        raise DeadlineExceededError("deadline exceeded before I/O")
    return LocalDeadline(
        runtime_id=runtime_id,
        deadline_monotonic_ms=now_monotonic_ms + effective_ms,
        effective_timeout_ms=effective_ms,
    )


@dataclass(frozen=True, slots=True)
class PortErrorDTO:
    schema_version: str
    code: str
    category: PortErrorCategory | str
    retryable: bool
    safe_message: str
    provider: str
    operation_id: str
    details: Mapping[str, str | int | bool | None]
    occurred_at: str | UtcInstant

    def __post_init__(self) -> None:
        SchemaVersion(self.schema_version)
        _validate_safe_code(self.code, "code")
        try:
            category = PortErrorCategory(self.category)
        except ValueError as error:
            raise ContractValidationError("invalid error category") from error
        if type(self.retryable) is not bool:
            raise ContractValidationError("retryable must be boolean")
        if not isinstance(self.safe_message, str) or not 1 <= len(self.safe_message) <= 512:
            raise ContractValidationError("safe_message must contain 1..512 characters")
        _validate_safe_code(self.provider, "provider")
        _validate_operation_id(self.operation_id)
        if not isinstance(self.details, Mapping) or len(self.details) > 64:
            raise ContractValidationError("details must contain at most 64 properties")
        details = dict(self.details)
        if any(type(value) not in _DETAIL_VALUE_TYPES for value in details.values()):
            raise ContractValidationError("details contains a non-scalar value")
        occurred_at = (
            self.occurred_at
            if isinstance(self.occurred_at, UtcInstant)
            else UtcInstant(self.occurred_at)
        )
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "details", MappingProxyType(details))
        object.__setattr__(self, "occurred_at", occurred_at)

    def to_wire(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "code": self.code,
            "category": self.category.value,
            "retryable": self.retryable,
            "safe_message": self.safe_message,
            "provider": self.provider,
            "operation_id": self.operation_id,
            "details": dict(self.details),
            "occurred_at": str(self.occurred_at),
        }

    def as_result(self) -> ErrorResultDTO:
        return ErrorResultDTO(error=self)


@dataclass(frozen=True, slots=True)
class ErrorResultDTO:
    error: PortErrorDTO
    status: str = "error"

    def __post_init__(self) -> None:
        if self.status != "error":
            raise ContractValidationError("error result status must be error")

    def to_wire(self) -> dict[str, object]:
        return {"status": self.status, "error": self.error.to_wire()}


def map_unexpected_exception(
    exception: BaseException,
    *,
    provider: str,
    operation_id: str,
    occurred_at: datetime,
) -> PortErrorDTO:
    """將未知例外轉成固定、安全且不包含 vendor payload 的 typed error。"""

    del exception
    return PortErrorDTO(
        schema_version=SCHEMA_VERSION,
        code="unexpected_provider_error",
        category=PortErrorCategory.UNEXPECTED,
        retryable=False,
        safe_message="Unexpected provider failure.",
        provider=provider,
        operation_id=operation_id,
        details={},
        occurred_at=_format_utc(occurred_at),
    )
