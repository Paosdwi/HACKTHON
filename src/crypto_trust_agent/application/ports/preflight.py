"""Application-owned readiness boundaries used by Core pre-flight.

The provider health DTOs and request shapes mirror the frozen 1.0.0 contracts.
Local task/dataset readiness is intentionally a separate side-effect-free Port.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.domain.primitives import ContractValidationError, UtcInstant

_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_OPERATION = re.compile(r"^OP-[A-Z0-9][A-Z0-9._:-]{0,126}$")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_STATUSES = {"healthy", "degraded", "unhealthy", "not_configured"}


def _safe(value: str, name: str) -> None:
    if not isinstance(value, str) or not _SAFE_CODE.fullmatch(value):
        raise ContractValidationError(f"invalid {name}")


def _operation(value: str) -> None:
    if not isinstance(value, str) or not _OPERATION.fullmatch(value):
        raise ContractValidationError("invalid operation_id")


def _request(operation_id: str, deadline: DeadlineDTO, schema_version: str) -> None:
    if schema_version != "1.0.0":
        raise ContractValidationError("unsupported schema_version")
    _operation(operation_id)
    if deadline.operation_id != operation_id:
        raise ContractValidationError("deadline operation_id mismatch")


@dataclass(frozen=True, slots=True)
class HealthCheckRequestDTO:
    operation_id: str
    deadline: DeadlineDTO
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        _request(self.operation_id, self.deadline, self.schema_version)


@dataclass(frozen=True, slots=True)
class ReasoningHealthCheckRequestDTO(HealthCheckRequestDTO):
    model_role: str = "primary"

    def __post_init__(self) -> None:
        HealthCheckRequestDTO.__post_init__(self)
        if self.model_role not in {"primary", "fallback"}:
            raise ContractValidationError("invalid model_role")


@dataclass(frozen=True, slots=True)
class CollectorHealthCheckRequestDTO(HealthCheckRequestDTO):
    provider: str = "external_allowlist"

    def __post_init__(self) -> None:
        HealthCheckRequestDTO.__post_init__(self)
        _safe(self.provider, "provider")


@dataclass(frozen=True, slots=True)
class ProviderHealthDTO:
    provider: str
    capability: str
    status: str
    checked_at: str | UtcInstant
    latency_ms: int
    safe_reason_code: str | None
    expires_at: str | UtcInstant | None
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise ContractValidationError("unsupported schema_version")
        _safe(self.provider, "provider")
        _safe(self.capability, "capability")
        if self.status not in _STATUSES:
            raise ContractValidationError("invalid health status")
        if type(self.latency_ms) is not int or not 0 <= self.latency_ms <= 30_000:
            raise ContractValidationError("invalid latency_ms")
        if self.safe_reason_code is not None:
            _safe(self.safe_reason_code, "safe_reason_code")
        object.__setattr__(self, "checked_at", self.checked_at if isinstance(self.checked_at, UtcInstant) else UtcInstant(self.checked_at))
        if self.expires_at is not None and not isinstance(self.expires_at, UtcInstant):
            object.__setattr__(self, "expires_at", UtcInstant(self.expires_at))

    def to_wire(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "provider": self.provider,
            "capability": self.capability,
            "status": self.status,
            "checked_at": str(self.checked_at),
            "latency_ms": self.latency_ms,
            "safe_reason_code": self.safe_reason_code,
            "expires_at": None if self.expires_at is None else str(self.expires_at),
        }


@dataclass(frozen=True, slots=True)
class TaskReadinessRequestDTO:
    operation_id: str
    task_id: str
    task_version: int
    request_fingerprint: str
    deadline: DeadlineDTO
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        _request(self.operation_id, self.deadline, self.schema_version)
        if not self.task_id.startswith("TASK-") or type(self.task_version) is not int or self.task_version < 1:
            raise ContractValidationError("invalid task readiness identity")
        if not _HASH.fullmatch(self.request_fingerprint):
            raise ContractValidationError("invalid request fingerprint")


@dataclass(frozen=True, slots=True)
class TaskReadinessDTO:
    input_lock_hash: str
    input_status: str
    input_capability_version: str
    input_safe_reason_code: str | None
    dataset_status: str
    dataset_capability_version: str
    dataset_safe_reason_code: str | None
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0" or not _HASH.fullmatch(self.input_lock_hash):
            raise ContractValidationError("invalid task readiness result")
        for status in (self.input_status, self.dataset_status):
            if status not in _STATUSES:
                raise ContractValidationError("invalid readiness status")
        for version in (self.input_capability_version, self.dataset_capability_version):
            if not isinstance(version, str) or not 1 <= len(version) <= 128:
                raise ContractValidationError("invalid capability version")
        for reason in (self.input_safe_reason_code, self.dataset_safe_reason_code):
            if reason is not None:
                _safe(reason, "safe_reason_code")


@runtime_checkable
class HealthCheckPort(Protocol):
    def health_check(self, request: object) -> ProviderHealthDTO | ErrorResultDTO: ...


@runtime_checkable
class TaskReadinessProbe(Protocol):
    def check(self, request: TaskReadinessRequestDTO) -> TaskReadinessDTO | ErrorResultDTO: ...


__all__ = (
    "CollectorHealthCheckRequestDTO",
    "HealthCheckPort",
    "HealthCheckRequestDTO",
    "ProviderHealthDTO",
    "ReasoningHealthCheckRequestDTO",
    "TaskReadinessDTO",
    "TaskReadinessProbe",
    "TaskReadinessRequestDTO",
)
