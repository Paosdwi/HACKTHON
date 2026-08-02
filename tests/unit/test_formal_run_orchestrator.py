from __future__ import annotations

import hashlib
import json
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event, Lock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.repositories import ExecutionRecordDTO, TaskRecordDTO
from crypto_trust_agent.application.orchestration.formal_run import (
    DEFAULT_FORMAL_RUN_BUDGET_POLICY,
    CancellationToken,
    FormalRunBudgetPolicy,
    FormalRunCommand,
    FormalRunOperationConflict,
    FormalRunOrchestrator,
    FormalRunStep,
    FormalRunStepResult,
    FormalRunValidationError,
    StageDefinition,
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

HASH = "sha256:" + "a" * 64
ALL_STEPS = (
    FormalRunStep.INITIALIZATION,
    FormalRunStep.COLLECTION,
    FormalRunStep.EXTRACTION,
    FormalRunStep.NORMALIZATION,
    FormalRunStep.ASSESSMENT,
    FormalRunStep.MARKET_ANALYSIS,
    FormalRunStep.STRATEGY_EVALUATION,
    FormalRunStep.CONTEXT_BOUNDARY,
    FormalRunStep.REASONING_BOUNDARY,
    FormalRunStep.ARTIFACT_PLACEHOLDER,
    FormalRunStep.TERMINAL_TRANSITION,
)


def plan():
    fingerprint = create_request_fingerprint(
        question="What is the BTC market status?",
        assets=("BTC",),
        timeframe_start="2026-07-01T00:00:00Z",
        timeframe_end="2026-08-01T00:00:00Z",
    )
    return build_plan(
        question_type=QuestionType.MARKET_STATUS,
        fingerprint=fingerprint,
        clock_snapshot="2026-08-01T02:00:00Z",
    )


def comparison_plan():
    fingerprint = create_request_fingerprint(
        question="Compare BTC and ETH market status",
        assets=("BTC", "ETH"),
        timeframe_start="2026-07-01T00:00:00Z",
        timeframe_end="2026-08-01T00:00:00Z",
    )
    return build_plan(
        question_type=QuestionType.ASSET_COMPARISON,
        fingerprint=fingerprint,
        clock_snapshot="2026-08-01T02:00:00Z",
    )


def execution(*, state: str = "created") -> ExecutionRecordDTO:
    return ExecutionRecordDTO(
        "EXEC-001",
        "TASK-001",
        HASH,
        1,
        "user_initial",
        None,
        None,
        state,
        "in_progress",
        "2026-08-01T02:15:00Z",
        "2026-08-01T02:00:00Z",
        "2026-08-01T02:00:00Z",
        None,
        (),
        None,
        1,
    )


class BlockingStepExecutor:
    non_production = True

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request):
        del request
        self.calls += 1
        time.sleep(1)
        raise AssertionError("late blocking result must be ignored")


class BlockingFirstEventPublisher:
    non_production = True

    def __init__(self, delegate: FakeEventPublisher) -> None:
        self._delegate = delegate
        self._lock = Lock()
        self.calls = 0

    def publish(self, request):
        with self._lock:
            self.calls += 1
            call_number = self.calls
        if call_number == 1:
            time.sleep(2)
        return self._delegate.publish(request)

    def publish_batch(self, request):
        return self._delegate.publish_batch(request)


class BlockingExecutionRepository:
    non_production = True

    def __init__(self, delegate: FakeExecutionRepository) -> None:
        self._delegate = delegate
        self._lock = Lock()
        self.transition_calls = 0
        self.late_result_completed = Event()

    def transition(self, request):
        with self._lock:
            self.transition_calls += 1
        time.sleep(0.5)
        result = self._delegate.transition(request)
        self.late_result_completed.set()
        return result


class TransitionClockException:
    non_production = True

    def __init__(self, delegate: FakeClock) -> None:
        self._delegate = delegate
        self.now_calls = 0

    def now_utc(self, request):
        self.now_calls += 1
        if self.now_calls == 2:
            raise RuntimeError("Authorization Bearer CLOCK-SECRET")
        return self._delegate.now_utc(request)

    def monotonic_ms(self, request):
        return self._delegate.monotonic_ms(request)


