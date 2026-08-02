"""Frozen MarketRegimeProvider DTOs for contract version 1.0.0."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from datetime import date
from decimal import Decimal
from typing import Any

from crypto_trust_agent.application.dto.common import DeadlineDTO
from crypto_trust_agent.domain.primitives import CanonicalDecimal, ContractValidationError, UtcInstant

SCHEMA_VERSION = "1.0.0"
INFER_ERROR_CODES = (
    "invalid_feature_schema", "feature_alignment_error", "model_version_mismatch",
    "invalid_probability_distribution", "market_regime_timeout", "endpoint_unavailable",
    "provider_rate_limited", "invalid_provider_output", "deadline_exceeded",
    "unexpected_provider_error",
)
HEALTH_ERROR_CODES = (
    "market_regime_timeout", "endpoint_unavailable", "deadline_exceeded",
    "unexpected_provider_error",
)
_OPERATION = re.compile(r"^OP-[A-Z0-9][A-Z0-9._:-]{0,126}$")
_TASK = re.compile(r"^TASK-[A-Za-z0-9._:-]{1,123}$")
_EXECUTION = re.compile(r"^EXEC-[A-Za-z0-9._:-]{1,123}$")
_ASSET = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VERSION = re.compile(r"^[a-z0-9_-]+-[0-9]+\.[0-9]+\.[0-9]+$")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractValidationError(message)


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
class FeatureWindowDTO(WireDTO):
    start: str | date
    end: str | date

    def __post_init__(self) -> None:
        try:
            start = self.start if isinstance(self.start, date) else date.fromisoformat(self.start)
            end = self.end if isinstance(self.end, date) else date.fromisoformat(self.end)
        except (TypeError, ValueError) as error:
            raise ContractValidationError("invalid feature window date") from error
        _require(start <= end, "invalid feature window")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


@dataclass(frozen=True, slots=True)
class FeatureDTO(WireDTO):
    name: str
    value: CanonicalDecimal | str
    calculation_version: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _require(isinstance(self.name, str) and _SAFE.fullmatch(self.name) is not None, "invalid feature name")
        value = self.value if isinstance(self.value, CanonicalDecimal) else CanonicalDecimal(self.value)
        _require(isinstance(self.calculation_version, str) and _VERSION.fullmatch(self.calculation_version) is not None, "invalid calculation_version")
        refs = tuple(self.source_refs)
        _require(1 <= len(refs) <= 100 and len(set(refs)) == len(refs) and all(isinstance(ref, str) and 1 <= len(ref) <= 256 for ref in refs), "invalid source_refs")
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "source_refs", refs)


@dataclass(frozen=True, slots=True)
class ExpectedModelDTO(WireDTO):
    name: str
    contract_version: str

    def __post_init__(self) -> None:
        _require(isinstance(self.name, str) and 1 <= len(self.name) <= 128, "invalid model name")
        _require(self.contract_version == SCHEMA_VERSION, "invalid model contract_version")


@dataclass(frozen=True, slots=True)
class InferRequestDTO(WireDTO):
    operation_id: str
    task_id: str
    execution_id: str
    asset: str
    as_of: str | UtcInstant
    feature_window: FeatureWindowDTO
    features: tuple[FeatureDTO, ...]
    input_feature_hash: str
    expected_model: ExpectedModelDTO
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _require(isinstance(self.operation_id, str) and _OPERATION.fullmatch(self.operation_id) is not None, "invalid operation_id")
        _require(isinstance(self.task_id, str) and _TASK.fullmatch(self.task_id) is not None, "invalid task_id")
        _require(isinstance(self.execution_id, str) and _EXECUTION.fullmatch(self.execution_id) is not None, "invalid execution_id")
        _require(isinstance(self.asset, str) and _ASSET.fullmatch(self.asset) is not None, "invalid asset")
        as_of = self.as_of if isinstance(self.as_of, UtcInstant) else UtcInstant(self.as_of)
        _require(isinstance(self.feature_window, FeatureWindowDTO), "invalid feature_window")
        features = tuple(self.features)
        _require(1 <= len(features) <= 256 and all(isinstance(item, FeatureDTO) for item in features), "invalid features")
        _require(isinstance(self.input_feature_hash, str) and _HASH.fullmatch(self.input_feature_hash) is not None, "invalid input_feature_hash")
        _require(isinstance(self.expected_model, ExpectedModelDTO), "invalid expected_model")
        _require(isinstance(self.deadline, DeadlineDTO) and self.deadline.operation_id == self.operation_id, "deadline operation_id mismatch")
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "features", features)


@dataclass(frozen=True, slots=True)
class ModelDTO(WireDTO):
    name: str
    version: str
    invocation_id: str

    def __post_init__(self) -> None:
        for name, value in (("name", self.name), ("version", self.version), ("invocation_id", self.invocation_id)):
            _require(isinstance(value, str) and 1 <= len(value) <= 128, f"invalid model {name}")


@dataclass(frozen=True, slots=True)
class ProbabilityDistributionDTO(WireDTO):
    bullish: CanonicalDecimal | str
    bearish: CanonicalDecimal | str
    sideways: CanonicalDecimal | str

    def __post_init__(self) -> None:
        values = []
        for name in ("bullish", "bearish", "sideways"):
            raw = getattr(self, name)
            value = raw if isinstance(raw, CanonicalDecimal) else CanonicalDecimal(raw)
            values.append(value.require_probability())
            object.__setattr__(self, name, value)
        total = sum((value.as_decimal() for value in values), Decimal("0"))
        _require(abs(total - Decimal("1")) <= Decimal("0.000001"), "invalid probability distribution")


@dataclass(frozen=True, slots=True)
class MarketRegimeResultDTO(WireDTO):
    operation_id: str
    asset: str
    as_of: str | UtcInstant
    model: ModelDTO
    probabilities: ProbabilityDistributionDTO
    anomaly_score: CanonicalDecimal | str
    feature_window: FeatureWindowDTO
    input_feature_hash: str
    computed_at: str | UtcInstant
    quality: str
    limitations: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _require(isinstance(self.operation_id, str) and _OPERATION.fullmatch(self.operation_id) is not None, "invalid operation_id")
        _require(isinstance(self.asset, str) and _ASSET.fullmatch(self.asset) is not None, "invalid asset")
        _require(isinstance(self.model, ModelDTO) and isinstance(self.probabilities, ProbabilityDistributionDTO), "invalid model result")
        anomaly = self.anomaly_score if isinstance(self.anomaly_score, CanonicalDecimal) else CanonicalDecimal(self.anomaly_score)
        anomaly.require_probability()
        _require(isinstance(self.feature_window, FeatureWindowDTO), "invalid feature_window")
        _require(isinstance(self.input_feature_hash, str) and _HASH.fullmatch(self.input_feature_hash) is not None, "invalid input_feature_hash")
        _require(self.quality in {"valid", "degraded_input"}, "invalid quality")
        limitations = tuple(self.limitations)
        _require(len(limitations) <= 50 and all(isinstance(item, str) and 1 <= len(item) <= 512 for item in limitations), "invalid limitations")
        object.__setattr__(self, "as_of", self.as_of if isinstance(self.as_of, UtcInstant) else UtcInstant(self.as_of))
        object.__setattr__(self, "anomaly_score", anomaly)
        object.__setattr__(self, "computed_at", self.computed_at if isinstance(self.computed_at, UtcInstant) else UtcInstant(self.computed_at))
        object.__setattr__(self, "limitations", limitations)


@dataclass(frozen=True, slots=True)
class HealthCheckRequestDTO(WireDTO):
    operation_id: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _require(isinstance(self.operation_id, str) and _OPERATION.fullmatch(self.operation_id) is not None, "invalid operation_id")
        _require(isinstance(self.deadline, DeadlineDTO) and self.deadline.operation_id == self.operation_id, "deadline operation_id mismatch")


__all__ = (
    "ExpectedModelDTO", "FeatureDTO", "FeatureWindowDTO", "HEALTH_ERROR_CODES",
    "HealthCheckRequestDTO", "INFER_ERROR_CODES", "InferRequestDTO",
    "MarketRegimeResultDTO", "ModelDTO", "ProbabilityDistributionDTO",
)
