"""Frozen DTOs for the Core repository, event, and clock Ports.

The field names and wire values mirror contract set 1.0.0.  Infrastructure
adapters may serialize these DTOs, but must not replace them with vendor types.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from crypto_trust_agent.application.dto.common import DeadlineDTO
from crypto_trust_agent.domain.primitives import CanonicalDecimal, ContractValidationError, UtcInstant


SCHEMA_VERSION = "1.0.0"
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_OPERATION = re.compile(r"^OP-[A-Z0-9][A-Z0-9._:-]{0,126}$")
_ARTIFACT_TRIPLES = {
    ("final_report", "json", "application/json"),
    ("evidence_list", "json", "application/json"),
    ("execution_log", "jsonl", "application/x-ndjson"),
    ("manifest", "json", "application/json"),
    ("markdown_report", "markdown", "text/markdown"),
    ("html_report", "html", "text/html"),
    ("csv_evidence", "csv", "text/csv"),
}
_ARTIFACT_PAIRS = {(kind, format_) for kind, format_, _ in _ARTIFACT_TRIPLES}


def _require(value: str, prefix: str, name: str) -> None:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ContractValidationError(f"invalid {name}")


def _require_hash(value: str) -> None:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ContractValidationError("invalid sha256")


def _require_operation(value: str) -> None:
    if not isinstance(value, str) or not _OPERATION.fullmatch(value):
        raise ContractValidationError("invalid operation_id")


def _utc(value: str | UtcInstant | None) -> UtcInstant | None:
    if value is None or isinstance(value, UtcInstant):
        return value
    return UtcInstant(value)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _wire(value: Any) -> Any:
    if isinstance(value, WireDTO):
        return value.to_wire()
    if isinstance(value, DeadlineDTO):
        return value.to_wire()
    if isinstance(value, UtcInstant):
        return str(value)
    if isinstance(value, CanonicalDecimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    return value


class WireDTO:
    """Common exact-wire serializer for frozen dataclass DTOs."""

    def __post_init__(self) -> None:
        if getattr(self, "schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
            raise ContractValidationError("unsupported schema_version")
        operation_id = getattr(self, "operation_id", None)
        if operation_id is not None:
            _require_operation(operation_id)
            deadline = getattr(self, "deadline", None)
            if deadline is not None and deadline.operation_id != operation_id:
                raise ContractValidationError("deadline operation_id mismatch")

    def to_wire(self) -> dict[str, object]:
        return {field.name: _wire(getattr(self, field.name)) for field in fields(self)}


@dataclass(frozen=True, slots=True)
class ClockReadRequestDTO(WireDTO):
    operation_id: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        WireDTO.__post_init__(self)
        _require_operation(self.operation_id)
        if self.schema_version != SCHEMA_VERSION:
            raise ContractValidationError("unsupported schema_version")


@dataclass(frozen=True, slots=True)
class UtcInstantDTO(WireDTO):
    utc: str | UtcInstant
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "utc", _utc(self.utc))


@dataclass(frozen=True, slots=True)
class MonotonicInstantDTO(WireDTO):
    runtime_id: str
    monotonic_ms: int
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if len(self.runtime_id) < 16 or type(self.monotonic_ms) is not int or self.monotonic_ms < 0:
            raise ContractValidationError("invalid monotonic instant")


@dataclass(frozen=True, slots=True)
class TaskRecordDTO(WireDTO):
    task_id: str
    trusted_user_scope: str
    request_fingerprint: str
    state: str
    version: int
    created_at: str | UtcInstant
    locked_execution_id: str | None = None
    fingerprint_ruleset_version: str = "fingerprint-1.0.0"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.task_id, "TASK-", "task_id")
        _require_hash(self.request_fingerprint)
        if self.state not in {"ready_for_preflight", "preflight_failed", "ready_for_execution", "execution_locked", "closed"}:
            raise ContractValidationError("invalid task state")
        if type(self.version) is not int or self.version < 1:
            raise ContractValidationError("invalid task version")
        if self.locked_execution_id is not None:
            _require(self.locked_execution_id, "EXEC-", "locked_execution_id")
        object.__setattr__(self, "created_at", _utc(self.created_at))


@dataclass(frozen=True, slots=True)
class RateLimitDecisionDTO(WireDTO):
    allowed: bool
    scope: str
    limit: int
    remaining: int
    window_seconds: int
    retry_after_seconds: int
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class CreateOrGetTaskRequestDTO(WireDTO):
    operation_id: str
    trusted_user_scope: str
    principal_subject_hash: str
    request_fingerprint: str
    idempotency_window_started_at: str | UtcInstant
    proposed_task: Mapping[str, object]
    deadline: DeadlineDTO
    fingerprint_ruleset_version: str = "fingerprint-1.0.0"
    idempotency_window_seconds: int = 86_400
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        WireDTO.__post_init__(self)
        _require_operation(self.operation_id)
        _require_hash(self.request_fingerprint)
        if len(self.principal_subject_hash) < 16 or self.idempotency_window_seconds != 86_400:
            raise ContractValidationError("invalid create-or-get request")
        object.__setattr__(self, "idempotency_window_started_at", _utc(self.idempotency_window_started_at))
        object.__setattr__(self, "proposed_task", _freeze(self.proposed_task))


@dataclass(frozen=True, slots=True)
class CreateOrGetTaskResultDTO(WireDTO):
    outcome: str
    task: TaskRecordDTO
    window_expires_at: str | UtcInstant
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.outcome not in {"created", "reused"}:
            raise ContractValidationError("invalid create-or-get outcome")
        object.__setattr__(self, "window_expires_at", _utc(self.window_expires_at))


@dataclass(frozen=True, slots=True)
class ConsumeTaskCreateSlotRequestDTO(WireDTO):
    operation_id: str
    trusted_user_scope: str
    principal_subject_hash: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class TaskQueryRequestDTO(WireDTO):
    operation_id: str
    trusted_user_scope: str
    task_id: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ConsumePreflightSlotRequestDTO(TaskQueryRequestDTO):
    pass


@dataclass(frozen=True, slots=True)
class PreflightRecordDTO(WireDTO):
    preflight_id: str
    task_id: str
    task_version: int
    input_lock_hash: str
    dependency_snapshot_hash: str
    checked_at: str | UtcInstant
    expires_at: str | UtcInstant
    ready: bool
    checks: tuple[Mapping[str, object], ...]
    dependency_snapshot: Mapping[str, object]
    consumed_at: str | UtcInstant | None = None
    consumed_by_execution_id: str | None = None
    ttl_seconds: int = 60
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.preflight_id, "PF-", "preflight_id")
        _require(self.task_id, "TASK-", "task_id")
        _require_hash(self.input_lock_hash)
        _require_hash(self.dependency_snapshot_hash)
        checked = _utc(self.checked_at)
        expires = _utc(self.expires_at)
        if self.ttl_seconds != 60 or (expires.as_datetime() - checked.as_datetime()).total_seconds() != 60:
            raise ContractValidationError("preflight expiry must equal checked_at plus 60 seconds")
        if (self.consumed_at is None) != (self.consumed_by_execution_id is None):
            raise ContractValidationError("preflight consumption fields must be paired")
        object.__setattr__(self, "checked_at", checked)
        object.__setattr__(self, "expires_at", expires)
        object.__setattr__(self, "consumed_at", _utc(self.consumed_at))
        object.__setattr__(self, "checks", tuple(_freeze(item) for item in self.checks))
        object.__setattr__(self, "dependency_snapshot", _freeze(self.dependency_snapshot))


@dataclass(frozen=True, slots=True)
class AppendPreflightResultRequestDTO(WireDTO):
    operation_id: str
    expected_task_version: int
    record: PreflightRecordDTO
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class GetLatestPreflightRequestDTO(WireDTO):
    operation_id: str
    task_id: str
    task_version: int
    input_lock_hash: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class AdminAuthorizationDTO(WireDTO):
    authorization_id: str
    verified_admin_subject_hash: str
    verified_at: str | UtcInstant
    reason: str

    def __post_init__(self) -> None:
        _require(self.authorization_id, "AUTH-", "authorization_id")
        object.__setattr__(self, "verified_at", _utc(self.verified_at))


@dataclass(frozen=True, slots=True)
class ExecutionRecordDTO(WireDTO):
    execution_id: str
    task_id: str
    request_fingerprint: str
    attempt_number: int
    attempt_kind: str
    original_execution_id: str | None
    technical_failure_code: str | None
    state: str
    outcome: str
    absolute_deadline_at: str | UtcInstant
    started_at: str | UtcInstant
    updated_at: str | UtcInstant
    completed_at: str | UtcInstant | None
    partial_reason_codes: tuple[str, ...]
    failure_reason_code: str | None
    version: int
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.execution_id, "EXEC-", "execution_id")
        _require(self.task_id, "TASK-", "task_id")
        _require_hash(self.request_fingerprint)
        object.__setattr__(self, "absolute_deadline_at", _utc(self.absolute_deadline_at))
        object.__setattr__(self, "started_at", _utc(self.started_at))
        object.__setattr__(self, "updated_at", _utc(self.updated_at))
        object.__setattr__(self, "completed_at", _utc(self.completed_at))
        object.__setattr__(self, "partial_reason_codes", tuple(self.partial_reason_codes))


@dataclass(frozen=True, slots=True)
class AcquireExecutionRequestDTO(WireDTO):
    operation_id: str
    trusted_user_scope: str
    request_fingerprint: str
    task_id: str
    expected_task_version: int
    input_lock_hash: str
    dependency_snapshot_hash: str
    execution_id: str
    preflight_id: str
    attempt_kind: str
    started_at: str | UtcInstant
    absolute_deadline_at: str | UtcInstant
    deadline: DeadlineDTO
    admin_authorization: AdminAuthorizationDTO | None = None
    original_execution_id: str | None = None
    technical_failure_code: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        WireDTO.__post_init__(self)
        _require_operation(self.operation_id)
        _require_hash(self.request_fingerprint)
        _require_hash(self.input_lock_hash)
        _require_hash(self.dependency_snapshot_hash)
        for value, prefix, name in ((self.task_id, "TASK-", "task_id"), (self.execution_id, "EXEC-", "execution_id"), (self.preflight_id, "PF-", "preflight_id")):
            _require(value, prefix, name)
        if self.attempt_kind == "user_initial":
            if self.admin_authorization or self.original_execution_id or self.technical_failure_code:
                raise ContractValidationError("initial attempt cannot carry rerun fields")
        elif self.attempt_kind == "admin_technical_rerun":
            if not self.admin_authorization or not self.original_execution_id or self.technical_failure_code not in {"provider_timeout", "provider_unavailable", "invalid_provider_output", "publication_failure", "platform_failure"}:
                raise ContractValidationError("invalid admin rerun")
        else:
            raise ContractValidationError("invalid attempt_kind")
        object.__setattr__(self, "started_at", _utc(self.started_at))
        object.__setattr__(self, "absolute_deadline_at", _utc(self.absolute_deadline_at))


@dataclass(frozen=True, slots=True)
class GetExecutionRequestDTO(WireDTO):
    operation_id: str
    trusted_user_scope: str
    execution_id: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class TransitionExecutionRequestDTO(WireDTO):
    operation_id: str
    execution_id: str
    expected_version: int
    from_state: str
    to_state: str
    occurred_at: str | UtcInstant
    deadline: DeadlineDTO
    safe_reason_code: str | None = None
    partial_reason_codes: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        WireDTO.__post_init__(self)
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at))
        object.__setattr__(self, "partial_reason_codes", tuple(self.partial_reason_codes))


@dataclass(frozen=True, slots=True)
class RecordManualCaseRequestDTO(WireDTO):
    operation_id: str
    execution_id: str
    expected_version: int
    reason_code: str
    created_at: str | UtcInstant
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ManualCaseDTO(WireDTO):
    manual_case_id: str
    execution_id: str
    task_id: str
    reason_code: str
    created_at: str | UtcInstant
    status: str = "open"
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ListByQuotaScopeRequestDTO(WireDTO):
    operation_id: str
    trusted_user_scope: str
    request_fingerprint: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ExecutionQuotaViewDTO(WireDTO):
    trusted_user_scope: str
    request_fingerprint: str
    initial_used: bool
    admin_rerun_used: bool
    manual_case_required: bool
    executions: tuple[ExecutionRecordDTO, ...]
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class EvidenceDTO(WireDTO):
    evidence_id: str
    task_id: str
    execution_id: str
    raw_record_id: str
    source_name: str
    source_type: str
    source_url: str | None
    published_at: str | UtcInstant | None
    fetched_at: str | UtcInstant
    content_reference: Mapping[str, object]
    raw_locator: str
    raw_content_hash: str
    clean_content_hash: str
    query_provenance: Mapping[str, object]
    validation_status: str
    created_at: str | UtcInstant
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.evidence_id, "EVID-", "evidence_id")
        _require_hash(self.raw_content_hash)
        _require_hash(self.clean_content_hash)
        if self.source_type != "dataset" and (self.source_url is None or not self.source_url.startswith("https://")):
            raise ContractValidationError("non-dataset evidence requires HTTPS source_url")
        reference = dict(self.content_reference)
        offset = reference.get("offset")
        unit = reference.get("unit")
        if (offset is None) != (unit is None):
            raise ContractValidationError("offset and unit must be paired")
        if offset is not None and int(offset["end"]) <= int(offset["start"]):
            raise ContractValidationError("content reference must be half-open")
        object.__setattr__(self, "published_at", _utc(self.published_at))
        object.__setattr__(self, "fetched_at", _utc(self.fetched_at))
        object.__setattr__(self, "created_at", _utc(self.created_at))
        object.__setattr__(self, "content_reference", _freeze(self.content_reference))
        object.__setattr__(self, "query_provenance", _freeze(self.query_provenance))


@dataclass(frozen=True, slots=True)
class EvidenceClaimLinkDTO(WireDTO):
    link_id: str
    task_id: str
    evidence_id: str
    claim_id: str
    stance: str
    created_at: str | UtcInstant
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class EvidenceAssessmentDTO(WireDTO):
    assessment_id: str
    task_id: str
    evidence_id: str
    assessment_sequence: int
    assessment_version: str
    ruleset_version: str
    source_trust: CanonicalDecimal | str
    relevance: CanonicalDecimal | str
    freshness: CanonicalDecimal | str
    independence: CanonicalDecimal | str
    independence_group: str
    consistency: CanonicalDecimal | str
    overall_confidence: CanonicalDecimal | str
    contradiction_severity: str
    computed_at: str | UtcInstant
    limitations: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.assessment_sequence < 1:
            raise ContractValidationError("invalid assessment sequence")
        for name in ("source_trust", "relevance", "freshness", "independence", "consistency", "overall_confidence"):
            value = getattr(self, name)
            decimal = value if isinstance(value, CanonicalDecimal) else CanonicalDecimal(value)
            decimal.require_probability()
            object.__setattr__(self, name, decimal)
        object.__setattr__(self, "computed_at", _utc(self.computed_at))
        object.__setattr__(self, "limitations", tuple(self.limitations))


@dataclass(frozen=True, slots=True)
class AppendEvidenceRequestDTO(WireDTO):
    operation_id: str
    expected_task_id: str
    evidence: EvidenceDTO
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class AppendClaimLinksRequestDTO(WireDTO):
    operation_id: str
    expected_task_id: str
    items: tuple[EvidenceClaimLinkDTO, ...]
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class AppendAssessmentsRequestDTO(WireDTO):
    operation_id: str
    expected_task_id: str
    items: tuple[EvidenceAssessmentDTO, ...]
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class AppendResultItemDTO(WireDTO):
    item_id: str
    outcome: str


@dataclass(frozen=True, slots=True)
class AppendResultDTO(WireDTO):
    items: tuple[AppendResultItemDTO, ...]
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class EvidencePageDTO(WireDTO):
    task_id: str
    items: tuple[EvidenceDTO, ...]
    snapshot_token: str
    next_cursor: str | None
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class LatestAssessmentsDTO(WireDTO):
    task_id: str
    items: tuple[EvidenceAssessmentDTO, ...]
    snapshot_token: str
    selection_ruleset_version: str = "assessment-selection-1.0.0"
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ArtifactPutRequestDTO(WireDTO):
    operation_id: str
    task_id: str
    execution_id: str
    artifact_type: str
    format: str
    mime_type: str
    content_schema_version: str
    sha256: str
    size_bytes: int
    generated_at: str | UtcInstant
    content_base64: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        WireDTO.__post_init__(self)
        if (self.artifact_type, self.format, self.mime_type) not in _ARTIFACT_TRIPLES or self.artifact_type == "manifest":
            raise ContractValidationError("invalid non-manifest artifact triple")
        _require_hash(self.sha256)
        object.__setattr__(self, "generated_at", _utc(self.generated_at))

    def with_operation(self, operation_id: str, **changes: object) -> ArtifactPutRequestDTO:
        changes.setdefault(
            "deadline",
            DeadlineDTO(
                schema_version=self.deadline.schema_version,
                operation_id=operation_id,
                deadline_at_utc=self.deadline.deadline_at_utc,
                budget_ms=self.deadline.budget_ms,
                sent_at_utc=self.deadline.sent_at_utc,
                safety_margin_ms=self.deadline.safety_margin_ms,
            ),
        )
        return replace(self, operation_id=operation_id, **changes)


@dataclass(frozen=True, slots=True)
class ArtifactDescriptorDTO(WireDTO):
    artifact_id: str
    task_id: str
    execution_id: str
    artifact_type: str
    format: str
    mime_type: str
    content_schema_version: str
    locator: str
    sha256: str
    size_bytes: int
    generated_at: str | UtcInstant
    stored_at: str | UtcInstant
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (self.artifact_type, self.format, self.mime_type) not in _ARTIFACT_TRIPLES:
            raise ContractValidationError("invalid artifact triple")
        _require_hash(self.sha256)
        object.__setattr__(self, "generated_at", _utc(self.generated_at))
        object.__setattr__(self, "stored_at", _utc(self.stored_at))


@dataclass(frozen=True, slots=True)
class ArtifactKeyRequestDTO(WireDTO):
    operation_id: str
    task_id: str
    execution_id: str
    artifact_type: str
    format: str
    content_schema_version: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        WireDTO.__post_init__(self)
        if (self.artifact_type, self.format) not in _ARTIFACT_PAIRS or self.artifact_type == "manifest":
            raise ContractValidationError("invalid non-manifest artifact pair")


@dataclass(frozen=True, slots=True)
class ArtifactContentDTO(WireDTO):
    descriptor: ArtifactDescriptorDTO
    delivery: Mapping[str, object]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "delivery", _freeze(self.delivery))


@dataclass(frozen=True, slots=True)
class ArtifactListDTO(WireDTO):
    task_id: str
    execution_id: str
    items: tuple[ArtifactDescriptorDTO, ...]
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ArtifactManifestDTO(WireDTO):
    task_id: str
    execution_id: str
    publication_outcome: str
    available: tuple[Mapping[str, object], ...]
    missing: tuple[Mapping[str, object], ...]
    generated_at: str | UtcInstant
    manifest_version: str = "1.0.0"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        available = tuple(_freeze(item) for item in self.available)
        missing = tuple(_freeze(item) for item in self.missing)
        pairs = {(str(item["artifact_type"]), str(item["format"])) for item in available}
        required = {("final_report", "json"), ("evidence_list", "json"), ("execution_log", "jsonl")}
        if not required.issubset(pairs) or ("manifest", "json") in pairs or len(pairs) != len(available):
            raise ContractValidationError("manifest minimum bundle is invalid")
        if (self.publication_outcome == "complete" and missing) or (self.publication_outcome == "partial" and not missing):
            raise ContractValidationError("manifest outcome does not match missing entries")
        if self.publication_outcome not in {"complete", "partial"}:
            raise ContractValidationError("invalid publication outcome")
        object.__setattr__(self, "available", available)
        object.__setattr__(self, "missing", missing)
        object.__setattr__(self, "generated_at", _utc(self.generated_at))


@dataclass(frozen=True, slots=True)
class PutManifestRequestDTO(WireDTO):
    operation_id: str
    manifest: ArtifactManifestDTO
    sha256: str
    size_bytes: int
    content_base64: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ArtifactExecutionRequestDTO(WireDTO):
    operation_id: str
    task_id: str
    execution_id: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class EventErrorDTO(WireDTO):
    code: str
    category: str
    retryable: bool
    safe_message: str

    def __post_init__(self) -> None:
        if not _SAFE.fullmatch(self.code):
            raise ContractValidationError("invalid event error code")
        if self.category not in {"validation", "timeout", "unavailable", "integrity", "unexpected"}:
            raise ContractValidationError("invalid event error category")
        if type(self.retryable) is not bool or not 1 <= len(self.safe_message) <= 512:
            raise ContractValidationError("invalid event error")
        if re.search(r"authorization|bearer\s+|token|secret|password|prompt|raw_content", self.safe_message, re.IGNORECASE):
            raise ContractValidationError("unsafe event error message")


@dataclass(frozen=True, slots=True)
class ExecutionEventDTO(WireDTO):
    event_id: str
    timestamp: str | UtcInstant
    task_id: str
    execution_id: str
    step: str
    tool: str
    status: str
    duration_ms: int
    retry_count: int
    sanitized_parameters: Mapping[str, str | int | bool | None]
    result_summary: Mapping[str, str | int | bool | None]
    error: EventErrorDTO | None
    deadline_remaining_ms: int
    correlation: Mapping[str, object]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.event_id, "EVT-", "event_id")
        _require(self.task_id, "TASK-", "task_id")
        _require(self.execution_id, "EXEC-", "execution_id")
        if not re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", self.step):
            raise ContractValidationError("invalid event step")
        if not re.fullmatch(r"^[a-z][a-z0-9_]{0,127}$", self.tool):
            raise ContractValidationError("invalid event tool")
        if self.status not in {"started", "completed", "failed", "skipped", "degraded"}:
            raise ContractValidationError("invalid event status")
        if type(self.duration_ms) is not int or not 0 <= self.duration_ms <= 900_000:
            raise ContractValidationError("invalid event duration")
        if type(self.retry_count) is not int or not 0 <= self.retry_count <= 10:
            raise ContractValidationError("invalid event retry count")
        if type(self.deadline_remaining_ms) is not int or not 0 <= self.deadline_remaining_ms <= 900_000:
            raise ContractValidationError("invalid event deadline remaining")
        correlation = dict(self.correlation)
        if set(correlation) != {"operation_id", "causation_event_id"}:
            raise ContractValidationError("invalid event correlation shape")
        _require_operation(str(correlation["operation_id"]))
        causation_id = correlation["causation_event_id"]
        if causation_id is not None:
            _require(str(causation_id), "EVT-", "causation_event_id")
        forbidden = re.compile(r"authorization|token|secret|password|prompt|raw_content", re.IGNORECASE)
        forbidden_value = re.compile(r"authorization|bearer\s+|token|secret|password|prompt|raw_content", re.IGNORECASE)
        for mapping in (self.sanitized_parameters, self.result_summary):
            if (
                len(mapping) > 64
                or any(forbidden.search(str(key)) for key in mapping)
                or any(isinstance(value, str) and forbidden_value.search(value) for value in mapping.values())
            ):
                raise ContractValidationError("unsafe event map")
        if self.error is not None and not isinstance(self.error, EventErrorDTO):
            raise ContractValidationError("event error must be EventErrorDTO")
        object.__setattr__(self, "timestamp", _utc(self.timestamp))
        object.__setattr__(self, "sanitized_parameters", _freeze(self.sanitized_parameters))
        object.__setattr__(self, "result_summary", _freeze(self.result_summary))
        object.__setattr__(self, "error", self.error)
        object.__setattr__(self, "correlation", _freeze(self.correlation))

    def replace(self, **changes: object) -> ExecutionEventDTO:
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class PublishEventRequestDTO(WireDTO):
    operation_id: str
    event: ExecutionEventDTO
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class PublishBatchRequestDTO(WireDTO):
    operation_id: str
    events: tuple[ExecutionEventDTO, ...]
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class PublishReceiptDTO(WireDTO):
    event_id: str
    outcome: str
    published_at: str | UtcInstant
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "published_at", _utc(self.published_at))


@dataclass(frozen=True, slots=True)
class BatchItemReceiptDTO(WireDTO):
    event_id: str
    outcome: str
    error_code: str | None


@dataclass(frozen=True, slots=True)
class PublishBatchReceiptDTO(WireDTO):
    items: tuple[BatchItemReceiptDTO, ...]
    published_at: str | UtcInstant
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "published_at", _utc(self.published_at))



@dataclass(frozen=True, slots=True)
class GetEvidenceRequestDTO(WireDTO):
    operation_id: str
    task_id: str
    evidence_id: str
    deadline: DeadlineDTO
    include_quarantined: bool = False
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ListEvidenceRequestDTO(WireDTO):
    operation_id: str
    task_id: str
    deadline: DeadlineDTO
    source_type: str | None = None
    validation_status: str | None = None
    snapshot_token: str | None = None
    cursor: str | None = None
    limit: int = 50
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class GetLatestAssessmentsRequestDTO(WireDTO):
    operation_id: str
    task_id: str
    evidence_ids: tuple[str, ...]
    snapshot_token: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION


def _wrap_schema_validation(dto_type: type[WireDTO]) -> None:
    original = dto_type.__dict__.get("__post_init__")
    if original is None:
        return

    def validated(self) -> None:
        WireDTO.__post_init__(self)
        original(self)

    setattr(dto_type, "__post_init__", validated)


for _dto_type in tuple(WireDTO.__subclasses__()):
    _wrap_schema_validation(_dto_type)


__all__ = tuple(name for name in globals() if name.endswith("DTO"))