"""Task aggregate 與其 deterministic state machine。"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum

from crypto_trust_agent.domain.errors import (
    FormalExecutionNotReady,
    InvalidStateTransition,
    VersionConflict,
)


_TASK_ID_PATTERN = re.compile(r"^TASK-[A-Za-z0-9._:-]{1,123}$")
_EXECUTION_ID_PATTERN = re.compile(r"^EXEC-[A-Za-z0-9._:-]{1,123}$")


class TaskState(str, Enum):
    VALIDATING = "validating"
    REJECTED = "rejected"
    PLANNED = "planned"
    READY_FOR_PREFLIGHT = "ready_for_preflight"
    PREFLIGHT_PASSED = "preflight_passed"
    EXECUTION_LOCKED = "execution_locked"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


_TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.VALIDATING: frozenset({TaskState.REJECTED, TaskState.PLANNED}),
    TaskState.REJECTED: frozenset(),
    TaskState.PLANNED: frozenset({TaskState.READY_FOR_PREFLIGHT}),
    TaskState.READY_FOR_PREFLIGHT: frozenset(
        {TaskState.READY_FOR_PREFLIGHT, TaskState.PREFLIGHT_PASSED}
    ),
    TaskState.PREFLIGHT_PASSED: frozenset(
        {TaskState.READY_FOR_PREFLIGHT, TaskState.EXECUTION_LOCKED}
    ),
    TaskState.EXECUTION_LOCKED: frozenset(
        {TaskState.COMPLETED, TaskState.PARTIAL, TaskState.FAILED}
    ),
    TaskState.COMPLETED: frozenset(),
    TaskState.PARTIAL: frozenset(),
    TaskState.FAILED: frozenset(),
}

_TASK_TERMINAL_STATES = frozenset(
    {TaskState.REJECTED, TaskState.COMPLETED, TaskState.PARTIAL, TaskState.FAILED}
)


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    formal_run_intent: bool
    state: TaskState = TaskState.VALIDATING
    version: int = 1
    locked_execution_id: str | None = None
    terminal_execution_id: str | None = None

    def __post_init__(self) -> None:
        if not _TASK_ID_PATTERN.fullmatch(self.task_id):
            raise ValueError("invalid task_id")
        if type(self.formal_run_intent) is not bool:
            raise ValueError("formal_run_intent must be boolean")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("version must be a positive integer")
        if self.locked_execution_id is not None and not _EXECUTION_ID_PATTERN.fullmatch(
            self.locked_execution_id
        ):
            raise ValueError("invalid locked_execution_id")
        if self.terminal_execution_id is not None and not _EXECUTION_ID_PATTERN.fullmatch(
            self.terminal_execution_id
        ):
            raise ValueError("invalid terminal_execution_id")

    @property
    def is_terminal(self) -> bool:
        return self.state in _TASK_TERMINAL_STATES

    def transition(self, target: TaskState, *, expected_version: int) -> Task:
        self._require_version(expected_version)
        if target not in _TASK_TRANSITIONS[self.state]:
            raise InvalidStateTransition(
                f"Task transition {self.state.value} -> {target.value} is not allowed"
            )
        if target is TaskState.EXECUTION_LOCKED:
            raise FormalExecutionNotReady(
                "execution lock requires an execution ID and atomic repository operation"
            )
        if target in {
            TaskState.COMPLETED,
            TaskState.PARTIAL,
            TaskState.FAILED,
        }:
            raise InvalidStateTransition(
                "terminal Task transition requires the matching Execution"
            )
        return replace(self, state=target, version=self.version + 1)

    def lock_for_execution(self, execution_id: str, *, expected_version: int) -> Task:
        self._require_version(expected_version)
        if not self.formal_run_intent or self.state is not TaskState.PREFLIGHT_PASSED:
            raise FormalExecutionNotReady(
                "formal execution requires a passed pre-flight for this Task version"
            )
        if not _EXECUTION_ID_PATTERN.fullmatch(execution_id):
            raise ValueError("invalid execution_id")
        return replace(
            self,
            state=TaskState.EXECUTION_LOCKED,
            version=self.version + 1,
            locked_execution_id=execution_id,
        )

    def finish_from_execution(
        self,
        target: TaskState,
        *,
        execution_id: str,
        expected_version: int,
    ) -> Task:
        self._require_version(expected_version)
        if (
            self.state is not TaskState.EXECUTION_LOCKED
            or self.locked_execution_id != execution_id
            or target
            not in {TaskState.COMPLETED, TaskState.PARTIAL, TaskState.FAILED}
        ):
            raise InvalidStateTransition(
                "Task can finish only from its matching locked Execution"
            )
        return replace(
            self,
            state=target,
            version=self.version + 1,
            terminal_execution_id=execution_id,
        )

    def _require_version(self, expected_version: int) -> None:
        if expected_version != self.version:
            raise VersionConflict(
                f"expected Task version {expected_version}, current {self.version}"
            )
