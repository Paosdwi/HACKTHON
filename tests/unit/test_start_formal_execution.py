import sys
import unittest
from datetime import timedelta
from pathlib import Path
from threading import Lock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.common import DeadlineDTO
from crypto_trust_agent.application.dto.repositories import (
    PreflightRecordDTO,
    TaskRecordDTO,
    TransitionExecutionRequestDTO,
)
from crypto_trust_agent.application.use_cases.start_formal_execution import (
    StartFormalExecutionAuthorizationError,
    StartFormalExecutionCommand,
    StartFormalExecutionConflict,
    StartFormalExecutionDependencyError,
    StartFormalExecutionUseCase,
    StartFormalExecutionValidationError,
)
from crypto_trust_agent.infrastructure.fakes import (
    FakeClock,
    FakeExecutionRepository,
    FakePlatformStore,
    FakeTaskRepository,
)

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
PSEUDONYM = "hmac-sha256:k1:" + "d" * 64


class IDs:
    def __init__(self) -> None:
        self._lock = Lock()
        self._value = 0

    def __call__(self, prefix: str) -> str:
        with self._lock:
            self._value += 1
            return f"{prefix}{self._value:08X}"


def deadline(clock: FakeClock, operation_id: str) -> DeadlineDTO:
    now = clock.current_utc()
    return DeadlineDTO(
        schema_version="1.0.0",
        operation_id=operation_id,
        deadline_at_utc=(now.as_datetime() + timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
        budget_ms=2_000,
        sent_at_utc=now,
        safety_margin_ms=100,
    )


def seed_ready(
    store: FakePlatformStore,
    task_id: str,
    *,
    checked_at: str = "2026-08-01T02:00:00Z",
    preflight_id: str = "PF-001",
    subject: str = "subject-1",
    fingerprint: str = HASH_A,
) -> None:
    from crypto_trust_agent.domain.primitives import UtcInstant
    checked = UtcInstant(checked_at)
    expires = (checked.as_datetime() + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    store.tasks[task_id] = TaskRecordDTO(task_id, subject, fingerprint, "ready_for_execution", 2, checked_at)
    store.preflights[task_id] = [
        PreflightRecordDTO(
            preflight_id,
            task_id,
            1,
            HASH_B,
            HASH_C,
            checked_at,
            expires,
            True,
            ({"name": "official_dataset", "required": True, "status": "healthy", "safe_reason_code": None},),
            {"items": ()},
        )
    ]


def start_command(
    task_id: str = "TASK-001",
    preflight_id: str = "PF-001",
    *,
    operation_id: str = "OP-EXEC-START-001",
    execution_id: str = "EXEC-001",
    is_admin: bool = False,
    original_execution_id: str | None = None,
    technical_failure_code: str | None = None,
) -> StartFormalExecutionCommand:
    return StartFormalExecutionCommand(
        trusted_user_scope="subject-1",
        principal_pseudonym=PSEUDONYM,
        is_admin=is_admin,
        task_id=task_id,
        preflight_id=preflight_id,
        input_lock_hash=HASH_B,
        operation_id=operation_id,
        execution_id=execution_id,
        original_execution_id=original_execution_id,
        technical_failure_code=technical_failure_code,
    )


def fail_execution(repository: FakeExecutionRepository, clock: FakeClock, execution_id: str, code: str) -> None:
    collecting = repository.transition(
        TransitionExecutionRequestDTO(
            f"OP-COLLECT-{execution_id}", execution_id, 1, "created", "collecting",
            clock.current_utc(), deadline(clock, f"OP-COLLECT-{execution_id}"),
        )
    )
    failed = repository.transition(
        TransitionExecutionRequestDTO(
            f"OP-FAIL-{execution_id}", execution_id, collecting.version, "collecting", "failed",
            clock.current_utc(), deadline(clock, f"OP-FAIL-{execution_id}"), safe_reason_code=code,
        )
    )
    if hasattr(failed, "error"):
        raise AssertionError(failed.error.code)


class StartFormalExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock("2026-08-01T02:00:00Z")
        self.store = FakePlatformStore()
        seed_ready(self.store, "TASK-001")
        self.tasks = FakeTaskRepository(self.store, self.clock)
        self.executions = FakeExecutionRepository(self.store, self.clock)
        self.use_case = StartFormalExecutionUseCase(self.tasks, self.executions, self.clock, IDs())

    def test_initial_start_uses_persisted_binding_and_fixed_900_second_deadline(self) -> None:
        result = self.use_case.execute(start_command())

        self.assertEqual(900, (
            result.execution.absolute_deadline_at.as_datetime()
            - result.execution.started_at.as_datetime()
        ).total_seconds())
        persisted = self.store.preflights["TASK-001"][-1]
        self.assertEqual(HASH_C, persisted.dependency_snapshot_hash)
        self.assertEqual("EXEC-001", persisted.consumed_by_execution_id)
        self.assertEqual(["EXEC-001"], self.store.quota[("subject-1", HASH_A)])
        self.assertFalse(hasattr(self.tasks, "lock_for_execution"))

    def test_59999_milliseconds_is_accepted_and_60_seconds_is_rejected_without_quota(self) -> None:
        self.clock.advance(wall_seconds=59.999)
        accepted = self.use_case.execute(start_command())
        self.assertEqual("EXEC-001", accepted.execution.execution_id)

        clock = FakeClock("2026-08-01T02:01:00Z")
        store = FakePlatformStore()
        seed_ready(store, "TASK-002", preflight_id="PF-002")
        use_case = StartFormalExecutionUseCase(
            FakeTaskRepository(store, clock), FakeExecutionRepository(store, clock), clock, IDs()
        )
        with self.assertRaises(StartFormalExecutionConflict) as caught:
            use_case.execute(start_command("TASK-002", "PF-002", execution_id="EXEC-002"))
        self.assertEqual("preflight_expired", caught.exception.code)
        self.assertEqual({}, store.executions)
        self.assertEqual({}, store.quota)

    def test_single_use_and_same_operation_replay_return_one_execution(self) -> None:
        command = start_command()
        first = self.use_case.execute(command)
        replay = self.use_case.execute(command)
        self.assertEqual(first, replay)
        self.assertEqual(1, len(self.store.executions))
        self.assertEqual(1, len(self.store.quota[("subject-1", HASH_A)]))

        with self.assertRaises(StartFormalExecutionConflict):
            self.use_case.execute(start_command(operation_id="OP-EXEC-OTHER", execution_id="EXEC-OTHER"))
        self.assertEqual(1, len(self.store.executions))

    def test_stale_binding_and_repository_exception_do_not_consume_quota(self) -> None:
        stale = start_command()
        stale = StartFormalExecutionCommand(**{**stale.__dict__, "input_lock_hash": "sha256:" + "f" * 64}) if hasattr(stale, "__dict__") else StartFormalExecutionCommand(
            stale.trusted_user_scope, stale.principal_pseudonym, stale.is_admin, stale.task_id,
            stale.preflight_id, "sha256:" + "f" * 64, stale.operation_id, stale.execution_id,
        )
        with self.assertRaises(StartFormalExecutionConflict):
            self.use_case.execute(stale)
        self.assertEqual({}, self.store.quota)

        class ExplodingExecutionRepository:
            def acquire_quota_and_create(self, request):
                raise RuntimeError("vendor secret token=canary")
            def list_by_quota_scope(self, request):
                return self.outer.executions.list_by_quota_scope(request)

        exploding = ExplodingExecutionRepository()
        exploding.outer = self
        use_case = StartFormalExecutionUseCase(self.tasks, exploding, self.clock, IDs())
        with self.assertRaises(StartFormalExecutionDependencyError) as caught:
            use_case.execute(start_command(operation_id="OP-EXEC-FAIL", execution_id="EXEC-FAIL"))
        self.assertEqual("unexpected_provider_error", caught.exception.code)
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual({}, self.store.quota)

    def test_admin_rerun_requires_verified_admin_and_frozen_allowlist(self) -> None:
        initial = self.use_case.execute(start_command()).execution
        fail_execution(self.executions, self.clock, initial.execution_id, "provider_timeout")
        seed_ready(self.store, "TASK-002", preflight_id="PF-002")

        with self.assertRaises(StartFormalExecutionAuthorizationError):
            self.use_case.execute(start_command(
                "TASK-002", "PF-002", operation_id="OP-RERUN-NONADMIN", execution_id="EXEC-002",
                original_execution_id="EXEC-001", technical_failure_code="provider_timeout",
            ))
        with self.assertRaises(StartFormalExecutionValidationError):
            self.use_case.execute(start_command(
                "TASK-002", "PF-002", operation_id="OP-RERUN-BAD", execution_id="EXEC-002",
                is_admin=True, original_execution_id="EXEC-001", technical_failure_code="business_failure",
            ))
        self.assertEqual(1, len(self.store.executions))

        rerun = self.use_case.execute(start_command(
            "TASK-002", "PF-002", operation_id="OP-RERUN-OK", execution_id="EXEC-002",
            is_admin=True, original_execution_id="EXEC-001", technical_failure_code="provider_timeout",
        )).execution
        self.assertEqual(2, rerun.attempt_number)
        self.assertEqual("TASK-002", rerun.task_id)
        self.assertEqual(2, len(self.store.quota[("subject-1", HASH_A)]))

    def test_failed_second_attempt_opens_manual_case_and_never_creates_third(self) -> None:
        initial = self.use_case.execute(start_command()).execution
        fail_execution(self.executions, self.clock, initial.execution_id, "provider_timeout")
        seed_ready(self.store, "TASK-002", preflight_id="PF-002")
        rerun = self.use_case.execute(start_command(
            "TASK-002", "PF-002", operation_id="OP-RERUN-OK", execution_id="EXEC-002",
            is_admin=True, original_execution_id="EXEC-001", technical_failure_code="provider_timeout",
        )).execution
        fail_execution(self.executions, self.clock, rerun.execution_id, "provider_timeout")
        seed_ready(self.store, "TASK-003", preflight_id="PF-003")

        result = self.use_case.execute(start_command(
            "TASK-003", "PF-003", operation_id="OP-THIRD", execution_id="EXEC-003",
            is_admin=True, original_execution_id="EXEC-001", technical_failure_code="provider_timeout",
        ))

        self.assertEqual("manual_case_opened", result.outcome)
        self.assertEqual("EXEC-002", result.manual_case.execution_id)
        self.assertEqual("repeated_technical_failure", result.manual_case.reason_code)
        self.assertNotIn("EXEC-003", self.store.executions)
        self.assertIsNone(self.store.preflights["TASK-003"][-1].consumed_at)
        self.assertEqual(2, len(self.store.quota[("subject-1", HASH_A)]))


if __name__ == "__main__":
    unittest.main()
