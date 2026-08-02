"""Thread-safe in-memory repository fakes.

These adapters are deliberately non-production.  Their single-process lock is
useful for contract and use-case tests, but is not a distributed transaction or
persistence design.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import timedelta
from functools import wraps
from threading import RLock

from crypto_trust_agent.application.dto.common import (
    DeadlineExceededError,
    ErrorResultDTO,
    PortErrorCategory,
    PortErrorDTO,
    build_local_deadline,
)
from crypto_trust_agent.application.dto.repositories import (
    AcquireExecutionRequestDTO,
    AppendAssessmentsRequestDTO,
    AppendClaimLinksRequestDTO,
    AppendEvidenceRequestDTO,
    AppendPreflightResultRequestDTO,
    AppendResultDTO,
    AppendResultItemDTO,
    ConsumePreflightSlotRequestDTO,
    ConsumeTaskCreateSlotRequestDTO,
    CreateOrGetTaskRequestDTO,
    CreateOrGetTaskResultDTO,
    EvidenceAssessmentDTO,
    EvidenceClaimLinkDTO,
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
    PreflightRecordDTO,
    RateLimitDecisionDTO,
    RecordManualCaseRequestDTO,
    TaskQueryRequestDTO,
    TaskRecordDTO,
    TransitionExecutionRequestDTO,
)
from crypto_trust_agent.domain.primitives import UtcInstant
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock


_ERROR_CATEGORY = {
    "not_found": PortErrorCategory.NOT_FOUND,
    "conflict": PortErrorCategory.CONFLICT,
    "rate": PortErrorCategory.RATE_LIMITED,
    "quota": PortErrorCategory.QUOTA_EXHAUSTED,
    "integrity": PortErrorCategory.INTEGRITY,
    "validation": PortErrorCategory.VALIDATION,
    "timeout": PortErrorCategory.TIMEOUT,
    "unavailable": PortErrorCategory.UNAVAILABLE,
}


def _error(clock: FakeClock, provider: str, operation_id: str, code: str, kind: str = "conflict") -> ErrorResultDTO:
    return ErrorResultDTO(
        error=PortErrorDTO(
            schema_version="1.0.0",
            code=code,
            category=_ERROR_CATEGORY[kind],
            retryable=False,
            safe_message=code.replace("_", " ").capitalize(),
            provider=provider,
            operation_id=operation_id,
            details={},
            occurred_at=clock.current_utc(),
        )
    )


class FakePlatformStore:
    """Shared state required for fake cross-repository atomicity."""

    non_production = True

    def __init__(self) -> None:
        self.lock = RLock()
        self.tasks: dict[str, TaskRecordDTO] = {}
        self.proposed_tasks: dict[str, object] = {}
        self.task_fingerprint_index: dict[tuple[str, str], tuple[str, UtcInstant]] = {}
        self.preflights: dict[str, list[PreflightRecordDTO]] = {}
        self.executions: dict[str, ExecutionRecordDTO] = {}
        self.execution_scope: dict[str, str] = {}
        self.quota: dict[tuple[str, str], list[str]] = {}
        self.manual_cases: dict[str, ManualCaseDTO] = {}
        self.replays: dict[tuple[str, str], object] = {}
        self.replay_identities: dict[tuple[str, str], object] = {}
        self.task_create_counters: dict[tuple[str, str], list[UtcInstant]] = {}
        self.preflight_counters: dict[str, list[UtcInstant]] = {}


def _guard_deadlines(provider: str, timeouts: dict[str, int]):
    """Apply the contract's receiver-local deadline check before any fake I/O."""

    def decorate(cls):
        for method_name, timeout_ms in timeouts.items():
            original = getattr(cls, method_name)

            @wraps(original)
            def guarded(self, request, *args, __original=original, __timeout=timeout_ms, **kwargs):
                try:
                    build_local_deadline(
                        request.deadline,
                        provider_timeout_ms=__timeout,
                        now_utc=self._clock.current_utc().as_datetime(),
                        now_monotonic_ms=self._clock.current_monotonic_ms(),
                        runtime_id=self._clock.runtime_id,
                    )
                except DeadlineExceededError:
                    return _error(self._clock, provider, request.operation_id, "deadline_exceeded", "timeout")
                return __original(self, request, *args, **kwargs)

            setattr(cls, method_name, guarded)
        return cls

    return decorate


