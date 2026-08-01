"""Frozen EvidenceExtractor boundary DTOs for contract version 1.0.0."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Any
from urllib.parse import urlsplit

from crypto_trust_agent.application.dto.common import DeadlineDTO
from crypto_trust_agent.domain.primitives import ContractValidationError, UtcInstant

SCHEMA_VERSION = "1.0.0"
MAX_INLINE_BYTES = 1_048_576
EXTRACT_ERROR_CODES = (
    "raw_record_not_safe", "input_too_large", "guardrail_rejected",
    "invalid_extraction_schema", "extractor_rate_limited", "extractor_timeout",
    "extractor_unavailable", "deadline_exceeded", "unexpected_provider_error",
)
REPAIR_ERROR_CODES = (
    "guardrail_rejected", "invalid_extraction_schema", "extractor_rate_limited",
    "extractor_timeout", "extractor_unavailable", "deadline_exceeded",
    "unexpected_provider_error",
)
EXTRACTOR_HEALTH_ERROR_CODES = (
    "extractor_timeout", "extractor_unavailable", "deadline_exceeded",
    "unexpected_provider_error",
)

_OPERATION = re.compile(r"^OP-[A-Z0-9][A-Z0-9._:-]{0,126}$")
_TASK = re.compile(r"^TASK-[A-Za-z0-9._:-]{1,123}$")
_EXECUTION = re.compile(r"^EXEC-[A-Za-z0-9._:-]{1,123}$")
_RAW = re.compile(r"^RAW-")
_CLAIM = re.compile(r"^XCL-")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_POLICY = re.compile(r"^[a-z0-9_-]+-[0-9]+\.[0-9]+\.[0-9]+$")
_ASSET = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractValidationError(message)


def _bounded_text(value: str, minimum: int, maximum: int, name: str) -> None:
    _require(isinstance(value, str) and minimum <= len(value) <= maximum, f"invalid {name}")


def _identifier(value: str, pattern: re.Pattern[str], name: str, *, prefix: bool = False) -> None:
    matched = pattern.match(value) if prefix and isinstance(value, str) else pattern.fullmatch(value) if isinstance(value, str) else None
    _require(matched is not None, f"invalid {name}")


def _utc(value: str | UtcInstant) -> UtcInstant:
    return value if isinstance(value, UtcInstant) else UtcInstant(value)


def _assets(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    frozen = tuple(values)
    _require(1 <= len(frozen) <= 100 and len(set(frozen)) == len(frozen), "invalid assets")
    _require(all(isinstance(item, str) and _ASSET.fullmatch(item) for item in frozen), "invalid asset")
    return frozen


def _wire(value: Any) -> Any:
    if hasattr(value, "to_wire"):
        return value.to_wire()
    if isinstance(value, UtcInstant):
        return str(value)
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    return value


class WireDTO:
    def to_wire(self) -> dict[str, object]:
        return {field.name: _wire(getattr(self, field.name)) for field in fields(self)}


@dataclass(frozen=True, slots=True)
class InlineContentInputDTO(WireDTO):
    clean_content: str
    kind: str = "inline"

    def __post_init__(self) -> None:
        _require(self.kind == "inline", "invalid content kind")
        _require(isinstance(self.clean_content, str) and 1 <= len(self.clean_content) <= MAX_INLINE_BYTES, "invalid clean_content")
        _require(len(self.clean_content.encode("utf-8")) <= MAX_INLINE_BYTES, "clean_content exceeds byte limit")

    def to_wire(self) -> dict[str, object]:
        return {"kind": self.kind, "clean_content": self.clean_content}


@dataclass(frozen=True, slots=True)
class LocatorContentInputDTO(WireDTO):
    locator: str
    kind: str = "locator"

    def __post_init__(self) -> None:
        _require(self.kind == "locator", "invalid content kind")
        _require(isinstance(self.locator, str) and len(self.locator) <= 4_096 and bool(urlsplit(self.locator).scheme), "invalid locator")

    def to_wire(self) -> dict[str, object]:
        return {"kind": self.kind, "locator": self.locator}


ContentInputDTO = InlineContentInputDTO | LocatorContentInputDTO


@dataclass(frozen=True, slots=True)
class ExtractRequestDTO(WireDTO):
    operation_id: str
    task_id: str
    execution_id: str
    raw_record_id: str
    raw_content_hash: str
    content: ContentInputDTO
    assets: tuple[str, ...]
    allowed_event_taxonomy: tuple[str, ...]
    output_schema_version: str
    guardrail_policy_version: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _identifier(self.operation_id, _OPERATION, "operation_id")
        _identifier(self.task_id, _TASK, "task_id")
        _identifier(self.execution_id, _EXECUTION, "execution_id")
        _identifier(self.raw_record_id, _RAW, "raw_record_id", prefix=True)
        _identifier(self.raw_content_hash, _HASH, "raw_content_hash")
        _require(isinstance(self.content, (InlineContentInputDTO, LocatorContentInputDTO)), "invalid content")
        object.__setattr__(self, "assets", _assets(self.assets))
        taxonomy = tuple(self.allowed_event_taxonomy)
        _require(1 <= len(taxonomy) <= 100 and len(set(taxonomy)) == len(taxonomy), "invalid allowed_event_taxonomy")
        _require(all(isinstance(item, str) and _SAFE.fullmatch(item) for item in taxonomy), "invalid taxonomy value")
        object.__setattr__(self, "allowed_event_taxonomy", taxonomy)
        _require(self.output_schema_version == SCHEMA_VERSION, "invalid output_schema_version")
        _identifier(self.guardrail_policy_version, _POLICY, "guardrail_policy_version")
        _require(isinstance(self.deadline, DeadlineDTO) and self.deadline.operation_id == self.operation_id, "deadline operation_id mismatch")


@dataclass(frozen=True, slots=True)
class ExtractionProviderDTO(WireDTO):
    name: str
    model_version: str
    invocation_id: str

    def __post_init__(self) -> None:
        _bounded_text(self.name, 1, 128, "provider name")
        _bounded_text(self.model_version, 1, 128, "model_version")
        _bounded_text(self.invocation_id, 1, 128, "invocation_id")


ProviderDTO = ExtractionProviderDTO


@dataclass(frozen=True, slots=True)
class ExtractedClaimDTO(WireDTO):
    extracted_claim_id: str
    text: str
    quote: str
    related_assets: tuple[str, ...]
    event_type: str
    sentiment: str
    relevance: str

    def __post_init__(self) -> None:
        _identifier(self.extracted_claim_id, _CLAIM, "extracted_claim_id", prefix=True)
        _bounded_text(self.text, 1, 2_000, "text")
        _bounded_text(self.quote, 1, 4_096, "quote")
        object.__setattr__(self, "related_assets", _assets(self.related_assets))
        _identifier(self.event_type, _SAFE, "event_type")
        _require(self.sentiment in {"negative", "neutral", "positive", "mixed"}, "invalid sentiment")
        _require(self.relevance in {"low", "medium", "high"}, "invalid relevance")


@dataclass(frozen=True, slots=True)
class ValidationErrorDTO(WireDTO):
    path: str
    code: str
    safe_message: str

    def __post_init__(self) -> None:
        _bounded_text(self.path, 1, 256, "path")
        _identifier(self.code, _SAFE, "code")
        _bounded_text(self.safe_message, 1, 512, "safe_message")


@dataclass(frozen=True, slots=True)
class UsageDTO(WireDTO):
    input_units: int | None
    output_units: int | None

    def __post_init__(self) -> None:
        for value in (self.input_units, self.output_units):
            _require(value is None or (type(value) is int and value >= 0), "invalid usage units")


@dataclass(frozen=True, slots=True)
class ExtractionResultDTO(WireDTO):
    outcome: str
    raw_record_id: str
    provider: ExtractionProviderDTO
    claims: tuple[ExtractedClaimDTO, ...]
    validation_errors: tuple[ValidationErrorDTO, ...]
    usage: UsageDTO
    started_at: str | UtcInstant
    finished_at: str | UtcInstant
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _require(self.outcome in {"valid", "invalid", "quarantined"}, "invalid extraction outcome")
        _identifier(self.raw_record_id, _RAW, "raw_record_id", prefix=True)
        _require(isinstance(self.provider, ExtractionProviderDTO), "invalid provider")
        claims = tuple(self.claims)
        errors = tuple(self.validation_errors)
        _require(len(claims) <= 200 and all(isinstance(item, ExtractedClaimDTO) for item in claims), "invalid claims")
        _require(len(errors) <= 100 and all(isinstance(item, ValidationErrorDTO) for item in errors), "invalid validation_errors")
        _require(isinstance(self.usage, UsageDTO), "invalid usage")
        started = _utc(self.started_at)
        finished = _utc(self.finished_at)
        _require(started.as_datetime() <= finished.as_datetime(), "invalid result timestamps")
        object.__setattr__(self, "claims", claims)
        object.__setattr__(self, "validation_errors", errors)
        object.__setattr__(self, "started_at", started)
        object.__setattr__(self, "finished_at", finished)


@dataclass(frozen=True, slots=True)
class RepairRequestDTO(WireDTO):
    operation_id: str
    task_id: str
    execution_id: str
    raw_record_id: str
    raw_content_hash: str
    context_hash: str
    original_result: ExtractionResultDTO
    validator_errors: tuple[ValidationErrorDTO, ...]
    output_schema_version: str
    guardrail_policy_version: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _identifier(self.operation_id, _OPERATION, "operation_id")
        _identifier(self.task_id, _TASK, "task_id")
        _identifier(self.execution_id, _EXECUTION, "execution_id")
        _identifier(self.raw_record_id, _RAW, "raw_record_id", prefix=True)
        _identifier(self.raw_content_hash, _HASH, "raw_content_hash")
        _identifier(self.context_hash, _HASH, "context_hash")
        _require(isinstance(self.original_result, ExtractionResultDTO) and self.original_result.raw_record_id == self.raw_record_id, "invalid original_result")
        errors = tuple(self.validator_errors)
        _require(1 <= len(errors) <= 100 and all(isinstance(item, ValidationErrorDTO) for item in errors), "invalid validator_errors")
        object.__setattr__(self, "validator_errors", errors)
        _require(self.output_schema_version == SCHEMA_VERSION, "invalid output_schema_version")
        _identifier(self.guardrail_policy_version, _POLICY, "guardrail_policy_version")
        _require(isinstance(self.deadline, DeadlineDTO) and self.deadline.operation_id == self.operation_id, "deadline operation_id mismatch")


@dataclass(frozen=True, slots=True)
class ExtractorHealthCheckRequestDTO(WireDTO):
    operation_id: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _identifier(self.operation_id, _OPERATION, "operation_id")
        _require(isinstance(self.deadline, DeadlineDTO) and self.deadline.operation_id == self.operation_id, "deadline operation_id mismatch")


__all__ = (
    "EXTRACT_ERROR_CODES", "EXTRACTOR_HEALTH_ERROR_CODES", "REPAIR_ERROR_CODES",
    "ContentInputDTO", "ExtractRequestDTO", "ExtractedClaimDTO",
    "ExtractionProviderDTO", "ExtractionResultDTO", "ExtractorHealthCheckRequestDTO",
    "InlineContentInputDTO", "LocatorContentInputDTO", "ProviderDTO", "RepairRequestDTO",
    "UsageDTO", "ValidationErrorDTO",
)
