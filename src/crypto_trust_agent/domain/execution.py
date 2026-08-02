"""Execution aggregate、state machine 與 admin rerun invariants。"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum

from crypto_trust_agent.domain.errors import (
    AdminRerunNotAllowed,
    FormalExecutionNotReady,
    InvalidStateTransition,
    VersionConflict,
)
from crypto_trust_agent.domain.primitives import UtcInstant
from crypto_trust_agent.domain.task import Task, TaskState


_EXECUTION_ID_PATTERN = re.compile(r"^EXEC-[A-Za-z0-9._:-]{1,123}$")
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

ALLOWED_TECHNICAL_FAILURE_CODES = frozenset(
    {
        "provider_timeout",
        "provider_unavailable",
        "invalid_provider_output",
        "publication_failure",
        "platform_failure",
    }
)


class AttemptKind(str, Enum):
    USER_INITIAL = "user_initial"
    ADMIN_TECHNICAL_RERUN = "admin_technical_rerun"


class ExecutionState(str, Enum):
    CREATED = "created"
    COLLECTING = "collecting"
    EXTRACTING = "extracting"
    ANALYZING = "analyzing"
    ASSESSING = "assessing"
    REASONING = "reasoning"
    VALIDATING = "validating"
    WRITING = "writing"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class ExecutionOutcome(str, Enum):
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


_ACTIVE_STATES = frozenset(
    {
        ExecutionState.COLLECTING,
        ExecutionState.EXTRACTING,
        ExecutionState.ANALYZING,
        ExecutionState.ASSESSING,
        ExecutionState.REASONING,
        ExecutionState.VALIDATING,
        ExecutionState.WRITING,
    }
)
_TERMINAL_STATES = frozenset(
    {ExecutionState.SUCCEEDED, ExecutionState.PARTIAL, ExecutionState.FAILED}
)
_EXECUTION_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.CREATED: frozenset({ExecutionState.COLLECTING}),
    ExecutionState.COLLECTING: frozenset(
        {
            ExecutionState.EXTRACTING,
            ExecutionState.ANALYZING,
            ExecutionState.PARTIAL,
            ExecutionState.FAILED,
        }
    ),
    ExecutionState.EXTRACTING: frozenset(
        {ExecutionState.ASSESSING, ExecutionState.PARTIAL, ExecutionState.FAILED}
    ),
    ExecutionState.ANALYZING: frozenset(
        {ExecutionState.ASSESSING, ExecutionState.PARTIAL, ExecutionState.FAILED}
    ),
    ExecutionState.ASSESSING: frozenset(
        {ExecutionState.REASONING, ExecutionState.PARTIAL, ExecutionState.FAILED}
    ),
    ExecutionState.REASONING: frozenset(
        {ExecutionState.VALIDATING, ExecutionState.PARTIAL, ExecutionState.FAILED}
    ),
    ExecutionState.VALIDATING: frozenset(
        {ExecutionState.WRITING, ExecutionState.PARTIAL, ExecutionState.FAILED}
    ),
    ExecutionState.WRITING: frozenset(_TERMINAL_STATES),
    ExecutionState.SUCCEEDED: frozenset(),
    ExecutionState.PARTIAL: frozenset(),
    ExecutionState.FAILED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Execution:
    execution_id: str
    task_id: str
    request_fingerprint: str
    attempt_number: int
    attempt_kind: AttemptKind
    original_execution_id: str | None
    technical_failure_code: str | None
    state: ExecutionState
    outcome: ExecutionOutcome
    started_at: UtcInstant
    absolute_deadline_at: UtcInstant
    completed_at: UtcInstant | None = None
    partial_reason_codes: tuple[str, ...] = ()
    failure_reason_code: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not _EXECUTION_ID_PATTERN.fullmatch(self.execution_id):
            raise ValueError("invalid execution_id")
        if not self.task_id.startswith("TASK-"):
            raise ValueError("invalid task_id")
        if not _SHA256_PATTERN.fullmatch(self.request_fingerprint):
            raise ValueError("invalid request_fingerprint")
        if type(self.attempt_number) is not int or not 1 <= self.attempt_number <= 2:
            raise AdminRerunNotAllowed("attempt_number must be 1 or 2")
        if self.attempt_kind is AttemptKind.USER_INITIAL:
            if (
                self.attempt_number != 1
                or self.original_execution_id is not None
                or self.technical_failure_code is not None
            ):
                raise AdminRerunNotAllowed("initial execution cannot carry rerun fields")
        else:
            if (
                self.attempt_number != 2
                or self.original_execution_id is None
                or self.technical_failure_code not in ALLOWED_TECHNICAL_FAILURE_CODES
            ):
                raise AdminRerunNotAllowed("invalid admin technical rerun")
        if self.original_execution_id is not None and not _EXECUTION_ID_PATTERN.fullmatch(
            self.original_execution_id
        ):
            raise AdminRerunNotAllowed("invalid original_execution_id")
        if self.absolute_deadline_at.as_datetime() <= self.started_at.as_datetime():
            raise ValueError("absolute deadline must be after started_at")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("version must be a positive integer")
        if len(set(self.partial_reason_codes)) != len(self.partial_reason_codes):
            raise ValueError("partial reason codes must be unique")
        for code in self.partial_reason_codes:
            if not _SAFE_CODE_PATTERN.fullmatch(code):
                raise ValueError("invalid partial reason code")
        if self.failure_reason_code is not None and not _SAFE_CODE_PATTERN.fullmatch(
            self.failure_reason_code
        ):
            raise ValueError("invalid failure reason code")

    @classmethod
    def create_for_locked_task(
        cls,
        *,
        task: Task,
        execution_id: str,
        request_fingerprint: str,
        started_at: str | UtcInstant,
        absolute_deadline_at: str | UtcInstant,
        attempt_number: int = 1,
        attempt_kind: AttemptKind = AttemptKind.USER_INITIAL,
        original_execution_id: str | None = None,
        technical_failure_code: str | None = None,
        admin_authorized: bool = False,
    ) -> Execution:
        if (
            task.state is not TaskState.EXECUTION_LOCKED
            or task.locked_execution_id != execution_id
        ):
            raise FormalExecutionNotReady(
                "Execution requires the matching atomically locked Task"
            )
        if attempt_kind is AttemptKind.ADMIN_TECHNICAL_RERUN and not admin_authorized:
            raise AdminRerunNotAllowed("verified admin authorization is required")
        if attempt_kind is AttemptKind.USER_INITIAL and admin_authorized:
            raise AdminRerunNotAllowed("initial execution must not use admin authorization")
        return cls(
            execution_id=execution_id,
            task_id=task.task_id,
            request_fingerprint=request_fingerprint,
            attempt_number=attempt_number,
            attempt_kind=attempt_kind,
            original_execution_id=original_execution_id,
            technical_failure_code=technical_failure_code,
            state=ExecutionState.CREATED,
            outcome=ExecutionOutcome.IN_PROGRESS,
            started_at=(
                started_at if isinstance(started_at, UtcInstant) else UtcInstant(started_at)
            ),
            absolute_deadline_at=(
                absolute_deadline_at
                if isinstance(absolute_deadline_at, UtcInstant)
                else UtcInstant(absolute_deadline_at)
            ),
        )

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    def transition(
        self,
        target: ExecutionState,
        *,
        expected_version: int,
        occurred_at: str | UtcInstant | None = None,
        partial_reason_codes: tuple[str, ...] = (),
        failure_reason_code: str | None = None,
    ) -> Execution:
        if expected_version != self.version:
            raise VersionConflict(
                f"expected Execution version {expected_version}, current {self.version}"
            )
        if target not in _EXECUTION_TRANSITIONS[self.state]:
            raise InvalidStateTransition(
                f"Execution transition {self.state.value} -> {target.value} is not allowed"
            )
        if target not in _TERMINAL_STATES:
            if occurred_at is not None or partial_reason_codes or failure_reason_code:
                raise InvalidStateTransition(
                    "terminal metadata is not allowed on an active transition"
                )
            return replace(self, state=target, version=self.version + 1)
        if occurred_at is None:
            raise InvalidStateTransition("terminal transition requires occurred_at")
        completed_at = (
            occurred_at if isinstance(occurred_at, UtcInstant) else UtcInstant(occurred_at)
        )
        if completed_at.as_datetime() < self.started_at.as_datetime():
            raise InvalidStateTransition("completed_at cannot precede started_at")
        outcomes = {
            ExecutionState.SUCCEEDED: ExecutionOutcome.SUCCESS,
            ExecutionState.PARTIAL: ExecutionOutcome.PARTIAL,
            ExecutionState.FAILED: ExecutionOutcome.FAILED,
        }
        return replace(
            self,
            state=target,
            outcome=outcomes[target],
            completed_at=completed_at,
            partial_reason_codes=partial_reason_codes,
            failure_reason_code=failure_reason_code,
            version=self.version + 1,
        )
