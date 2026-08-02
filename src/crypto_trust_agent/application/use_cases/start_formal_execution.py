"""Formal execution start and repeated technical-failure escalation orchestration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.repositories import (
    AcquireExecutionRequestDTO,
    AdminAuthorizationDTO,
    ClockReadRequestDTO,
    ExecutionRecordDTO,
    GetLatestPreflightRequestDTO,
    ListByQuotaScopeRequestDTO,
    ManualCaseDTO,
    RecordManualCaseRequestDTO,
    TaskQueryRequestDTO,
)
from crypto_trust_agent.application.ports import Clock, ExecutionRepository, TaskRepository
from crypto_trust_agent.domain.primitives import ContractValidationError

IdentifierFactory = Callable[[str], str]
TECHNICAL_FAILURE_ALLOWLIST = frozenset(
    {
        "provider_timeout",
        "provider_unavailable",
        "invalid_provider_output",
        "publication_failure",
        "platform_failure",
    }
)
_PSEUDONYM = re.compile(r"^hmac-sha256:[A-Za-z0-9._-]{1,32}:[0-9a-f]{64}$")


class StartFormalExecutionValidationError(ValueError):
    """The request cannot identify a valid persisted ready binding."""


class StartFormalExecutionAuthorizationError(PermissionError):
    """A verified administrator is required for a technical rerun."""


class StartFormalExecutionTaskNotFound(LookupError):
    """The task is absent or outside the verified principal's scope."""


class StartFormalExecutionConflict(RuntimeError):
    """The persisted pass or formal quota cannot be used."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StartFormalExecutionDependencyError(RuntimeError):
    """A required repository or clock operation failed safely."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class StartFormalExecutionCommand:
    trusted_user_scope: str
    principal_pseudonym: str
    is_admin: bool
    task_id: str
    preflight_id: str
    input_lock_hash: str
    operation_id: str | None = None
    execution_id: str | None = None
    original_execution_id: str | None = None
    technical_failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class StartFormalExecutionResult:
    outcome: str
    execution: ExecutionRecordDTO | None = None
    manual_case: ManualCaseDTO | None = None

    def __post_init__(self) -> None:
        if self.outcome == "execution_created" and (self.execution is None or self.manual_case is not None):
            raise ValueError("execution result is inconsistent")
        if self.outcome == "manual_case_opened" and (self.manual_case is None or self.execution is not None):
            raise ValueError("manual-case result is inconsistent")

    def to_safe_dict(self) -> dict[str, object]:
        if self.execution is not None:
            return {
                "schema_version": "1.0.0",
                "outcome": self.outcome,
                "execution_id": self.execution.execution_id,
                "task_id": self.execution.task_id,
                "attempt_number": self.execution.attempt_number,
                "attempt_kind": self.execution.attempt_kind,
                "state": self.execution.state,
                "started_at": str(self.execution.started_at),
                "absolute_deadline_at": str(self.execution.absolute_deadline_at),
                "hard_deadline_seconds": 900,
            }
        assert self.manual_case is not None
        return {
            "schema_version": "1.0.0",
            "outcome": self.outcome,
            "manual_case_id": self.manual_case.manual_case_id,
            "execution_id": self.manual_case.execution_id,
            "task_id": self.manual_case.task_id,
            "status": self.manual_case.status,
            "reason_code": self.manual_case.reason_code,
        }


