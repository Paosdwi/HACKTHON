"""Application-owned Ports for repository, event, and time boundaries."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from crypto_trust_agent.application.dto.common import ErrorResultDTO
from crypto_trust_agent.application.dto.repositories import (
    AcquireExecutionRequestDTO,
    AppendAssessmentsRequestDTO,
    AppendClaimLinksRequestDTO,
    AppendEvidenceRequestDTO,
    AppendPreflightResultRequestDTO,
    AppendResultDTO,
    ArtifactContentDTO,
    ArtifactDescriptorDTO,
    ArtifactExecutionRequestDTO,
    ArtifactKeyRequestDTO,
    ArtifactListDTO,
    ArtifactManifestDTO,
    ArtifactPutRequestDTO,
    ClockReadRequestDTO,
    ConsumePreflightSlotRequestDTO,
    ConsumeTaskCreateSlotRequestDTO,
    CreateOrGetTaskRequestDTO,
    CreateOrGetTaskResultDTO,
    EvidenceDTO,
    EvidencePageDTO,
    ExecutionQuotaViewDTO,
    ExecutionRecordDTO,
    GetEvidenceRequestDTO,
    GetExecutionRequestDTO,
    GetLatestAssessmentsRequestDTO,
    GetLatestPreflightRequestDTO,
    LatestAssessmentsDTO,
    ListByQuotaScopeRequestDTO,
    ListEvidenceRequestDTO,
    ManualCaseDTO,
    MonotonicInstantDTO,
    PreflightRecordDTO,
    PublishBatchReceiptDTO,
    PublishBatchRequestDTO,
    PublishEventRequestDTO,
    PublishReceiptDTO,
    PutManifestRequestDTO,
    RateLimitDecisionDTO,
    RecordManualCaseRequestDTO,
    TaskQueryRequestDTO,
    TaskRecordDTO,
    TransitionExecutionRequestDTO,
    UtcInstantDTO,
)


@runtime_checkable
class TaskRepository(Protocol):
    def consume_task_create_slot(self, request: ConsumeTaskCreateSlotRequestDTO) -> RateLimitDecisionDTO | ErrorResultDTO: ...
    def create_or_get(self, request: CreateOrGetTaskRequestDTO) -> CreateOrGetTaskResultDTO | ErrorResultDTO: ...
    def get(self, request: TaskQueryRequestDTO) -> TaskRecordDTO | ErrorResultDTO: ...
    def consume_preflight_slot(self, request: ConsumePreflightSlotRequestDTO) -> RateLimitDecisionDTO | ErrorResultDTO: ...
    def append_preflight_result(self, request: AppendPreflightResultRequestDTO) -> PreflightRecordDTO | ErrorResultDTO: ...
    def get_latest_preflight(self, request: GetLatestPreflightRequestDTO) -> PreflightRecordDTO | ErrorResultDTO: ...


@runtime_checkable
class ExecutionRepository(Protocol):
    def acquire_quota_and_create(self, request: AcquireExecutionRequestDTO) -> ExecutionRecordDTO | ErrorResultDTO: ...
    def get(self, request: GetExecutionRequestDTO) -> ExecutionRecordDTO | ErrorResultDTO: ...
    def transition(self, request: TransitionExecutionRequestDTO) -> ExecutionRecordDTO | ErrorResultDTO: ...
    def record_manual_case(self, request: RecordManualCaseRequestDTO) -> ManualCaseDTO | ErrorResultDTO: ...
    def list_by_quota_scope(self, request: ListByQuotaScopeRequestDTO) -> ExecutionQuotaViewDTO | ErrorResultDTO: ...


@runtime_checkable
class EvidenceRepository(Protocol):
    def append_evidence(self, request: AppendEvidenceRequestDTO) -> EvidenceDTO | ErrorResultDTO: ...
    def append_claim_links(self, request: AppendClaimLinksRequestDTO) -> AppendResultDTO | ErrorResultDTO: ...
    def append_assessments(self, request: AppendAssessmentsRequestDTO) -> AppendResultDTO | ErrorResultDTO: ...
    def get(self, request: GetEvidenceRequestDTO) -> EvidenceDTO | ErrorResultDTO: ...
    def list_for_task(self, request: ListEvidenceRequestDTO) -> EvidencePageDTO | ErrorResultDTO: ...
    def get_latest_assessments(self, request: GetLatestAssessmentsRequestDTO) -> LatestAssessmentsDTO | ErrorResultDTO: ...


@runtime_checkable
class ArtifactRepository(Protocol):
    def put(self, request: ArtifactPutRequestDTO) -> ArtifactDescriptorDTO | ErrorResultDTO: ...
    def get(self, request: ArtifactKeyRequestDTO) -> ArtifactContentDTO | ErrorResultDTO: ...
    def list_for_execution(self, request: ArtifactExecutionRequestDTO) -> ArtifactListDTO | ErrorResultDTO: ...
    def put_manifest(self, request: PutManifestRequestDTO) -> ArtifactDescriptorDTO | ErrorResultDTO: ...
    def get_manifest(self, request: ArtifactExecutionRequestDTO) -> ArtifactManifestDTO | ErrorResultDTO: ...


@runtime_checkable
class EventPublisher(Protocol):
    def publish(self, request: PublishEventRequestDTO) -> PublishReceiptDTO | ErrorResultDTO: ...
    def publish_batch(self, request: PublishBatchRequestDTO) -> PublishBatchReceiptDTO | ErrorResultDTO: ...


@runtime_checkable
class Clock(Protocol):
    def now_utc(self, request: ClockReadRequestDTO) -> UtcInstantDTO | ErrorResultDTO: ...
    def monotonic_ms(self, request: ClockReadRequestDTO) -> MonotonicInstantDTO | ErrorResultDTO: ...
