"""EvidenceExtractor 2.0.0 repair authority DTOs and canonical hashing."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields
from typing import Any

from crypto_trust_agent.application.dto.common import DeadlineDTO
from crypto_trust_agent.application.dto.evidence_extractor import (
    ContentInputDTO,
    ExtractionResultDTO,
    InlineContentInputDTO,
    LocatorContentInputDTO,
    ValidationErrorDTO,
)
from crypto_trust_agent.domain.primitives import ContractValidationError

SCHEMA_VERSION_V2 = "2.0.0"
OUTPUT_SCHEMA_VERSION = "1.0.0"
REPAIR_AUTHORIZATION_RULESET_VERSION = "repair-authorization-1.0.0"

REPAIR_V2_ERROR_CODES = (
    "guardrail_rejected",
    "invalid_extraction_schema",
    "repair_content_unavailable",
    "repair_content_hash_mismatch",
    "repair_asset_scope_violation",
    "repair_event_taxonomy_violation",
    "extractor_rate_limited",
    "extractor_timeout",
    "extractor_unavailable",
    "deadline_exceeded",
    "unexpected_provider_error",
)

_OPERATION = re.compile(r"^OP-[A-Z0-9][A-Z0-9._:-]{0,126}$")
_TASK = re.compile(r"^TASK-[A-Za-z0-9._:-]{1,123}$")
_EXECUTION = re.compile(r"^EXEC-[A-Za-z0-9._:-]{1,123}$")
_RAW = re.compile(r"^RAW-")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_POLICY = re.compile(r"^[a-z0-9_-]+-[0-9]+\.[0-9]+\.[0-9]+$")
_ASSET = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractValidationError(message)


def _identifier(value: str, pattern: re.Pattern[str], name: str, *, prefix: bool = False) -> None:
    matched = (
        pattern.match(value)
        if prefix and isinstance(value, str)
        else pattern.fullmatch(value)
        if isinstance(value, str)
        else None
    )
    _require(matched is not None, f"invalid {name}")


def _wire(value: Any) -> Any:
    if hasattr(value, "to_wire"):
        return value.to_wire()
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    return value


def _authority_assets(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    frozen = tuple(values)
    _require(1 <= len(frozen) <= 100, "invalid assets")
    _require(all(isinstance(item, str) and _ASSET.fullmatch(item) for item in frozen), "invalid asset")
    return frozen


def _request_assets(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    frozen = _authority_assets(values)
    _require(len(set(frozen)) == len(frozen), "invalid assets")
    return frozen


def _authority_taxonomy(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    frozen = tuple(values)
    _require(1 <= len(frozen) <= 100, "invalid allowed_event_taxonomy")
    _require(all(isinstance(item, str) and _SAFE.fullmatch(item) for item in frozen), "invalid taxonomy value")
    return frozen


def _request_taxonomy(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    frozen = _authority_taxonomy(values)
    _require(len(set(frozen)) == len(frozen), "invalid allowed_event_taxonomy")
    return frozen


@dataclass(frozen=True, slots=True)
class RepairAuthorizationInputDTO:
    """Core-owned inputs for the versioned repair authorization digest."""

    authorization_ruleset_version: str
    raw_record_id: str
    raw_content_hash: str
    clean_content_hash: str
    assets: tuple[str, ...]
    allowed_event_taxonomy: tuple[str, ...]
    output_schema_version: str
    guardrail_policy_version: str

    def __post_init__(self) -> None:
        _identifier(
            self.authorization_ruleset_version,
            _POLICY,
            "authorization_ruleset_version",
        )
        _identifier(self.raw_record_id, _RAW, "raw_record_id", prefix=True)
        _identifier(self.raw_content_hash, _HASH, "raw_content_hash")
        _identifier(self.clean_content_hash, _HASH, "clean_content_hash")
        object.__setattr__(self, "assets", _authority_assets(self.assets))
        object.__setattr__(
            self,
            "allowed_event_taxonomy",
            _authority_taxonomy(self.allowed_event_taxonomy),
        )
        _require(self.output_schema_version == OUTPUT_SCHEMA_VERSION, "invalid output_schema_version")
        _identifier(
            self.guardrail_policy_version,
            _POLICY,
            "guardrail_policy_version",
        )


def repair_authorization_payload(value: RepairAuthorizationInputDTO) -> dict[str, object]:
    """Return the approved minimal authority payload; no content or locator enters it."""

    _require(isinstance(value, RepairAuthorizationInputDTO), "invalid repair authorization input")
    return {
        "allowed_event_taxonomy": sorted(set(value.allowed_event_taxonomy)),
        "assets": sorted(set(value.assets)),
        "authorization_ruleset_version": value.authorization_ruleset_version,
        "clean_content_hash": value.clean_content_hash,
        "guardrail_policy_version": value.guardrail_policy_version,
        "output_schema_version": value.output_schema_version,
        "raw_content_hash": value.raw_content_hash,
        "raw_record_id": value.raw_record_id,
    }


def build_repair_authorization_hash(value: RepairAuthorizationInputDTO) -> str:
    """Build deterministic UTF-8 canonical JSON SHA-256 for repair authority."""

    encoded = json.dumps(
        repair_authorization_payload(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class RepairRequestV2DTO:
    operation_id: str
    task_id: str
    execution_id: str
    raw_record_id: str
    raw_content_hash: str
    context_hash: str
    original_result: ExtractionResultDTO
    validator_errors: tuple[ValidationErrorDTO, ...]
    content: ContentInputDTO
    clean_content_hash: str
    assets: tuple[str, ...]
    allowed_event_taxonomy: tuple[str, ...]
    repair_authorization_hash: str
    output_schema_version: str
    guardrail_policy_version: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION_V2

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION_V2, "unsupported schema_version")
        _identifier(self.operation_id, _OPERATION, "operation_id")
        _identifier(self.task_id, _TASK, "task_id")
        _identifier(self.execution_id, _EXECUTION, "execution_id")
        _identifier(self.raw_record_id, _RAW, "raw_record_id", prefix=True)
        _identifier(self.raw_content_hash, _HASH, "raw_content_hash")
        _identifier(self.context_hash, _HASH, "context_hash")
        _require(
            isinstance(self.original_result, ExtractionResultDTO)
            and self.original_result.raw_record_id == self.raw_record_id,
            "invalid original_result",
        )
        errors = tuple(self.validator_errors)
        _require(
            1 <= len(errors) <= 100
            and all(isinstance(item, ValidationErrorDTO) for item in errors),
            "invalid validator_errors",
        )
        object.__setattr__(self, "validator_errors", errors)
        _require(
            isinstance(self.content, (InlineContentInputDTO, LocatorContentInputDTO)),
            "invalid content",
        )
        _identifier(self.clean_content_hash, _HASH, "clean_content_hash")
        object.__setattr__(self, "assets", _request_assets(self.assets))
        object.__setattr__(
            self,
            "allowed_event_taxonomy",
            _request_taxonomy(self.allowed_event_taxonomy),
        )
        _identifier(
            self.repair_authorization_hash,
            _HASH,
            "repair_authorization_hash",
        )
        _require(self.output_schema_version == OUTPUT_SCHEMA_VERSION, "invalid output_schema_version")
        _identifier(
            self.guardrail_policy_version,
            _POLICY,
            "guardrail_policy_version",
        )
        _require(
            isinstance(self.deadline, DeadlineDTO)
            and self.deadline.operation_id == self.operation_id,
            "deadline operation_id mismatch",
        )

    def to_wire(self) -> dict[str, object]:
        return {field.name: _wire(getattr(self, field.name)) for field in fields(self)}

    def expected_authorization_hash(
        self,
        *,
        authorization_ruleset_version: str = REPAIR_AUTHORIZATION_RULESET_VERSION,
    ) -> str:
        return build_repair_authorization_hash(
            RepairAuthorizationInputDTO(
                authorization_ruleset_version=authorization_ruleset_version,
                raw_record_id=self.raw_record_id,
                raw_content_hash=self.raw_content_hash,
                clean_content_hash=self.clean_content_hash,
                assets=self.assets,
                allowed_event_taxonomy=self.allowed_event_taxonomy,
                output_schema_version=self.output_schema_version,
                guardrail_policy_version=self.guardrail_policy_version,
            )
        )


__all__ = (
    "OUTPUT_SCHEMA_VERSION",
    "REPAIR_AUTHORIZATION_RULESET_VERSION",
    "REPAIR_V2_ERROR_CODES",
    "SCHEMA_VERSION_V2",
    "RepairAuthorizationInputDTO",
    "RepairRequestV2DTO",
    "build_repair_authorization_hash",
    "repair_authorization_payload",
)
