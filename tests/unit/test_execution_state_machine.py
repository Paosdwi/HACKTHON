from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.domain.errors import (  # noqa: E402
    AdminRerunNotAllowed,
    FormalExecutionNotReady,
    InvalidStateTransition,
    VersionConflict,
)
from crypto_trust_agent.domain.execution import (  # noqa: E402
    AttemptKind,
    Execution,
    ExecutionOutcome,
    ExecutionState,
)
from crypto_trust_agent.domain.task import Task, TaskState  # noqa: E402


class ExecutionStateMachineTests(unittest.TestCase):
    def locked_task(self, execution_id: str = "EXEC-001") -> Task:
        task = Task(task_id="TASK-001", formal_run_intent=True)
        task = task.transition(TaskState.PLANNED, expected_version=1)
        task = task.transition(TaskState.READY_FOR_PREFLIGHT, expected_version=2)
        task = task.transition(TaskState.PREFLIGHT_PASSED, expected_version=3)
        return task.lock_for_execution(execution_id, expected_version=4)

    def test_execution_cannot_exist_without_matching_preflight_lock(self) -> None:
        task = Task(task_id="TASK-001", formal_run_intent=True)
        with self.assertRaises(FormalExecutionNotReady):
            Execution.create_for_locked_task(
                task=task,
                execution_id="EXEC-001",
                request_fingerprint="sha256:" + "a" * 64,
                started_at="2026-08-01T02:00:00Z",
                absolute_deadline_at="2026-08-01T02:15:00Z",
            )

    def test_successful_state_path_is_versioned_and_terminal(self) -> None:
        execution = Execution.create_for_locked_task(
            task=self.locked_task(),
            execution_id="EXEC-001",
            request_fingerprint="sha256:" + "a" * 64,
            started_at="2026-08-01T02:00:00Z",
            absolute_deadline_at="2026-08-01T02:15:00Z",
        )
        path = (
            ExecutionState.COLLECTING,
            ExecutionState.EXTRACTING,
            ExecutionState.ASSESSING,
            ExecutionState.REASONING,
            ExecutionState.VALIDATING,
            ExecutionState.WRITING,
        )
        for state in path:
            execution = execution.transition(state, expected_version=execution.version)
        execution = execution.transition(
            ExecutionState.SUCCEEDED,
            expected_version=execution.version,
            occurred_at="2026-08-01T02:10:00Z",
        )
        self.assertEqual(ExecutionOutcome.SUCCESS, execution.outcome)
        self.assertEqual(8, execution.version)
        with self.assertRaises(InvalidStateTransition):
            execution.transition(
                ExecutionState.COLLECTING,
                expected_version=execution.version,
            )

    def test_analysis_branch_and_partial_terminal_are_valid(self) -> None:
        execution = Execution.create_for_locked_task(
            task=self.locked_task(),
            execution_id="EXEC-001",
            request_fingerprint="sha256:" + "b" * 64,
            started_at="2026-08-01T02:00:00Z",
            absolute_deadline_at="2026-08-01T02:15:00Z",
        )
        execution = execution.transition(ExecutionState.COLLECTING, expected_version=1)
        execution = execution.transition(ExecutionState.ANALYZING, expected_version=2)
        execution = execution.transition(ExecutionState.ASSESSING, expected_version=3)
        execution = execution.transition(
            ExecutionState.PARTIAL,
            expected_version=4,
            occurred_at="2026-08-01T02:14:59Z",
            partial_reason_codes=("required_source_missing",),
        )
        self.assertEqual(ExecutionOutcome.PARTIAL, execution.outcome)

    def test_version_conflict_is_detected(self) -> None:
        execution = Execution.create_for_locked_task(
            task=self.locked_task(),
            execution_id="EXEC-001",
            request_fingerprint="sha256:" + "c" * 64,
            started_at="2026-08-01T02:00:00Z",
            absolute_deadline_at="2026-08-01T02:15:00Z",
        )
        with self.assertRaises(VersionConflict):
            execution.transition(ExecutionState.COLLECTING, expected_version=2)

    def test_admin_rerun_requires_second_attempt_authorization_and_allowlisted_failure(self) -> None:
        rerun = Execution.create_for_locked_task(
            task=self.locked_task("EXEC-002"),
            execution_id="EXEC-002",
            request_fingerprint="sha256:" + "d" * 64,
            started_at="2026-08-01T03:00:00Z",
            absolute_deadline_at="2026-08-01T03:15:00Z",
            attempt_number=2,
            attempt_kind=AttemptKind.ADMIN_TECHNICAL_RERUN,
            original_execution_id="EXEC-001",
            technical_failure_code="provider_timeout",
            admin_authorized=True,
        )
        self.assertEqual(2, rerun.attempt_number)

        invalid_cases = (
            {"admin_authorized": False, "technical_failure_code": "provider_timeout"},
            {"admin_authorized": True, "technical_failure_code": "bad_analysis"},
            {"admin_authorized": True, "technical_failure_code": "provider_timeout", "attempt_number": 3},
        )
        for index, overrides in enumerate(invalid_cases, start=3):
            values = {
                "task": self.locked_task(f"EXEC-00{index}"),
                "execution_id": f"EXEC-00{index}",
                "request_fingerprint": "sha256:" + "e" * 64,
                "started_at": "2026-08-01T03:00:00Z",
                "absolute_deadline_at": "2026-08-01T03:15:00Z",
                "attempt_number": 2,
                "attempt_kind": AttemptKind.ADMIN_TECHNICAL_RERUN,
                "original_execution_id": "EXEC-001",
                "technical_failure_code": "provider_timeout",
                "admin_authorized": True,
            }
            values.update(overrides)
            with self.subTest(overrides=overrides):
                with self.assertRaises(AdminRerunNotAllowed):
                    Execution.create_for_locked_task(**values)


if __name__ == "__main__":
    unittest.main()