class StartFormalExecutionUseCase:
    """Use a persisted ready pass; only ExecutionRepository may mutate start state."""

    def __init__(
        self,
        task_repository: TaskRepository,
        execution_repository: ExecutionRepository,
        clock: Clock,
        identifier_factory: IdentifierFactory,
    ) -> None:
        self._tasks = task_repository
        self._executions = execution_repository
        self._clock = clock
        self._new_id = identifier_factory

    def execute(self, command: StartFormalExecutionCommand) -> StartFormalExecutionResult:
        self._validate_command(command)
        now = self._read_now()
        task_operation = self._new_id("OP-EXEC-TASK-")
        task = self._invoke(
            self._tasks.get,
            TaskQueryRequestDTO(
                operation_id=task_operation,
                trusted_user_scope=command.trusted_user_scope,
                task_id=command.task_id,
                deadline=self._deadline(task_operation, now),
            ),
        )
        if isinstance(task, ErrorResultDTO):
            self._raise_port_error(task)

        is_rerun = command.original_execution_id is not None or command.technical_failure_code is not None
        authorization = None
        if is_rerun:
            authorization_or_manual = self._authorize_or_escalate(command, task.request_fingerprint, now)
            if isinstance(authorization_or_manual, StartFormalExecutionResult):
                return authorization_or_manual
            authorization = authorization_or_manual

        replaying_locked_start = (
            task.state == "execution_locked"
            and command.execution_id is not None
            and task.locked_execution_id == command.execution_id
            and task.version >= 3
        )
        if task.state == "ready_for_execution" and task.version >= 2:
            expected_task_version = task.version
            preflight_version = task.version - 1
        elif replaying_locked_start:
            expected_task_version = task.version - 1
            preflight_version = task.version - 2
        else:
            raise StartFormalExecutionConflict("preflight_stale")
        preflight_operation = self._new_id("OP-EXEC-PREFLIGHT-")
        preflight = self._invoke(
            self._tasks.get_latest_preflight,
            GetLatestPreflightRequestDTO(
                operation_id=preflight_operation,
                task_id=task.task_id,
                task_version=preflight_version,
                input_lock_hash=command.input_lock_hash,
                deadline=self._deadline(preflight_operation, now),
            ),
        )
        if isinstance(preflight, ErrorResultDTO):
            self._raise_port_error(preflight)
        if preflight.preflight_id != command.preflight_id or not preflight.ready:
            raise StartFormalExecutionConflict("preflight_stale")

        operation_id = command.operation_id or self._new_id("OP-EXEC-ACQUIRE-")
        execution_id = command.execution_id or self._new_id("EXEC-")
        try:
            request = AcquireExecutionRequestDTO(
                operation_id=operation_id,
                trusted_user_scope=command.trusted_user_scope,
                request_fingerprint=task.request_fingerprint,
                task_id=task.task_id,
                expected_task_version=expected_task_version,
                input_lock_hash=preflight.input_lock_hash,
                dependency_snapshot_hash=preflight.dependency_snapshot_hash,
                execution_id=execution_id,
                preflight_id=preflight.preflight_id,
                attempt_kind="admin_technical_rerun" if is_rerun else "user_initial",
                started_at=now,
                absolute_deadline_at=(now.as_datetime() + timedelta(seconds=900)).isoformat().replace("+00:00", "Z"),
                deadline=self._deadline(operation_id, now),
                admin_authorization=authorization,
                original_execution_id=command.original_execution_id,
                technical_failure_code=command.technical_failure_code,
            )
        except (ContractValidationError, TypeError, ValueError) as error:
            raise StartFormalExecutionValidationError("invalid execution start request") from error
        created = self._invoke(self._executions.acquire_quota_and_create, request)
        if isinstance(created, ErrorResultDTO):
            self._raise_port_error(created)
        return StartFormalExecutionResult("execution_created", execution=created)

    def _authorize_or_escalate(self, command, request_fingerprint: str, now):
        if not command.is_admin:
            raise StartFormalExecutionAuthorizationError("verified administrator required")
        if command.original_execution_id is None or command.technical_failure_code not in TECHNICAL_FAILURE_ALLOWLIST:
            raise StartFormalExecutionValidationError("invalid technical rerun")
        quota_operation = self._new_id("OP-EXEC-QUOTA-")
        quota = self._invoke(
            self._executions.list_by_quota_scope,
            ListByQuotaScopeRequestDTO(
                operation_id=quota_operation,
                trusted_user_scope=command.trusted_user_scope,
                request_fingerprint=request_fingerprint,
                deadline=self._deadline(quota_operation, now),
            ),
        )
        if isinstance(quota, ErrorResultDTO):
            self._raise_port_error(quota)
        if quota.admin_rerun_used:
            rerun = quota.executions[-1]
            if (
                command.execution_id == rerun.execution_id
                and rerun.original_execution_id == command.original_execution_id
                and rerun.technical_failure_code == command.technical_failure_code
            ):
                return AdminAuthorizationDTO(
                    authorization_id=self._new_id("AUTH-"),
                    verified_admin_subject_hash=command.principal_pseudonym,
                    verified_at=now,
                    reason="verified technical rerun",
                )
            if (
                rerun.attempt_number != 2
                or rerun.original_execution_id != command.original_execution_id
                or rerun.state != "failed"
                or rerun.outcome != "failed"
                or rerun.failure_reason_code not in TECHNICAL_FAILURE_ALLOWLIST
                or rerun.failure_reason_code != command.technical_failure_code
            ):
                raise StartFormalExecutionConflict("formal_quota_exhausted")
            manual_operation = self._new_id("OP-EXEC-MANUAL-")
            manual = self._invoke(
                self._executions.record_manual_case,
                RecordManualCaseRequestDTO(
                    operation_id=manual_operation,
                    execution_id=rerun.execution_id,
                    expected_version=rerun.version,
                    reason_code="rerun_failed",
                    created_at=now,
                    deadline=self._deadline(manual_operation, now),
                ),
            )
            if isinstance(manual, ErrorResultDTO):
                self._raise_port_error(manual)
            return StartFormalExecutionResult("manual_case_opened", manual_case=manual)
        return AdminAuthorizationDTO(
            authorization_id=self._new_id("AUTH-"),
            verified_admin_subject_hash=command.principal_pseudonym,
            verified_at=now,
            reason="verified technical rerun",
        )

    @staticmethod
    def _validate_command(command: StartFormalExecutionCommand) -> None:
        if (
            not isinstance(command.trusted_user_scope, str)
            or not command.trusted_user_scope
            or not _PSEUDONYM.fullmatch(command.principal_pseudonym)
            or type(command.is_admin) is not bool
            or not isinstance(command.task_id, str)
            or not command.task_id.startswith("TASK-")
            or not isinstance(command.preflight_id, str)
            or not command.preflight_id.startswith("PF-")
            or not isinstance(command.input_lock_hash, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", command.input_lock_hash)
        ):
            raise StartFormalExecutionValidationError("invalid execution start request")

    def _read_now(self):
        operation_id = self._new_id("OP-CLOCK-EXEC-")
        result = self._invoke(self._clock.now_utc, ClockReadRequestDTO(operation_id))
        if isinstance(result, ErrorResultDTO):
            raise StartFormalExecutionDependencyError(result.error.code)
        return result.utc

    @staticmethod
    def _deadline(operation_id: str, now) -> DeadlineDTO:
        return DeadlineDTO(
            schema_version="1.0.0",
            operation_id=operation_id,
            deadline_at_utc=(now.as_datetime() + timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
            budget_ms=2_000,
            sent_at_utc=now,
            safety_margin_ms=100,
        )

    @staticmethod
    def _invoke(operation, request):
        try:
            return operation(request)
        except StartFormalExecutionDependencyError:
            raise
        except Exception as error:
            raise StartFormalExecutionDependencyError("unexpected_provider_error") from error

    @staticmethod
    def _raise_port_error(result: ErrorResultDTO) -> None:
        code = result.error.code
        if code in {"task_not_found", "execution_not_found"}:
            raise StartFormalExecutionTaskNotFound("resource not found")
        if code in {
            "preflight_not_passed",
            "preflight_consumed",
            "preflight_expired",
            "preflight_stale",
            "formal_quota_exhausted",
            "invalid_technical_failure_code",
            "execution_version_conflict",
        }:
            raise StartFormalExecutionConflict(code)
        if code == "admin_authorization_required":
            raise StartFormalExecutionAuthorizationError("verified administrator required")
        raise StartFormalExecutionDependencyError(code)


__all__ = (
    "StartFormalExecutionAuthorizationError",
    "StartFormalExecutionCommand",
    "StartFormalExecutionConflict",
    "StartFormalExecutionDependencyError",
    "StartFormalExecutionResult",
    "StartFormalExecutionTaskNotFound",
    "StartFormalExecutionUseCase",
    "StartFormalExecutionValidationError",
    "TECHNICAL_FAILURE_ALLOWLIST",
)
