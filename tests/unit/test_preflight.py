import sys
import unittest
from pathlib import Path
from threading import Lock
from types import MappingProxyType

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.repositories import TaskRecordDTO
from crypto_trust_agent.application.ports.preflight import HealthCheckRequestDTO
from crypto_trust_agent.application.use_cases.preflight import (
    PreflightCommand,
    PreflightRateLimited,
    PreflightTaskNotFound,
    PreflightUseCase,
)
from crypto_trust_agent.infrastructure.fakes import (
    FakeClock,
    FakePlatformStore,
    FakeProviderHealthProbe,
    FakeTaskReadinessProbe,
    FakeTaskRepository,
)


HASH_A = "sha256:" + "a" * 64


class IDs:
    def __init__(self) -> None:
        self._lock = Lock()
        self._value = 0

    def __call__(self, prefix: str) -> str:
        with self._lock:
            self._value += 1
            return f"{prefix}{self._value:08X}"


def seed(store: FakePlatformStore) -> None:
    store.tasks["TASK-001"] = TaskRecordDTO(
        task_id="TASK-001",
        trusted_user_scope="trusted-user-1",
        request_fingerprint=HASH_A,
        state="ready_for_preflight",
        version=1,
        created_at="2026-08-01T02:00:00Z",
    )
    store.proposed_tasks["TASK-001"] = {
        "task_id": "TASK-001",
        "question": "Status of BTC",
        "assets_requested_order": ("BTC",),
        "assets_canonical": ("BTC",),
        "timeframe": {"start": "2026-07-18T00:00:00Z", "end": "2026-08-01T00:00:00Z"},
        "question_type": "market_status",
        "formal_run_intent": True,
        "sourcing_plan": {"ruleset_version": "planner-1.0.0"},
        "analysis_plan": {"ruleset_version": "planner-1.0.0"},
    }


class PreflightUseCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock("2026-08-01T02:00:00Z")
        self.store = FakePlatformStore()
        seed(self.store)
        self.tasks = FakeTaskRepository(self.store, self.clock)
        self.local = FakeTaskReadinessProbe(self.store, self.clock)
        self.nova = FakeProviderHealthProbe(self.clock, provider="nova", capability="extraction_v1")
        self.opus = FakeProviderHealthProbe(self.clock, provider="opus", capability="reasoning_v1")
        self.sagemaker = FakeProviderHealthProbe(self.clock, provider="sagemaker", capability="market_regime_v1")
        self.allowlist = FakeProviderHealthProbe(self.clock, provider="external_allowlist", capability="collector_policy_v1")
        self.use_case = PreflightUseCase(
            self.tasks,
            self.clock,
            self.local,
            self.nova,
            self.opus,
            self.sagemaker,
            self.allowlist,
            IDs(),
        )

    def run_preflight(self):
        return self.use_case.execute(PreflightCommand("trusted-user-1", "TASK-001"))

    def test_success_records_six_checks_and_fixed_single_use_pass_binding(self) -> None:
        result = self.run_preflight()

        self.assertTrue(result.ready)
        self.assertEqual(
            ("input", "official_dataset", "nova", "opus", "sagemaker", "external_allowlist"),
            tuple(item["name"] for item in result.record.checks),
        )
        self.assertEqual(60, result.record.ttl_seconds)
        self.assertEqual(60, (result.record.expires_at.as_datetime() - result.record.checked_at.as_datetime()).total_seconds())
        self.assertEqual("TASK-001", result.record.task_id)
        self.assertEqual(1, result.record.task_version)
        self.assertRegex(result.record.input_lock_hash, r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(result.record.dependency_snapshot_hash, r"^sha256:[0-9a-f]{64}$")
        self.assertIsNone(result.record.consumed_at)
        self.assertIsNone(result.record.consumed_by_execution_id)
        self.assertEqual({}, self.store.executions)
        self.assertEqual({}, self.store.quota)
        self.assertEqual(1, self.local.probe_count)
        self.assertEqual([1, 1, 1, 1], [probe.probe_count for probe in (self.nova, self.opus, self.sagemaker, self.allowlist)])
        self.assertEqual([0, 0, 0, 0], [probe.inference_count for probe in (self.nova, self.opus, self.sagemaker, self.allowlist)])

    def test_unhealthy_dependency_returns_only_safe_code_and_creates_no_execution(self) -> None:
        self.opus.set_health("unhealthy", "model_unavailable", raw_diagnostic="secret endpoint token=canary")

        result = self.run_preflight()

        self.assertFalse(result.ready)
        reasons = result.safe_reason_codes
        self.assertEqual(("model_unavailable",), reasons)
        serialized = str(result.to_safe_dict())
        self.assertNotIn("secret", serialized)
        self.assertNotIn("token", serialized)
        self.assertEqual({}, self.store.executions)
        self.assertEqual({}, self.store.quota)

    def test_frozen_repository_task_payload_is_canonicalized_for_input_hash(self) -> None:
        proposed = self.store.proposed_tasks["TASK-001"]
        self.store.proposed_tasks["TASK-001"] = MappingProxyType(
            {
                **proposed,
                "timeframe": MappingProxyType(dict(proposed["timeframe"])),
                "sourcing_plan": MappingProxyType(dict(proposed["sourcing_plan"])),
                "analysis_plan": MappingProxyType(dict(proposed["analysis_plan"])),
            }
        )
        result = self.run_preflight()
        self.assertTrue(result.ready)
        self.assertRegex(result.record.input_lock_hash, r"^sha256:[0-9a-f]{64}$")

    def test_expired_health_deadline_fails_before_fake_probe_io(self) -> None:
        operation_id = "OP-EXPIRED-HEALTH-001"
        result = self.nova.health_check(
            HealthCheckRequestDTO(
                operation_id=operation_id,
                deadline=DeadlineDTO(
                    schema_version="1.0.0",
                    operation_id=operation_id,
                    deadline_at_utc="2026-08-01T02:00:00Z",
                    budget_ms=3_000,
                    sent_at_utc="2026-08-01T01:59:59Z",
                    safety_margin_ms=100,
                ),
            )
        )
        self.assertIsInstance(result, ErrorResultDTO)
        self.assertEqual("deadline_exceeded", result.error.code)
        self.assertEqual(0, self.nova.probe_count)

    def test_local_probe_exception_is_safely_mapped_without_leaking_diagnostics(self) -> None:
        class ExplodingLocalProbe:
            def check(self, request):
                del request
                raise RuntimeError("secret local path token=canary")

        self.use_case._task_readiness = ExplodingLocalProbe()
        result = self.run_preflight()

        self.assertFalse(result.ready)
        self.assertEqual(("unexpected_provider_error",), result.safe_reason_codes)
        self.assertNotIn("secret", str(result.to_safe_dict()))
        self.assertEqual({}, self.store.executions)
        self.assertEqual({}, self.store.quota)

    def test_fourth_attempt_is_rate_limited_before_any_probe(self) -> None:
        for _ in range(3):
            self.run_preflight()
        counts = (self.local.probe_count, self.nova.probe_count, self.opus.probe_count, self.sagemaker.probe_count, self.allowlist.probe_count)

        with self.assertRaises(PreflightRateLimited) as caught:
            self.run_preflight()

        self.assertGreaterEqual(caught.exception.retry_after_seconds, 1)
        self.assertEqual(counts, (self.local.probe_count, self.nova.probe_count, self.opus.probe_count, self.sagemaker.probe_count, self.allowlist.probe_count))
        self.assertEqual(3, len(self.store.preflights["TASK-001"]))
        self.assertEqual({}, self.store.executions)
        self.assertEqual({}, self.store.quota)

    def test_rate_window_reopens_at_sixty_second_boundary(self) -> None:
        for _ in range(3):
            self.run_preflight()
        self.clock.advance(wall_seconds=60)

        result = self.run_preflight()

        self.assertTrue(result.ready)
        self.assertEqual(4, self.nova.probe_count)

    def test_foreign_principal_cannot_probe_or_learn_task(self) -> None:
        with self.assertRaises(PreflightTaskNotFound):
            self.use_case.execute(PreflightCommand("attacker", "TASK-001"))
        self.assertEqual(0, self.local.probe_count)
        self.assertEqual(0, self.nova.probe_count)


if __name__ == "__main__":
    unittest.main()