@_guard_deadlines(
    "task_repository",
    {
        "consume_task_create_slot": 2000,
        "create_or_get": 2000,
        "get": 2000,
        "consume_preflight_slot": 2000,
        "append_preflight_result": 2000,
        "get_latest_preflight": 2000,
    },
)
class FakeTaskRepository:
    """Non-production TaskRepository with one-process conditional writes."""

    non_production = True

    def __init__(self, store: FakePlatformStore, clock: FakeClock) -> None:
        self._store = store
        self._clock = clock

    def consume_task_create_slot(self, request: ConsumeTaskCreateSlotRequestDTO) -> RateLimitDecisionDTO | ErrorResultDTO:
        with self._store.lock:
            replay = self._replay("task_create_slot", request.operation_id)
            if replay is not None:
                return replay
            now = self._clock.current_utc()
            key = (request.trusted_user_scope, request.principal_subject_hash)
            cutoff = now.as_datetime() - timedelta(hours=1)
            current = [item for item in self._store.task_create_counters.get(key, []) if item.as_datetime() > cutoff]
            allowed = len(current) < 10
            if allowed:
                current.append(now)
            self._store.task_create_counters[key] = current
            result = RateLimitDecisionDTO(
                allowed=allowed,
                scope=f"task_create:{request.trusted_user_scope}",
                limit=10,
                remaining=max(0, 10 - len(current)),
                window_seconds=3600,
                retry_after_seconds=0 if allowed else 1,
            )
            return self._remember("task_create_slot", request.operation_id, result)

    def create_or_get(self, request: CreateOrGetTaskRequestDTO) -> CreateOrGetTaskResultDTO | ErrorResultDTO:
        with self._store.lock:
            identity = (request.trusted_user_scope, request.request_fingerprint)
            replay = self._replay("task_create", request.operation_id)
            if replay is not None:
                if self._store.replay_identities.get(("task_create", request.operation_id)) != identity:
                    return _error(self._clock, "task_repository", request.operation_id, "fingerprint_index_conflict")
                return replay
            key = identity
            indexed = self._store.task_fingerprint_index.get(key)
            now = self._clock.current_utc()
            if indexed is not None and now.as_datetime() < indexed[1].as_datetime():
                task = self._store.tasks[indexed[0]]
                result = CreateOrGetTaskResultDTO("reused", task, indexed[1])
                return self._remember("task_create", request.operation_id, result, identity)
            proposed = request.proposed_task
            task_id = str(proposed["task_id"])
            if task_id in self._store.tasks:
                result = _error(self._clock, "task_repository", request.operation_id, "fingerprint_index_conflict")
                return self._remember("task_create", request.operation_id, result, identity)
            started = request.idempotency_window_started_at
            expires = UtcInstant((started.as_datetime() + timedelta(seconds=86_400)).isoformat().replace("+00:00", "Z"))
            task = TaskRecordDTO(
                task_id=task_id,
                trusted_user_scope=request.trusted_user_scope,
                request_fingerprint=request.request_fingerprint,
                state="ready_for_preflight",
                version=1,
                created_at=proposed["created_at"],
            )
            self._store.tasks[task_id] = task
            self._store.proposed_tasks[task_id] = proposed
            self._store.task_fingerprint_index[key] = (task_id, expires)
            result = CreateOrGetTaskResultDTO("created", task, expires)
            return self._remember("task_create", request.operation_id, result, identity)

    def get(self, request: TaskQueryRequestDTO) -> TaskRecordDTO | ErrorResultDTO:
        with self._store.lock:
            task = self._store.tasks.get(request.task_id)
            if task is None or task.trusted_user_scope != request.trusted_user_scope:
                return _error(self._clock, "task_repository", request.operation_id, "task_not_found", "not_found")
            return task

    def consume_preflight_slot(self, request: ConsumePreflightSlotRequestDTO) -> RateLimitDecisionDTO | ErrorResultDTO:
        with self._store.lock:
            replay = self._replay("preflight_slot", request.operation_id)
            if replay is not None:
                return replay
            task = self._store.tasks.get(request.task_id)
            if task is None or task.trusted_user_scope != request.trusted_user_scope:
                return _error(self._clock, "task_repository", request.operation_id, "task_not_found", "not_found")
            now = self._clock.current_utc()
            cutoff = now.as_datetime() - timedelta(seconds=60)
            current = [item for item in self._store.preflight_counters.get(request.task_id, []) if item.as_datetime() > cutoff]
            allowed = len(current) < 3
            if allowed:
                current.append(now)
            self._store.preflight_counters[request.task_id] = current
            if allowed or not current:
                retry_after = 0
            else:
                remaining = (current[0].as_datetime() + timedelta(seconds=60) - now.as_datetime()).total_seconds()
                retry_after = max(1, int(remaining) if remaining.is_integer() else int(remaining) + 1)
            result = RateLimitDecisionDTO(allowed, f"preflight:{request.task_id}", 3, max(0, 3 - len(current)), 60, retry_after)
            return self._remember("preflight_slot", request.operation_id, result)

    def append_preflight_result(self, request: AppendPreflightResultRequestDTO) -> PreflightRecordDTO | ErrorResultDTO:
        with self._store.lock:
            replay = self._replay("append_preflight", request.operation_id)
            if replay is not None:
                return replay
            record = request.record
            task = self._store.tasks.get(record.task_id)
            if task is None:
                return _error(self._clock, "task_repository", request.operation_id, "task_not_found", "not_found")
            if (
                task.version != request.expected_task_version
                or record.task_version != task.version
                or task.state not in {"ready_for_preflight", "preflight_failed", "ready_for_execution"}
            ):
                return _error(self._clock, "task_repository", request.operation_id, "task_version_conflict")
            history = self._store.preflights.setdefault(record.task_id, [])
            existing = next((item for item in history if item.preflight_id == record.preflight_id), None)
            if existing is not None:
                if existing == record:
                    return self._remember("append_preflight", request.operation_id, existing)
                return _error(self._clock, "task_repository", request.operation_id, "task_version_conflict")
            history.append(record)
            next_state = "ready_for_execution" if record.ready else "preflight_failed"
            self._store.tasks[task.task_id] = replace(task, state=next_state, version=task.version + 1)
            return self._remember("append_preflight", request.operation_id, record)

    def get_latest_preflight(self, request: GetLatestPreflightRequestDTO) -> PreflightRecordDTO | ErrorResultDTO:
        with self._store.lock:
            candidates = [item for item in self._store.preflights.get(request.task_id, ()) if item.task_version == request.task_version and item.input_lock_hash == request.input_lock_hash]
            if not candidates:
                return _error(self._clock, "task_repository", request.operation_id, "preflight_not_passed", "validation")
            record = candidates[-1]
            if record.consumed_at is not None:
                return record
            if self._clock.current_utc().as_datetime() >= record.expires_at.as_datetime():
                return _error(self._clock, "task_repository", request.operation_id, "preflight_expired", "validation")
            if not record.ready:
                return _error(self._clock, "task_repository", request.operation_id, "preflight_not_passed", "validation")
            return record

    def _replay(self, method: str, operation_id: str) -> object | None:
        return self._store.replays.get((method, operation_id))

    def _remember(self, method: str, operation_id: str, result: object, identity: object | None = None):
        self._store.replays[(method, operation_id)] = result
        if identity is not None:
            self._store.replay_identities[(method, operation_id)] = identity
        return result


