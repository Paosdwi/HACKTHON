from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.domain.errors import (  # noqa: E402
    FormalExecutionNotReady,
    InvalidStateTransition,
    VersionConflict,
)
from crypto_trust_agent.domain.task import Task, TaskState  # noqa: E402


class TaskStateMachineTests(unittest.TestCase):
    def test_happy_path_requires_preflight_before_execution_lock(self) -> None:
        task = Task(task_id="TASK-001", formal_run_intent=True)
        task = task.transition(TaskState.PLANNED, expected_version=1)
        task = task.transition(TaskState.READY_FOR_PREFLIGHT, expected_version=2)
        task = task.transition(TaskState.PREFLIGHT_PASSED, expected_version=3)
        task = task.lock_for_execution("EXEC-001", expected_version=4)
        task = task.finish_from_execution(
            TaskState.COMPLETED,
            execution_id="EXEC-001",
            expected_version=5,
        )
        self.assertEqual(TaskState.COMPLETED, task.state)
        self.assertEqual(6, task.version)
        self.assertEqual("EXEC-001", task.terminal_execution_id)

    def test_execution_lock_is_rejected_before_preflight(self) -> None:
        task = Task(task_id="TASK-001", formal_run_intent=True)
        with self.assertRaises(FormalExecutionNotReady):
            task.lock_for_execution("EXEC-001", expected_version=1)

    def test_not_ready_and_expired_preflight_return_to_ready_state(self) -> None:
        task = Task(task_id="TASK-001", formal_run_intent=True)
        task = task.transition(TaskState.PLANNED, expected_version=1)
        task = task.transition(TaskState.READY_FOR_PREFLIGHT, expected_version=2)
        retryable = task.transition(TaskState.READY_FOR_PREFLIGHT, expected_version=3)
        passed = retryable.transition(TaskState.PREFLIGHT_PASSED, expected_version=4)
        expired = passed.transition(TaskState.READY_FOR_PREFLIGHT, expected_version=5)
        self.assertEqual(TaskState.READY_FOR_PREFLIGHT, expired.state)

    def test_invalid_transition_and_terminal_regression_are_rejected(self) -> None:
        task = Task(task_id="TASK-001", formal_run_intent=True)
        with self.assertRaises(InvalidStateTransition):
            task.transition(TaskState.PREFLIGHT_PASSED, expected_version=1)

        rejected = task.transition(TaskState.REJECTED, expected_version=1)
        with self.assertRaises(InvalidStateTransition):
            rejected.transition(TaskState.VALIDATING, expected_version=2)

    def test_stale_expected_version_is_detected(self) -> None:
        task = Task(task_id="TASK-001", formal_run_intent=True)
        with self.assertRaises(VersionConflict):
            task.transition(TaskState.PLANNED, expected_version=99)


if __name__ == "__main__":
    unittest.main()
