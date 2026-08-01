from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.repositories import ExecutionRecordDTO, TaskRecordDTO
from crypto_trust_agent.application.orchestration.formal_run import (
    FormalRunCommand,
    FormalRunOrchestrator,
    FormalRunStep,
    StepOutcome,
    TerminalOutcome,
)
from crypto_trust_agent.application.planning import QuestionType, build_plan
from crypto_trust_agent.domain.fingerprint import create_request_fingerprint
from crypto_trust_agent.infrastructure.fakes import (
    FakeClock,
    FakeEventPublisher,
    FakeExecutionRepository,
    FakeFormalRunStepExecutor,
    FakePlatformStore,
)

HASH = "sha256:" + "b" * 64


def build_fixture():
    clock = FakeClock("2026-08-01T02:00:00Z", runtime_id="integration-runtime-01")
    store = FakePlatformStore()
    record = ExecutionRecordDTO(
        "EXEC-INTEGRATION", "TASK-INTEGRATION", HASH, 1, "user_initial", None, None,
        "created", "in_progress", "2026-08-01T02:15:00Z", "2026-08-01T02:00:00Z",
        "2026-08-01T02:00:00Z", None, (), None, 1,
    )
    store.tasks[record.task_id] = TaskRecordDTO(
        record.task_id, "subject-integration", HASH, "execution_locked", 3,
        "2026-08-01T02:00:00Z", record.execution_id,
    )
    store.executions[record.execution_id] = record
    fingerprint = create_request_fingerprint(
        question="Compare ETH and BTC market status",
        assets=("ETH", "BTC"),
        timeframe_start="2026-07-01T00:00:00Z",
        timeframe_end="2026-08-01T00:00:00Z",
    )
    deterministic_plan = build_plan(
        question_type=QuestionType.ASSET_COMPARISON,
        fingerprint=fingerprint,
        clock_snapshot="2026-08-01T02:00:00Z",
    )
    steps = FakeFormalRunStepExecutor(clock)
    events = FakeEventPublisher(clock)
    orchestrator = FormalRunOrchestrator(
        clock, steps, events, FakeExecutionRepository(store, clock)
    )
    command = FormalRunCommand("OP-RUN-INTEGRATION", record, deterministic_plan)
    return clock, store, steps, events, orchestrator, command


class FormalRunOrchestrationIntegrationTests(unittest.TestCase):
    def test_full_fake_dag_converges_with_t52_strategy_and_t62_placeholder(self) -> None:
        _, store, steps, events, orchestrator, command = build_fixture()
        steps.configure(
            FormalRunStep.STRATEGY_EVALUATION,
            outcome=StepOutcome.SUCCESS,
            contradiction_count=3,
        )

        result = orchestrator.execute(command)

        self.assertEqual(TerminalOutcome.SUCCESS, result.terminal_outcome)
        self.assertEqual(3, result.contradiction_count)
        self.assertEqual("completed", store.executions["EXEC-INTEGRATION"].state)
        self.assertFalse(result.artifact_publication_completed)
        self.assertEqual(0, sum(item.retry_count for item in events.recorded_events))
        self.assertTrue(all(getattr(adapter, "non_production", False) for adapter in (steps, events)))

    def test_required_and_optional_source_degradation_converges_partial(self) -> None:
        _, store, steps, _, orchestrator, command = build_fixture()
        steps.configure(
            FormalRunStep.COLLECTION,
            outcome=StepOutcome.DEGRADED,
            required_source_failures=("news",),
            optional_source_failures=("social", "macro"),
            safe_reason_code="source_coverage_degraded",
        )

        result = orchestrator.execute(command)

        self.assertEqual(TerminalOutcome.PARTIAL, result.terminal_outcome)
        self.assertEqual("partial", store.executions["EXEC-INTEGRATION"].outcome)
        self.assertIn("required_source_missing", result.partial_reason_codes)
        self.assertIn("optional_source_failed", result.degraded_reason_codes)


if __name__ == "__main__":
    unittest.main()