@_guard_deadlines(
    "execution_repository",
    {
        "acquire_quota_and_create": 2000,
        "get": 2000,
        "transition": 2000,
        "record_manual_case": 2000,
        "list_by_quota_scope": 2000,
    },
)
class FakeExecutionRepository:
    """Non-production sole authority for fake execution acquisition."""

    non_production = True
    _ACQUIRE_FAULT_STAGES = frozenset(
        {"before_commit", "after_preflight_consume", "after_replay_record"}
    )

    def __init__(
        self,
        store: FakePlatformStore,
        clock: FakeClock,
        *,
        acquire_fault_at: str | None = None,
    ) -> None:
        if acquire_fault_at is not None and acquire_fault_at not in self._ACQUIRE_FAULT_STAGES:
            raise ValueError("unsupported fake acquire fault stage")
        self._store = store
        self._clock = clock
        self._acquire_fault_at = acquire_fault_at
        self._acquire_fault_consumed = False

    def acquire_quota_and_create(self, request: AcquireExecutionRequestDTO) -> ExecutionRecordDTO | ErrorResultDTO:
        with self._store.lock:
            replay_key = ("execution_acquire", request.operation_id)
            # Server-generated timestamps, deadlines, and authorization IDs may change
            # when an at-least-once caller reconstructs the same operation. Keep the
            # replay identity stable while still binding every caller-controlled and
            # quota-relevant field.
            identity = (
                request.operation_id,
                request.execution_id,
                request.trusted_user_scope,
                request.request_fingerprint,
                request.task_id,
                request.expected_task_version,
                request.input_lock_hash,
                request.dependency_snapshot_hash,
                request.preflight_id,
                request.attempt_kind,
                request.original_execution_id,
                request.technical_failure_code,
                None
                if request.admin_authorization is None
                else request.admin_authorization.verified_admin_subject_hash,
            )

            def remember(result: ExecutionRecordDTO | ErrorResultDTO):
                self._store.replay_identities[replay_key] = identity
                self._store.replays[replay_key] = result
                return result

            task = self._store.tasks.get(request.task_id)
            if (
                task is None
                or task.trusted_user_scope != request.trusted_user_scope
                or task.request_fingerprint != request.request_fingerprint
            ):
                return _error(
                    self._clock,
                    "execution_repository",
                    request.operation_id,
                    "preflight_stale",
                    "validation",
                )
            replay = self._store.replays.get(replay_key)
            if replay is not None:
                if self._store.replay_identities.get(replay_key) != identity:
                    return _error(
                        self._clock,
                        "execution_repository",
                        request.operation_id,
                        "preflight_stale",
                        "validation",
                    )
                return replay
            if request.execution_id in self._store.executions:
                return remember(
                    _error(
                        self._clock,
                        "execution_repository",
                        request.operation_id,
                        "preflight_stale",
                        "validation",
                    )
                )
            if task.version != request.expected_task_version or task.state != "ready_for_execution":
                return remember(
                    _error(
                        self._clock,
                        "execution_repository",
                        request.operation_id,
                        "preflight_stale",
                        "validation",
                    )
                )
            preflight = next(
                (
                    item
                    for item in reversed(self._store.preflights.get(request.task_id, ()))
                    if item.preflight_id == request.preflight_id
                ),
                None,
            )
            if preflight is None or not preflight.ready:
                return remember(
                    _error(
                        self._clock,
                        "execution_repository",
                        request.operation_id,
                        "preflight_not_passed",
                        "validation",
                    )
                )
            if preflight.consumed_at is not None:
                return remember(
                    _error(
                        self._clock,
                        "execution_repository",
                        request.operation_id,
                        "preflight_consumed",
                        "conflict",
                    )
                )
            if self._clock.current_utc().as_datetime() >= preflight.expires_at.as_datetime():
                return remember(
                    _error(
                        self._clock,
                        "execution_repository",
                        request.operation_id,
                        "preflight_expired",
                        "validation",
                    )
                )
            if (
                preflight.task_version + 1 != request.expected_task_version
                or preflight.input_lock_hash != request.input_lock_hash
                or preflight.dependency_snapshot_hash != request.dependency_snapshot_hash
            ):
                return remember(
                    _error(
                        self._clock,
                        "execution_repository",
                        request.operation_id,
                        "preflight_stale",
                        "validation",
                    )
                )
            scope_key = (request.trusted_user_scope, request.request_fingerprint)
            ids = self._store.quota.get(scope_key, [])
            if request.attempt_kind == "user_initial":
                if ids:
                    return remember(
                        _error(
                            self._clock,
                            "execution_repository",
                            request.operation_id,
                            "formal_quota_exhausted",
                            "quota",
                        )
                    )
                attempt_number = 1
            else:
                if request.admin_authorization is None:
                    return remember(
                        _error(
                            self._clock,
                            "execution_repository",
                            request.operation_id,
                            "admin_authorization_required",
                            "validation",
                        )
                    )
                if len(ids) != 1 or request.original_execution_id != ids[0]:
                    return remember(
                        _error(
                            self._clock,
                            "execution_repository",
                            request.operation_id,
                            "formal_quota_exhausted",
                            "quota",
                        )
                    )
                original = self._store.executions.get(ids[0])
                if (
                    original is None
                    or original.state != "failed"
                    or original.outcome != "failed"
                    or original.failure_reason_code != request.technical_failure_code
                ):
                    return remember(
                        _error(
                            self._clock,
                            "execution_repository",
                            request.operation_id,
                            "invalid_technical_failure_code",
                            "validation",
                        )
                    )
                attempt_number = 2
            now = self._clock.current_utc()
            execution = ExecutionRecordDTO(
                execution_id=request.execution_id,
                task_id=request.task_id,
                request_fingerprint=request.request_fingerprint,
                attempt_number=attempt_number,
                attempt_kind=request.attempt_kind,
                original_execution_id=request.original_execution_id,
                technical_failure_code=request.technical_failure_code,
                state="created",
                outcome="in_progress",
                absolute_deadline_at=request.absolute_deadline_at,
                started_at=request.started_at,
                updated_at=request.started_at,
                completed_at=None,
                partial_reason_codes=(),
                failure_reason_code=None,
                version=1,
            )
            snapshot = {
                "tasks": dict(self._store.tasks),
                "preflights": {
                    key: list(value) for key, value in self._store.preflights.items()
                },
                "executions": dict(self._store.executions),
                "execution_scope": dict(self._store.execution_scope),
                "quota": {key: list(value) for key, value in self._store.quota.items()},
                "replays": dict(self._store.replays),
                "replay_identities": dict(self._store.replay_identities),
            }

            class InjectedAcquireFault(RuntimeError):
                pass

            def inject(stage: str) -> None:
                if (
                    self._acquire_fault_at == stage
                    and not self._acquire_fault_consumed
                ):
                    self._acquire_fault_consumed = True
                    raise InjectedAcquireFault("non-production injected acquire failure")

            try:
                inject("before_commit")
                self._store.tasks[task.task_id] = replace(
                    task,
                    state="execution_locked",
                    version=task.version + 1,
                    locked_execution_id=request.execution_id,
                )
                history = self._store.preflights[request.task_id]
                history[history.index(preflight)] = replace(
                    preflight,
                    consumed_at=now,
                    consumed_by_execution_id=request.execution_id,
                )
                inject("after_preflight_consume")
                self._store.executions[request.execution_id] = execution
                self._store.execution_scope[request.execution_id] = request.trusted_user_scope
                self._store.quota[scope_key] = [*ids, request.execution_id]
                remember(execution)
                inject("after_replay_record")
                return execution
            except InjectedAcquireFault:
                for name, values in snapshot.items():
                    target = getattr(self._store, name)
                    target.clear()
                    target.update(values)
                return _error(
                    self._clock,
                    "execution_repository",
                    request.operation_id,
                    "repository_unavailable",
                    "unavailable",
                )

    def get(self, request: GetExecutionRequestDTO) -> ExecutionRecordDTO | ErrorResultDTO:
        with self._store.lock:
            execution = self._store.executions.get(request.execution_id)
            if execution is None or self._store.execution_scope.get(request.execution_id) != request.trusted_user_scope:
                return _error(self._clock, "execution_repository", request.operation_id, "execution_not_found", "not_found")
            return execution

    def transition(self, request: TransitionExecutionRequestDTO) -> ExecutionRecordDTO | ErrorResultDTO:
        transitions = {
            "created": {"collecting"},
            "collecting": {"extracting", "analyzing", "failed", "manual_case_required"},
            "extracting": {"reasoning", "failed", "manual_case_required"},
            "analyzing": {"reasoning", "failed", "manual_case_required"},
            "reasoning": {"validating", "failed", "manual_case_required"},
            "validating": {"publishing", "failed", "manual_case_required"},
            "publishing": {"completed", "failed", "manual_case_required"},
        }
        with self._store.lock:
            identity = (request.execution_id, request.expected_version)
            replay = self._store.replays.get(("execution_transition", request.operation_id))
            if replay is not None:
                if self._store.replay_identities.get(("execution_transition", request.operation_id)) != identity:
                    return _error(self._clock, "execution_repository", request.operation_id, "execution_version_conflict")
                return replay
            self._store.replay_identities[("execution_transition", request.operation_id)] = identity
            execution = self._store.executions.get(request.execution_id)
            if execution is None:
                return self._remember_transition(request.operation_id, _error(self._clock, "execution_repository", request.operation_id, "execution_not_found", "not_found"))
            if execution.version != request.expected_version:
                return self._remember_transition(request.operation_id, _error(self._clock, "execution_repository", request.operation_id, "execution_version_conflict"))
            if execution.state != request.from_state or request.to_state not in transitions.get(execution.state, set()):
                return self._remember_transition(request.operation_id, _error(self._clock, "execution_repository", request.operation_id, "invalid_state_transition", "validation"))
            terminal = request.to_state in {"completed", "failed", "manual_case_required"}
            if request.to_state == "completed":
                outcome = "partial" if request.partial_reason_codes else "success"
            elif request.to_state == "failed":
                outcome = "failed"
            elif request.to_state == "manual_case_required":
                outcome = "manual_case_required"
            else:
                outcome = "in_progress"
            updated = replace(
                execution,
                state=request.to_state,
                outcome=outcome,
                updated_at=request.occurred_at,
                completed_at=request.occurred_at if terminal else None,
                partial_reason_codes=request.partial_reason_codes,
                failure_reason_code=request.safe_reason_code if request.to_state == "failed" else None,
                version=execution.version + 1,
            )
            self._store.executions[execution.execution_id] = updated
            if terminal:
                task = self._store.tasks[execution.task_id]
                self._store.tasks[task.task_id] = replace(task, state="closed", version=task.version + 1)
            return self._remember_transition(request.operation_id, updated)

    def record_manual_case(self, request: RecordManualCaseRequestDTO) -> ManualCaseDTO | ErrorResultDTO:
        with self._store.lock:
            replay = self._store.replays.get(("manual_case", request.operation_id))
            if replay is not None:
                return replay
            execution = self._store.executions.get(request.execution_id)
            if execution is None:
                result = _error(self._clock, "execution_repository", request.operation_id, "execution_not_found", "not_found")
            elif execution.version != request.expected_version:
                result = _error(self._clock, "execution_repository", request.operation_id, "execution_version_conflict")
            elif request.execution_id in self._store.manual_cases:
                result = self._store.manual_cases[request.execution_id]
            else:
                result = ManualCaseDTO(
                    manual_case_id=f"CASE-{len(self._store.manual_cases) + 1:08d}",
                    execution_id=execution.execution_id,
                    task_id=execution.task_id,
                    reason_code=request.reason_code,
                    created_at=request.created_at,
                )
                self._store.manual_cases[request.execution_id] = result
            self._store.replays[("manual_case", request.operation_id)] = result
            return result

    def list_by_quota_scope(self, request: ListByQuotaScopeRequestDTO) -> ExecutionQuotaViewDTO:
        with self._store.lock:
            ids = self._store.quota.get((request.trusted_user_scope, request.request_fingerprint), [])
            executions = tuple(self._store.executions[item] for item in ids)
            return ExecutionQuotaViewDTO(
                trusted_user_scope=request.trusted_user_scope,
                request_fingerprint=request.request_fingerprint,
                initial_used=len(ids) >= 1,
                admin_rerun_used=len(ids) >= 2,
                manual_case_required=any(item in self._store.manual_cases for item in ids),
                executions=executions,
            )

    def _remember(self, operation_id: str, result: object):
        self._store.replays[("execution_acquire", operation_id)] = result
        return result

    def _remember_transition(self, operation_id: str, result: object):
        self._store.replays[("execution_transition", operation_id)] = result
        return result