class ParallelProbeStepExecutor:
    non_production = True

    def __init__(self, expected_jobs: int) -> None:
        self._lock = Lock()
        self.expected_jobs = expected_jobs
        self.active_collections = 0
        self.max_active_collections = 0
        self.finished_collections = 0
        self.extraction_overlapped_collection = False

    def execute(self, request):
        if request.step is FormalRunStep.COLLECTION:
            with self._lock:
                self.active_collections += 1
                self.max_active_collections = max(
                    self.max_active_collections, self.active_collections
                )
            if request.planned_job_ids[0].endswith("MARKET"):
                time.sleep(0.01)
            else:
                time.sleep(0.1)
            with self._lock:
                self.active_collections -= 1
                self.finished_collections += 1
            return FormalRunStepResult(
                StepOutcome.SUCCESS,
                completed_job_ids=request.planned_job_ids,
            )
        if request.step is FormalRunStep.EXTRACTION:
            with self._lock:
                if self.finished_collections < self.expected_jobs:
                    self.extraction_overlapped_collection = True
            return FormalRunStepResult(
                StepOutcome.SUCCESS,
                completed_job_ids=request.planned_job_ids,
            )
        return FormalRunStepResult(StepOutcome.SUCCESS)


class ExtractionMilestoneStepExecutor:
    non_production = True

    def __init__(self, clock: FakeClock, expected_jobs: int) -> None:
        self._clock = clock
        self._extraction_barrier = Barrier(expected_jobs)
        self._lock = Lock()
        self._advanced = False

    def execute(self, request):
        if request.step is FormalRunStep.EXTRACTION:
            self._extraction_barrier.wait(timeout=1)
            with self._lock:
                if not self._advanced:
                    self._clock.advance(wall_seconds=480, monotonic_ms=480_000)
                    self._advanced = True
        return FormalRunStepResult(
            StepOutcome.SUCCESS,
            completed_job_ids=request.planned_job_ids,
        )


class FormalRunOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock(
            "2026-08-01T02:00:00Z",
            monotonic_ms=7_500_000,
            runtime_id="receiver-runtime-0001",
        )
        self.store = FakePlatformStore()
        self.store.tasks["TASK-001"] = TaskRecordDTO(
            "TASK-001", "subject-1", HASH, "execution_locked", 3,
            "2026-08-01T02:00:00Z", "EXEC-001",
        )
        self.store.executions["EXEC-001"] = execution()
        self.repository = FakeExecutionRepository(self.store, self.clock)
        self.events = FakeEventPublisher(self.clock)
        self.steps = FakeFormalRunStepExecutor(self.clock)
        self.orchestrator = FormalRunOrchestrator(
            self.clock, self.steps, self.events, self.repository
        )
        self.command = FormalRunCommand(
            operation_id="OP-RUN-001",
            execution=execution(),
            plan=plan(),
        )

    def test_budget_is_fixed_versioned_deterministic_and_covers_required_categories(self) -> None:
        policy = DEFAULT_FORMAL_RUN_BUDGET_POLICY
        self.assertEqual("formal-run-budget-1.0.0", policy.version)
        self.assertEqual(900_000, policy.hard_deadline_ms)
        self.assertEqual(
            {
                "collection",
                "extraction",
                "market_analysis",
                "evidence_processing",
                "reasoning",
                "artifact_publication",
                "safety_margin",
            },
            set(policy.allocations_ms),
        )
        self.assertEqual(policy.canonical_bytes(), policy.canonical_bytes())
        self.assertEqual(450_000, policy.definition(FormalRunStep.COLLECTION).hard_deadline_ms)
        self.assertEqual(810_000, policy.definition(FormalRunStep.REASONING_BOUNDARY).hard_deadline_ms)
        self.assertEqual(900_000, policy.definition(FormalRunStep.ARTIFACT_PLACEHOLDER).hard_deadline_ms)

    def test_success_uses_receiver_local_monotonic_deadline_and_exact_dag_order(self) -> None:
        result = self.orchestrator.execute(self.command)

        self.assertEqual(TerminalOutcome.SUCCESS, result.terminal_outcome)
        self.assertEqual(900_000, result.hard_deadline_ms)
        self.assertEqual("receiver-runtime-0001", result.local_runtime_id)
        self.assertEqual(8_400_000, result.local_deadline_monotonic_ms)
        self.assertEqual(ALL_STEPS, tuple(item.step for item in result.steps))
        self.assertEqual(1, self.steps.call_count(FormalRunStep.INITIALIZATION))
        self.assertEqual(len(self.command.plan.sourcing_jobs), self.steps.call_count(FormalRunStep.COLLECTION))
        self.assertEqual(len(self.command.plan.sourcing_jobs), self.steps.call_count(FormalRunStep.EXTRACTION))
        for step in ALL_STEPS[3:-1]:
            self.assertEqual(1, self.steps.call_count(step))
        self.assertFalse(result.artifact_publication_completed)
        self.assertEqual("stable_t62_boundary_placeholder", result.artifact_boundary_kind)
        collection_requests = tuple(
            request for request in self.steps.requests
            if request.step is FormalRunStep.COLLECTION
        )
        expected_job_ids = tuple(job.job_id for job in self.command.plan.sourcing_jobs)
        self.assertEqual(
            set(expected_job_ids),
            {request.planned_job_ids[0] for request in collection_requests},
        )
        self.assertEqual(expected_job_ids, result.planned_job_ids)
        for request in self.steps.requests:
            self.assertEqual(
                {
                    "schema_version", "operation_id", "deadline_at_utc",
                    "budget_ms", "sent_at_utc", "safety_margin_ms",
                },
                set(request.deadline.to_wire()),
            )
            self.assertNotIn("monotonic", str(request.deadline.to_wire()))

    def test_same_command_replays_without_new_calls_and_payload_conflict_is_rejected(self) -> None:
        first = self.orchestrator.execute(self.command)
        event_count = len(self.events.recorded_events)
        transition_count = len(self.store.replays)
        calls = self.steps.calls
        second = self.orchestrator.execute(self.command)
        self.assertEqual(first, second)
        self.assertEqual(calls, self.steps.calls)
        self.assertEqual(event_count, len(self.events.recorded_events))
        self.assertEqual(transition_count, len(self.store.replays))

        other_plan = build_plan(
            question_type=QuestionType.HYPOTHESIS_VALIDATION,
            fingerprint=create_request_fingerprint(
                question="Validate BTC hypothesis",
                assets=("BTC",),
                timeframe_start="2026-07-01T00:00:00Z",
                timeframe_end="2026-08-01T00:00:00Z",
            ),
            clock_snapshot="2026-08-01T02:00:00Z",
        )
        with self.assertRaises(FormalRunOperationConflict):
            self.orchestrator.execute(FormalRunCommand("OP-RUN-001", execution(), other_plan))

    def test_replay_identity_includes_complete_execution_binding(self) -> None:
        self.orchestrator.execute(self.command)
        changed_execution = replace(execution(), version=2)
        with self.assertRaises(FormalRunOperationConflict):
            self.orchestrator.execute(
                FormalRunCommand("OP-RUN-001", changed_execution, plan())
            )

    def test_blocking_boundary_is_outer_guarded_and_execution_converges_failed(self) -> None:
        blocker = BlockingStepExecutor()
        stages = tuple(
            StageDefinition(
                item.step,
                item.dependencies,
                item.budget_category,
                100 if item.step is FormalRunStep.INITIALIZATION else item.operation_budget_ms,
                1_500 if item.step is FormalRunStep.INITIALIZATION else item.hard_deadline_ms,
                item.invokes_boundary,
            )
            for item in DEFAULT_FORMAL_RUN_BUDGET_POLICY.stages
        )
        policy = FormalRunBudgetPolicy(
            DEFAULT_FORMAL_RUN_BUDGET_POLICY.version,
            DEFAULT_FORMAL_RUN_BUDGET_POLICY.hard_deadline_ms,
            DEFAULT_FORMAL_RUN_BUDGET_POLICY.allocations_ms,
            stages,
        )
        orchestrator = FormalRunOrchestrator(
            self.clock, blocker, self.events, self.repository, budget_policy=policy
        )
        started = time.perf_counter()
        result = orchestrator.execute(self.command)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.5)
        self.assertEqual(TerminalOutcome.TIMED_OUT, result.terminal_outcome)
        self.assertEqual(1, blocker.calls)
        self.assertEqual("failed", self.store.executions["EXEC-001"].state)
        self.assertEqual("formal_run_timed_out", self.store.executions["EXEC-001"].failure_reason_code)

    def test_blocking_execution_transition_is_outer_guarded_without_retry_or_late_progress(self) -> None:
        self.clock.advance(wall_seconds=899.8, monotonic_ms=899_800)
        repository = BlockingExecutionRepository(self.repository)
        orchestrator = FormalRunOrchestrator(
            self.clock, self.steps, self.events, repository
        )
        started = time.perf_counter()
        result = orchestrator.execute(self.command)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.35)
        self.assertEqual(TerminalOutcome.TIMED_OUT, result.terminal_outcome)
        self.assertEqual("deadline_exceeded", result.degraded_reason_codes[0])
        self.assertEqual(1, repository.transition_calls)
        self.assertEqual((), self.steps.calls)
        self.assertEqual((), self.events.recorded_events)

        self.assertTrue(repository.late_result_completed.wait(1))
        replayed = orchestrator.execute(self.command)
        self.assertIs(result, replayed)
        self.assertEqual(1, repository.transition_calls)
        self.assertEqual((), self.steps.calls)
        self.assertEqual((), self.events.recorded_events)

    def test_transition_clock_exception_fails_safely_without_repository_io(self) -> None:
        exploding_clock = TransitionClockException(self.clock)
        orchestrator = FormalRunOrchestrator(
            exploding_clock, self.steps, self.events, self.repository
        )
        result = orchestrator.execute(self.command)

        self.assertEqual(TerminalOutcome.FAILED, result.terminal_outcome)
        self.assertEqual(("unexpected_provider_error",), result.degraded_reason_codes)
        self.assertNotIn("secret", repr(result).lower())
        self.assertEqual("created", self.store.executions["EXEC-001"].state)
        self.assertEqual((), self.steps.calls)
        self.assertEqual((), self.events.recorded_events)

    def test_event_publish_is_outer_guarded_by_receiver_local_deadline(self) -> None:
        self.clock.advance(wall_seconds=898.8, monotonic_ms=898_800)
        blocking_events = BlockingFirstEventPublisher(self.events)
        orchestrator = FormalRunOrchestrator(
            self.clock, self.steps, blocking_events, self.repository
        )
        started = time.perf_counter()
        result = orchestrator.execute(self.command)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.5)
        self.assertEqual(TerminalOutcome.SUCCESS, result.terminal_outcome)
        self.assertGreater(blocking_events.calls, 1)

    def test_two_asset_jobs_run_in_bounded_parallelism_with_collection_extraction_overlap(self) -> None:
        dual_plan = comparison_plan()
        probe = ParallelProbeStepExecutor(len(dual_plan.sourcing_jobs))
        orchestrator = FormalRunOrchestrator(
            self.clock, probe, self.events, self.repository
        )
        result = orchestrator.execute(
            FormalRunCommand("OP-RUN-DUAL", execution(), dual_plan)
        )
        self.assertEqual(TerminalOutcome.SUCCESS, result.terminal_outcome)
        self.assertGreaterEqual(probe.max_active_collections, 2)
        self.assertLessEqual(probe.max_active_collections, 8)
        self.assertTrue(probe.extraction_overlapped_collection)
        self.assertEqual(
            {"BTC", "ETH"},
            {job.asset for job in dual_plan.sourcing_jobs},
        )

    def test_approaching_deadline_uses_exact_deterministic_optional_cutoff(self) -> None:
        self.clock.advance(wall_seconds=880, monotonic_ms=880_000)
        result = self.orchestrator.execute(self.command)
        self.assertEqual(TerminalOutcome.SUCCESS, result.terminal_outcome)
        self.assertIn("optional_source_failed", result.degraded_reason_codes)
        collection_ids = {
            request.planned_job_ids[0]
            for request in self.steps.requests
            if request.step is FormalRunStep.COLLECTION
        }
        self.assertEqual(
            {"JOB-BTC-MARKET", "JOB-BTC-NEWS", "JOB-BTC-OFFICIAL"},
            collection_ids,
        )
        self.assertTrue(
            {"JOB-BTC-ON_CHAIN", "JOB-BTC-SOCIAL", "JOB-BTC-MACRO"}.isdisjoint(
                collection_ids
            )
        )
        self.assertEqual(
            self.steps.call_count(FormalRunStep.COLLECTION),
            self.steps.call_count(FormalRunStep.EXTRACTION),
        )
        self.assertEqual(
            tuple(job.job_id for job in self.command.plan.sourcing_jobs),
            result.planned_job_ids,
        )

    def test_collection_requires_every_planned_job_to_converge(self) -> None:
        jobs = tuple(job.job_id for job in self.command.plan.sourcing_jobs)
        self.steps.configure(
            FormalRunStep.COLLECTION,
            outcome=StepOutcome.SUCCESS,
            completed_job_ids=jobs[:-1],
        )
        result = self.orchestrator.execute(self.command)
        self.assertEqual(TerminalOutcome.FAILED, result.terminal_outcome)
        self.assertEqual("planned_job_convergence_failed", result.step(FormalRunStep.COLLECTION).safe_reason_code)
        self.assertNotIn(FormalRunStep.REASONING_BOUNDARY, self.steps.calls)

    def test_extraction_may_finish_after_collection_deadline_before_its_own_deadline(self) -> None:
        milestone_steps = ExtractionMilestoneStepExecutor(
            self.clock, len(self.command.plan.sourcing_jobs)
        )
        orchestrator = FormalRunOrchestrator(
            self.clock, milestone_steps, self.events, self.repository
        )
        result = orchestrator.execute(self.command)
        self.assertEqual(TerminalOutcome.SUCCESS, result.terminal_outcome)
        self.assertLessEqual(
            result.step(FormalRunStep.COLLECTION).finished_monotonic_ms,
            7_950_000,
        )
        self.assertEqual(
            7_980_000,
            result.step(FormalRunStep.EXTRACTION).finished_monotonic_ms,
        )

    def test_extraction_rejects_missing_foreign_and_duplicate_job_convergence(self) -> None:
        for index, completed_ids in enumerate(((), ("JOB-FOREIGN",)), start=1):
            with self.subTest(completed_ids=completed_ids):
                clock = FakeClock(
                    "2026-08-01T02:00:00Z",
                    monotonic_ms=7_500_000,
                    runtime_id=f"receiver-runtime-000{index + 1}",
                )
                store = FakePlatformStore()
                store.tasks["TASK-001"] = self.store.tasks["TASK-001"]
                store.executions["EXEC-001"] = execution()
                repository = FakeExecutionRepository(store, clock)
                steps = FakeFormalRunStepExecutor(clock)
                steps.configure(
                    FormalRunStep.EXTRACTION,
                    outcome=StepOutcome.SUCCESS,
                    completed_job_ids=completed_ids,
                )
                result = FormalRunOrchestrator(
                    clock, steps, FakeEventPublisher(clock), repository
                ).execute(
                    FormalRunCommand(f"OP-RUN-EXTRACT-{index}", execution(), plan())
                )
                self.assertEqual(TerminalOutcome.FAILED, result.terminal_outcome)
                self.assertEqual(
                    "planned_job_convergence_failed",
                    result.step(FormalRunStep.EXTRACTION).safe_reason_code,
                )
        with self.assertRaises(ValueError):
            FormalRunStepResult(
                StepOutcome.SUCCESS,
                completed_job_ids=("JOB-BTC-MARKET", "JOB-BTC-MARKET"),
            )

    def test_concurrent_same_operation_is_single_flight(self) -> None:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = tuple(
                executor.submit(self.orchestrator.execute, self.command)
                for _ in range(4)
            )
            results = tuple(future.result() for future in futures)
        self.assertTrue(all(item == results[0] for item in results))
        self.assertEqual(1, self.steps.call_count(FormalRunStep.INITIALIZATION))
        self.assertEqual(
            len(self.command.plan.sourcing_jobs),
            self.steps.call_count(FormalRunStep.COLLECTION),
        )

    def test_rejects_plan_hash_and_canonical_projection_tampering(self) -> None:
        original = plan()
        with self.assertRaises(FormalRunValidationError):
            self.orchestrator.execute(
                FormalRunCommand(
                    "OP-RUN-BAD-HASH",
                    execution(),
                    replace(original, canonical_hash="sha256:" + "b" * 64),
                )
            )

        payload = json.loads(original.canonical_json)
        payload["sourcing_jobs"][0]["job_id"] = "JOB-BTC-TAMPERED"
        tampered_json = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        tampered = replace(
            original,
            canonical_json=tampered_json,
            canonical_hash="sha256:" + hashlib.sha256(tampered_json).hexdigest(),
        )
        with self.assertRaises(FormalRunValidationError):
            self.orchestrator.execute(
                FormalRunCommand("OP-RUN-BAD-PROJECTION", execution(), tampered)
            )

    def test_required_failure_is_partial_but_optional_failure_does_not_destroy_run(self) -> None:
        self.steps.configure(
            FormalRunStep.COLLECTION,
            outcome=StepOutcome.DEGRADED,
            required_source_failures=("official",),
            optional_source_failures=("social",),
            safe_reason_code="source_coverage_degraded",
        )
        result = self.orchestrator.execute(self.command)
        self.assertEqual(TerminalOutcome.PARTIAL, result.terminal_outcome)
        self.assertIn("required_source_missing", result.partial_reason_codes)
        self.assertIn("optional_source_failed", result.degraded_reason_codes)
        self.assertEqual("partial", self.store.executions["EXEC-001"].outcome)

    def test_optional_failure_only_remains_success_with_visible_degradation(self) -> None:
        self.steps.configure(
            FormalRunStep.COLLECTION,
            outcome=StepOutcome.DEGRADED,
            optional_source_failures=("social",),
            safe_reason_code="optional_source_failed",
        )
        result = self.orchestrator.execute(self.command)
        self.assertEqual(TerminalOutcome.SUCCESS, result.terminal_outcome)
        self.assertEqual((), result.partial_reason_codes)
        self.assertIn("optional_source_failed", result.degraded_reason_codes)

    def test_timeout_stops_all_later_boundaries_and_never_retries(self) -> None:
        self.steps.configure(
            FormalRunStep.COLLECTION,
            outcome=StepOutcome.TIMEOUT,
            duration_ms=450_000,
            safe_reason_code="collection_timeout",
        )
        result = self.orchestrator.execute(self.command)
        self.assertEqual(TerminalOutcome.TIMED_OUT, result.terminal_outcome)
        self.assertEqual(1, self.steps.call_count(FormalRunStep.INITIALIZATION))
        self.assertGreaterEqual(self.steps.call_count(FormalRunStep.COLLECTION), 1)
        self.assertLessEqual(
            self.steps.call_count(FormalRunStep.COLLECTION),
            len(self.command.plan.sourcing_jobs),
        )
        self.assertTrue(all(item not in self.steps.calls for item in ALL_STEPS[2:-1]))
        self.assertEqual(StepOutcome.SKIPPED, result.step(FormalRunStep.REASONING_BOUNDARY).outcome)

    def test_expired_before_io_makes_no_step_event_or_repository_call(self) -> None:
        self.clock.advance(wall_seconds=900, monotonic_ms=900_000)
        result = self.orchestrator.execute(self.command)
        self.assertEqual(TerminalOutcome.TIMED_OUT, result.terminal_outcome)
        self.assertEqual((), self.steps.calls)
        self.assertEqual((), self.events.recorded_events)
        self.assertEqual("created", self.store.executions["EXEC-001"].state)

    def test_cancellation_after_collection_stops_new_boundaries(self) -> None:
        cancellation = CancellationToken()
        self.steps.cancel_after(FormalRunStep.COLLECTION, cancellation)
        result = self.orchestrator.execute(
            FormalRunCommand("OP-RUN-CANCEL", execution(), plan(), cancellation)
        )
        self.assertEqual(TerminalOutcome.FAILED, result.terminal_outcome)
        self.assertIn("formal_run_cancelled", result.degraded_reason_codes)
        self.assertEqual(1, self.steps.call_count(FormalRunStep.INITIALIZATION))
        self.assertGreaterEqual(self.steps.call_count(FormalRunStep.COLLECTION), 1)
        self.assertLessEqual(
            self.steps.call_count(FormalRunStep.COLLECTION),
            len(self.command.plan.sourcing_jobs),
        )
        self.assertNotIn(FormalRunStep.EXTRACTION, self.steps.calls)
        self.assertNotIn(FormalRunStep.REASONING_BOUNDARY, self.steps.calls)

    def test_quarantine_and_no_verified_evidence_fail_without_reasoning_call(self) -> None:
        self.steps.configure(
            FormalRunStep.ASSESSMENT,
            outcome=StepOutcome.FAILURE,
            safe_reason_code="no_verified_evidence",
            evidence_count=0,
            quarantined_count=3,
        )
        result = self.orchestrator.execute(self.command)
        self.assertEqual(TerminalOutcome.FAILED, result.terminal_outcome)
        self.assertIn("quarantined_evidence_excluded", result.degraded_reason_codes)
        self.assertNotIn(FormalRunStep.REASONING_BOUNDARY, self.steps.calls)

    def test_provider_unavailable_is_typed_degraded_and_contradictions_are_retained(self) -> None:
        self.steps.configure(
            FormalRunStep.STRATEGY_EVALUATION,
            outcome=StepOutcome.SUCCESS,
            contradiction_count=2,
        )
        self.steps.configure(
            FormalRunStep.REASONING_BOUNDARY,
            outcome=StepOutcome.DEGRADED,
            safe_reason_code="reasoning_provider_unavailable",
        )
        result = self.orchestrator.execute(self.command)
        self.assertEqual(TerminalOutcome.PARTIAL, result.terminal_outcome)
        self.assertEqual(2, result.contradiction_count)
        self.assertIn("reasoning_provider_unavailable", result.partial_reason_codes)

    def test_safe_events_cover_start_and_terminal_outcome_without_sensitive_values(self) -> None:
        result = self.orchestrator.execute(self.command)
        self.assertEqual(TerminalOutcome.SUCCESS, result.terminal_outcome)
        self.assertGreaterEqual(len(self.events.recorded_events), 2 * len(ALL_STEPS[:-1]))
        for event in self.events.recorded_events:
            wire = str(event.to_wire()).lower()
            for forbidden in ("bearer ", "authorization", "raw_content", "password"):
                self.assertNotIn(forbidden, wire)
            self.assertEqual(0, event.retry_count)
        terminal_events = [event for event in self.events.recorded_events if event.step == "terminal_transition"]
        self.assertEqual(1, len(terminal_events))
        self.assertEqual("completed", terminal_events[0].status)

    def test_rejects_execution_not_created_by_successful_preflight(self) -> None:
        with self.assertRaises(FormalRunValidationError):
            self.orchestrator.execute(
                FormalRunCommand("OP-RUN-INVALID", execution(state="collecting"), plan())
            )


if __name__ == "__main__":
    unittest.main()