@_guard_deadlines(
    "evidence_repository",
    {
        "append_evidence": 2000,
        "append_claim_links": 2000,
        "append_assessments": 2000,
        "get": 2000,
        "list_for_task": 5000,
        "get_latest_assessments": 5000,
    },
)
class FakeEvidenceRepository:
    """Non-production append-only evidence fake with immutable snapshots."""

    non_production = True

    def __init__(
        self,
        clock: FakeClock,
        *,
        snapshot_ttl_seconds: int | None = None,
    ) -> None:
        if snapshot_ttl_seconds is not None and (
            type(snapshot_ttl_seconds) is not int or snapshot_ttl_seconds <= 0
        ):
            raise ValueError("snapshot_ttl_seconds must be a positive integer or None")
        self._clock = clock
        self._snapshot_ttl_seconds = snapshot_ttl_seconds
        self._lock = RLock()
        self._evidence: dict[str, EvidenceDTO] = {}
        self._links: dict[str, EvidenceClaimLinkDTO] = {}
        self._assessments: dict[str, EvidenceAssessmentDTO] = {}
        self._snapshots: dict[
            str,
            tuple[str, str | None, str | None, tuple[str, ...], dict[str, int], int],
        ] = {}
        self._issued_cursors: dict[str, set[str | None]] = {}
        self._snapshot_counter = 0
        self._replays: dict[tuple[str, str], object] = {}

    @property
    def snapshot_ttl_seconds(self) -> int | None:
        """Explicit fake-only policy; ``None`` preserves non-expiring behavior."""

        return self._snapshot_ttl_seconds

    def _snapshot_expired(
        self,
        snapshot: tuple[
            str, str | None, str | None, tuple[str, ...], dict[str, int], int
        ],
    ) -> bool:
        if self._snapshot_ttl_seconds is None:
            return False
        elapsed_ms = self._clock.current_monotonic_ms() - snapshot[5]
        return elapsed_ms >= self._snapshot_ttl_seconds * 1_000

    def append_evidence(self, request: AppendEvidenceRequestDTO) -> EvidenceDTO | ErrorResultDTO:
        with self._lock:
            replay = self._replays.get(("evidence", request.operation_id))
            if replay is not None:
                if isinstance(replay, EvidenceDTO) and replay != request.evidence:
                    return _error(self._clock, "evidence_repository", request.operation_id, "evidence_immutable", "integrity")
                return replay
            item = request.evidence
            if item.task_id != request.expected_task_id:
                result = _error(self._clock, "evidence_repository", request.operation_id, "cross_task_reference", "validation")
            else:
                existing = self._evidence.get(item.evidence_id)
                if existing is None:
                    self._evidence[item.evidence_id] = item
                    result = item
                elif existing == item:
                    result = existing
                else:
                    code = "content_hash_conflict" if (existing.raw_content_hash != item.raw_content_hash or existing.clean_content_hash != item.clean_content_hash) else "evidence_immutable"
                    result = _error(self._clock, "evidence_repository", request.operation_id, code, "integrity")
            self._replays[("evidence", request.operation_id)] = result
            return result

    def append_claim_links(self, request: AppendClaimLinksRequestDTO) -> AppendResultDTO | ErrorResultDTO:
        with self._lock:
            prepared: list[tuple[EvidenceClaimLinkDTO, str]] = []
            for item in request.items:
                evidence = self._evidence.get(item.evidence_id)
                if item.task_id != request.expected_task_id or (evidence and evidence.task_id != item.task_id):
                    return _error(self._clock, "evidence_repository", request.operation_id, "cross_task_reference", "validation")
                if evidence is None:
                    return _error(self._clock, "evidence_repository", request.operation_id, "evidence_not_found", "not_found")
                if evidence.validation_status == "quarantined":
                    return _error(self._clock, "evidence_repository", request.operation_id, "quarantined_reference", "validation")
                existing = self._links.get(item.link_id)
                if existing is not None and existing != item:
                    return _error(self._clock, "evidence_repository", request.operation_id, "claim_link_invalid", "integrity")
                prepared.append((item, "existing" if existing else "appended"))
            for item, _ in prepared:
                self._links.setdefault(item.link_id, item)
            return AppendResultDTO(tuple(AppendResultItemDTO(item.link_id, outcome) for item, outcome in prepared))

    def append_assessments(self, request: AppendAssessmentsRequestDTO) -> AppendResultDTO | ErrorResultDTO:
        with self._lock:
            prepared: list[tuple[EvidenceAssessmentDTO, str]] = []
            staged_max: dict[str, int] = {}
            for item in request.items:
                evidence = self._evidence.get(item.evidence_id)
                if item.task_id != request.expected_task_id or (evidence and evidence.task_id != item.task_id):
                    return _error(self._clock, "evidence_repository", request.operation_id, "cross_task_reference", "validation")
                if evidence is None:
                    return _error(self._clock, "evidence_repository", request.operation_id, "evidence_not_found", "not_found")
                existing = self._assessments.get(item.assessment_id)
                if existing is not None:
                    if existing != item:
                        return _error(self._clock, "evidence_repository", request.operation_id, "assessment_sequence_conflict", "integrity")
                    prepared.append((item, "existing"))
                    continue
                current_max = staged_max.get(item.evidence_id, max((entry.assessment_sequence for entry in self._assessments.values() if entry.evidence_id == item.evidence_id), default=0))
                if item.assessment_sequence <= current_max:
                    return _error(self._clock, "evidence_repository", request.operation_id, "assessment_sequence_conflict", "integrity")
                staged_max[item.evidence_id] = item.assessment_sequence
                prepared.append((item, "appended"))
            for item, outcome in prepared:
                if outcome == "appended":
                    self._assessments[item.assessment_id] = item
            return AppendResultDTO(tuple(AppendResultItemDTO(item.assessment_id, outcome) for item, outcome in prepared))

    def get(self, request: GetEvidenceRequestDTO) -> EvidenceDTO | ErrorResultDTO:
        with self._lock:
            item = self._evidence.get(request.evidence_id)
            if item is None:
                return _error(self._clock, "evidence_repository", request.operation_id, "evidence_not_found", "not_found")
            if item.task_id != request.task_id:
                return _error(self._clock, "evidence_repository", request.operation_id, "cross_task_reference", "validation")
            if item.validation_status == "quarantined" and not request.include_quarantined:
                return _error(self._clock, "evidence_repository", request.operation_id, "quarantined_reference", "validation")
            return item

    def list_for_task(self, request: ListEvidenceRequestDTO) -> EvidencePageDTO | ErrorResultDTO:
        with self._lock:
            if not 1 <= request.limit <= 200:
                raise ValueError("limit must be 1..200")
            if request.snapshot_token is None:
                ids = tuple(sorted(item.evidence_id for item in self._evidence.values() if item.task_id == request.task_id and (request.source_type is None or item.source_type == request.source_type) and (request.validation_status is None or item.validation_status == request.validation_status)))
                self._snapshot_counter += 1
                digest = hashlib.sha256(
                    f"fake-evidence-snapshot-v1:{self._snapshot_counter}".encode("ascii")
                ).hexdigest()
                token = f"snapshot-{digest}"
                assessment_max = {
                    evidence_id: max(
                        (entry.assessment_sequence for entry in self._assessments.values() if entry.evidence_id == evidence_id),
                        default=0,
                    )
                    for evidence_id in ids
                }
                self._snapshots[token] = (
                    request.task_id,
                    request.source_type,
                    request.validation_status,
                    ids,
                    assessment_max,
                    self._clock.current_monotonic_ms(),
                )
                self._issued_cursors[token] = {None}
                offset = 0
            else:
                token = request.snapshot_token
                snapshot = self._snapshots.get(token)
                if (
                    snapshot is None
                    or self._snapshot_expired(snapshot)
                    or snapshot[:3] != (request.task_id, request.source_type, request.validation_status)
                ):
                    return _error(self._clock, "evidence_repository", request.operation_id, "snapshot_expired", "validation")
                ids = snapshot[3]
                if request.cursor not in self._issued_cursors[token]:
                    return _error(self._clock, "evidence_repository", request.operation_id, "snapshot_expired", "validation")
                if request.cursor is None:
                    offset = 0
                else:
                    prefix = f"{token}:"
                    offset = int(request.cursor[len(prefix):])
            selected = ids[offset: offset + request.limit]
            next_cursor = f"{token}:{offset + request.limit}" if offset + request.limit < len(ids) else None
            self._issued_cursors[token].add(next_cursor)
            return EvidencePageDTO(request.task_id, tuple(self._evidence[item] for item in selected), token, next_cursor)

    def get_latest_assessments(self, request: GetLatestAssessmentsRequestDTO) -> LatestAssessmentsDTO | ErrorResultDTO:
        with self._lock:
            snapshot = self._snapshots.get(request.snapshot_token)
            if (
                snapshot is None
                or self._snapshot_expired(snapshot)
                or snapshot[0] != request.task_id
            ):
                return _error(self._clock, "evidence_repository", request.operation_id, "snapshot_expired", "validation")
            snapshot_ids = set(snapshot[3])
            assessment_max = snapshot[4]
            latest: list[EvidenceAssessmentDTO] = []
            for evidence_id in request.evidence_ids:
                evidence = self._evidence.get(evidence_id)
                if evidence is None:
                    return _error(self._clock, "evidence_repository", request.operation_id, "evidence_not_found", "not_found")
                if evidence.task_id != request.task_id or evidence_id not in snapshot_ids:
                    return _error(self._clock, "evidence_repository", request.operation_id, "cross_task_reference", "validation")
                candidates = [
                    item
                    for item in self._assessments.values()
                    if item.evidence_id == evidence_id
                    and item.assessment_sequence <= assessment_max[evidence_id]
                ]
                if candidates:
                    latest.append(max(candidates, key=lambda item: item.assessment_sequence))
            return LatestAssessmentsDTO(request.task_id, tuple(latest), request.snapshot_token)
