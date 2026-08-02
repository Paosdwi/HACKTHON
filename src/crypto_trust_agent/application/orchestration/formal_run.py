"""Deterministic 900-second Formal Run DAG orchestration.

This module owns control flow and deadline allocation only. Provider adapters
remain behind injected boundaries and are never selected by the planner.
"""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import timedelta
from enum import Enum
from threading import Event, RLock, Thread
from types import MappingProxyType
from typing import Mapping, Protocol

from crypto_trust_agent.application.dto.common import (
    DeadlineDTO,
    DeadlineExceededError,
    ErrorResultDTO,
    build_local_deadline,
)
from crypto_trust_agent.application.dto.repositories import (
    ClockReadRequestDTO,
    EventErrorDTO,
    ExecutionEventDTO,
    ExecutionRecordDTO,
    PublishEventRequestDTO,
    TransitionExecutionRequestDTO,
)
from crypto_trust_agent.application.planning import DeterministicPlan
from crypto_trust_agent.application.ports.repositories import (
    Clock,
    EventPublisher,
    ExecutionRepository,
)
from crypto_trust_agent.domain.primitives import UtcInstant

FORMAL_RUN_BUDGET_VERSION = "formal-run-budget-1.0.0"
FORMAL_RUN_HARD_DEADLINE_MS = 900_000
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class FormalRunValidationError(ValueError):
    """The run was not created by the accepted pre-flight/Execution workflow."""


class FormalRunOperationConflict(RuntimeError):
    """An operation ID was replayed with a different immutable payload."""


class FormalRunDependencyError(RuntimeError):
    """A Core boundary failed with a safe error code."""

    def __init__(
        self,
        code: str,
        *,
        runtime_id: str = "dependency-failed-runtime",
        local_deadline: int = 0,
        instant: int = 0,
    ) -> None:
        self.code = code
        self.runtime_id = runtime_id
        self.local_deadline = local_deadline
        self.instant = instant
        super().__init__(code)


class FormalRunStep(str, Enum):
    INITIALIZATION = "initialization"
    COLLECTION = "collection"
    EXTRACTION = "extraction"
    NORMALIZATION = "normalization"
    ASSESSMENT = "assessment"
    MARKET_ANALYSIS = "market_analysis"
    STRATEGY_EVALUATION = "strategy_evaluation"
    CONTEXT_BOUNDARY = "context_boundary"
    REASONING_BOUNDARY = "reasoning_boundary"
    ARTIFACT_PLACEHOLDER = "artifact_placeholder"
    TERMINAL_TRANSITION = "terminal_transition"


class StepOutcome(str, Enum):
    SUCCESS = "success"
    DEGRADED = "degraded"
    FAILURE = "failure"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


class TerminalOutcome(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class StageDefinition:
    step: FormalRunStep
    dependencies: tuple[FormalRunStep, ...]
    budget_category: str
    operation_budget_ms: int
    hard_deadline_ms: int
    invokes_boundary: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.operation_budget_ms <= FORMAL_RUN_HARD_DEADLINE_MS:
            raise ValueError("invalid operation budget")
        if not 1 <= self.hard_deadline_ms <= FORMAL_RUN_HARD_DEADLINE_MS:
            raise ValueError("invalid stage hard deadline")


_STAGE_DEFINITIONS = (
    StageDefinition(FormalRunStep.INITIALIZATION, (), "evidence_processing", 30_000, 30_000),
    StageDefinition(FormalRunStep.COLLECTION, (FormalRunStep.INITIALIZATION,), "collection", 360_000, 450_000),
    StageDefinition(FormalRunStep.EXTRACTION, (FormalRunStep.COLLECTION,), "extraction", 330_000, 510_000),
    StageDefinition(FormalRunStep.NORMALIZATION, (FormalRunStep.EXTRACTION,), "evidence_processing", 30_000, 510_000),
    StageDefinition(FormalRunStep.ASSESSMENT, (FormalRunStep.NORMALIZATION,), "evidence_processing", 90_000, 690_000),
    StageDefinition(FormalRunStep.MARKET_ANALYSIS, (FormalRunStep.COLLECTION,), "market_analysis", 150_000, 600_000),
    StageDefinition(FormalRunStep.STRATEGY_EVALUATION, (FormalRunStep.ASSESSMENT,), "evidence_processing", 90_000, 690_000),
    StageDefinition(
        FormalRunStep.CONTEXT_BOUNDARY,
        (FormalRunStep.STRATEGY_EVALUATION, FormalRunStep.MARKET_ANALYSIS),
        "evidence_processing",
        30_000,
        690_000,
    ),
    StageDefinition(FormalRunStep.REASONING_BOUNDARY, (FormalRunStep.CONTEXT_BOUNDARY,), "reasoning", 150_000, 810_000),
    StageDefinition(FormalRunStep.ARTIFACT_PLACEHOLDER, (FormalRunStep.REASONING_BOUNDARY,), "artifact_publication", 25_000, 900_000),
    StageDefinition(FormalRunStep.TERMINAL_TRANSITION, (FormalRunStep.ARTIFACT_PLACEHOLDER,), "artifact_publication", 5_000, 900_000, False),
)


@dataclass(frozen=True, slots=True)
class FormalRunBudgetPolicy:
    version: str
    hard_deadline_ms: int
    allocations_ms: Mapping[str, int]
    stages: tuple[StageDefinition, ...]

    def __post_init__(self) -> None:
        if self.version != FORMAL_RUN_BUDGET_VERSION or self.hard_deadline_ms != 900_000:
            raise ValueError("unsupported formal run budget")
        expected = {
            "collection",
            "extraction",
            "market_analysis",
            "evidence_processing",
            "reasoning",
            "artifact_publication",
            "safety_margin",
        }
        if set(self.allocations_ms) != expected:
            raise ValueError("incomplete formal run budget")
        object.__setattr__(self, "allocations_ms", MappingProxyType(dict(self.allocations_ms)))
        object.__setattr__(self, "stages", tuple(self.stages))

    def definition(self, step: FormalRunStep) -> StageDefinition:
        return next(item for item in self.stages if item.step is step)

    def canonical_bytes(self) -> bytes:
        payload = {
            "allocations_ms": dict(sorted(self.allocations_ms.items())),
            "hard_deadline_ms": self.hard_deadline_ms,
            "stages": [
                {
                    "budget_category": item.budget_category,
                    "dependencies": [dependency.value for dependency in item.dependencies],
                    "hard_deadline_ms": item.hard_deadline_ms,
                    "operation_budget_ms": item.operation_budget_ms,
                    "step": item.step.value,
                }
                for item in self.stages
            ],
            "version": self.version,
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


DEFAULT_FORMAL_RUN_BUDGET_POLICY = FormalRunBudgetPolicy(
    FORMAL_RUN_BUDGET_VERSION,
    FORMAL_RUN_HARD_DEADLINE_MS,
    {
        "collection": 360_000,
        "extraction": 330_000,
        "market_analysis": 150_000,
        "evidence_processing": 180_000,
        "reasoning": 150_000,
        "artifact_publication": 25_000,
        "safety_margin": 1_000,
    },
    _STAGE_DEFINITIONS,
)


class CancellationToken:
    """In-process cancellation signal; no runtime clock value crosses a wire."""

    __slots__ = ("_cancelled",)

    def __init__(self) -> None:
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


@dataclass(frozen=True, slots=True)
class FormalRunCommand:
    operation_id: str
    execution: ExecutionRecordDTO
    plan: DeterministicPlan
    cancellation: CancellationToken = field(default_factory=CancellationToken, compare=False)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"^OP-[A-Z0-9][A-Z0-9._:-]{0,126}$", self.operation_id):
            raise FormalRunValidationError("invalid operation_id")
        if not isinstance(self.execution, ExecutionRecordDTO) or not isinstance(self.plan, DeterministicPlan):
            raise FormalRunValidationError("invalid formal run command")
        if not isinstance(self.cancellation, CancellationToken):
            raise FormalRunValidationError("invalid cancellation signal")


@dataclass(frozen=True, slots=True)
class FormalRunStepRequest:
    operation_id: str
    task_id: str
    execution_id: str
    step: FormalRunStep
    plan_hash: str
    budget_version: str
    deadline: DeadlineDTO
    planned_job_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FormalRunStepResult:
    outcome: StepOutcome
    safe_reason_code: str | None = None
    required_source_failures: tuple[str, ...] = ()
    optional_source_failures: tuple[str, ...] = ()
    evidence_count: int | None = None
    quarantined_count: int = 0
    contradiction_count: int = 0
    completed_job_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.safe_reason_code is not None and not _SAFE_CODE.fullmatch(self.safe_reason_code):
            raise ValueError("invalid safe reason code")
        object.__setattr__(self, "required_source_failures", tuple(sorted(set(self.required_source_failures))))
        object.__setattr__(self, "optional_source_failures", tuple(sorted(set(self.optional_source_failures))))
        for value in (self.evidence_count, self.quarantined_count, self.contradiction_count):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("step counters must be nonnegative integers")
        completed = tuple(self.completed_job_ids)
        if len(completed) != len(set(completed)) or any(
            not isinstance(item, str) or not item.startswith("JOB-") for item in completed
        ):
            raise ValueError("invalid completed_job_ids")
        object.__setattr__(self, "completed_job_ids", completed)


class FormalRunStepExecutor(Protocol):
    def execute(self, request: FormalRunStepRequest) -> FormalRunStepResult: ...


@dataclass(frozen=True, slots=True)
class StepRecord:
    step: FormalRunStep
    operation_id: str
    outcome: StepOutcome
    started_monotonic_ms: int
    finished_monotonic_ms: int
    safe_reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class FormalRunResult:
    operation_id: str
    task_id: str
    execution_id: str
    budget_version: str
    hard_deadline_ms: int
    local_runtime_id: str
    local_deadline_monotonic_ms: int
    terminal_outcome: TerminalOutcome
    steps: tuple[StepRecord, ...]
    partial_reason_codes: tuple[str, ...]
    degraded_reason_codes: tuple[str, ...]
    contradiction_count: int
    planned_job_ids: tuple[str, ...]
    artifact_boundary_kind: str = "stable_t62_boundary_placeholder"
    artifact_publication_completed: bool = False

    def step(self, step: FormalRunStep) -> StepRecord:
        return next(item for item in self.steps if item.step is step)


class FormalRunOrchestrator:
    """Execute the approved DAG once; all retry counts remain zero."""

    def __init__(
        self,
        clock: Clock,
        step_executor: FormalRunStepExecutor,
        event_publisher: EventPublisher,
        execution_repository: ExecutionRepository,
        *,
        budget_policy: FormalRunBudgetPolicy = DEFAULT_FORMAL_RUN_BUDGET_POLICY,
    ) -> None:
        self._clock = clock
        self._steps = step_executor
        self._events = event_publisher
        self._executions = execution_repository
        self._policy = budget_policy
        self._completed: dict[str, tuple[tuple[object, bytes, bytes], FormalRunResult]] = {}
        self._operation_locks: dict[str, RLock] = {}
        self._operation_locks_guard = RLock()

    def execute(self, command: FormalRunCommand) -> FormalRunResult:
        with self._operation_locks_guard:
            operation_lock = self._operation_locks.setdefault(command.operation_id, RLock())
        with operation_lock:
            try:
                return self._execute_once(command)
            except FormalRunDependencyError as error:
                identity = (
                    command.execution,
                    command.plan.canonical_json,
                    self._policy.canonical_bytes(),
                )
                result = self._dependency_failed_result(command, error)
                self._completed[command.operation_id] = (identity, result)
                return result

    def _execute_once(self, command: FormalRunCommand) -> FormalRunResult:
        identity = (
            command.execution,
            command.plan.canonical_json,
            self._policy.canonical_bytes(),
        )
        replay = self._completed.get(command.operation_id)
        if replay is not None:
            if replay[0] != identity:
                raise FormalRunOperationConflict("operation_id payload conflict")
            return replay[1]
        self._validate(command)

        now_utc, now_monotonic, runtime_id = self._clock_snapshot(command.operation_id)
        utc_remaining_ms = (
            command.execution.absolute_deadline_at.as_datetime() - now_utc.as_datetime()
        ) // timedelta(milliseconds=1)
        if utc_remaining_ms <= 0:
            result = self._expired_result(command, runtime_id, now_monotonic)
            self._completed[command.operation_id] = (identity, result)
            return result
        local_deadline = now_monotonic + min(self._policy.hard_deadline_ms, utc_remaining_ms)
        t0_monotonic = now_monotonic
        current_execution = command.execution
        records: list[StepRecord] = []
        partial: set[str] = set()
        degraded: set[str] = set()
        contradiction_count = 0
        terminal: TerminalOutcome | None = None
        completed_steps: set[FormalRunStep] = set()
        planned_job_ids = tuple(
            job.job_id for job in sorted(command.plan.sourcing_jobs, key=lambda item: item.priority)
        )
        pending_extraction: FormalRunStepResult | None = None

        current_execution = self._transition(
            command,
            current_execution,
            "collecting",
            now_utc,
            local_deadline,
            boundary_deadline=min(
                local_deadline,
                t0_monotonic
                + self._policy.definition(FormalRunStep.INITIALIZATION).hard_deadline_ms,
            ),
        )

        for definition in self._policy.stages[:-1]:
            if terminal is not None:
                records.append(self._skipped_record(command, definition.step, "upstream_terminal", self._monotonic(command.operation_id)))
                continue
            if command.cancellation.cancelled:
                terminal = TerminalOutcome.FAILED
                degraded.add("formal_run_cancelled")
                records.append(self._skipped_record(command, definition.step, "formal_run_cancelled", self._monotonic(command.operation_id)))
                continue
            current_monotonic = self._monotonic(command.operation_id)
            stage_deadline = t0_monotonic + definition.hard_deadline_ms
            if current_monotonic >= local_deadline or current_monotonic >= stage_deadline:
                terminal = TerminalOutcome.TIMED_OUT
                records.append(self._timeout_record(command, definition.step, current_monotonic))
                continue
            if any(dependency not in completed_steps for dependency in definition.dependencies):
                terminal = TerminalOutcome.FAILED
                records.append(self._skipped_record(command, definition.step, "dag_dependency_failed", current_monotonic))
                continue

            step_operation = self._step_operation(command.operation_id, definition.step)
            deadline = self._step_deadline(
                command,
                definition,
                step_operation,
                now_utc,
                current_monotonic - t0_monotonic,
            )
            request = FormalRunStepRequest(
                step_operation,
                command.execution.task_id,
                command.execution.execution_id,
                definition.step,
                command.plan.canonical_hash,
                self._policy.version,
                deadline,
                planned_job_ids if definition.step is FormalRunStep.COLLECTION else (),
            )
            self._publish_step_event(command, request, StepOutcome.SUCCESS, started=True, duration_ms=0, safe_reason_code=None, root_deadline=local_deadline)
            collection_finished_override: int | None = None
            if definition.step is FormalRunStep.COLLECTION:
                step_result, pending_extraction, collection_finished_override = self._execute_sourcing_dag(
                    command,
                    request,
                    now_utc,
                    t0_monotonic,
                    local_deadline,
                )
            elif definition.step is FormalRunStep.EXTRACTION and pending_extraction is not None:
                step_result = pending_extraction
            else:
                step_result = self._execute_with_guard(command, request)
            if (
                definition.step in {FormalRunStep.COLLECTION, FormalRunStep.EXTRACTION}
                and step_result.outcome is not StepOutcome.TIMEOUT
                and step_result.completed_job_ids != planned_job_ids
            ):
                step_result = FormalRunStepResult(
                    StepOutcome.FAILURE,
                    "planned_job_convergence_failed",
                    completed_job_ids=step_result.completed_job_ids,
                )
            finished = (
                collection_finished_override
                if collection_finished_override is not None
                else self._monotonic(command.operation_id)
            )
            effective_outcome = step_result.outcome
            if finished >= local_deadline or finished > stage_deadline:
                effective_outcome = StepOutcome.TIMEOUT
                step_result = FormalRunStepResult(
                    StepOutcome.TIMEOUT,
                    step_result.safe_reason_code or "step_timeout",
                    step_result.required_source_failures,
                    step_result.optional_source_failures,
                    step_result.evidence_count,
                    step_result.quarantined_count,
                    step_result.contradiction_count,
                    step_result.completed_job_ids,
                )
            record = StepRecord(
                definition.step,
                step_operation,
                effective_outcome,
                current_monotonic,
                finished,
                step_result.safe_reason_code,
            )
            records.append(record)
            self._publish_step_event(
                command,
                request,
                effective_outcome,
                started=False,
                duration_ms=max(0, finished - current_monotonic),
                safe_reason_code=step_result.safe_reason_code,
                root_deadline=local_deadline,
            )

            if step_result.required_source_failures:
                partial.add("required_source_missing")
            if step_result.optional_source_failures:
                degraded.add("optional_source_failed")
            if step_result.quarantined_count:
                degraded.add("quarantined_evidence_excluded")
            contradiction_count = max(contradiction_count, step_result.contradiction_count)

            if effective_outcome is StepOutcome.TIMEOUT:
                terminal = TerminalOutcome.TIMED_OUT
            elif effective_outcome is StepOutcome.FAILURE:
                terminal = TerminalOutcome.FAILED
            elif effective_outcome is StepOutcome.DEGRADED:
                if step_result.safe_reason_code == "reasoning_provider_unavailable":
                    partial.add("reasoning_provider_unavailable")
                elif step_result.safe_reason_code:
                    degraded.add(step_result.safe_reason_code)
                completed_steps.add(definition.step)
            elif effective_outcome is StepOutcome.SKIPPED:
                if step_result.safe_reason_code:
                    degraded.add(step_result.safe_reason_code)
                completed_steps.add(definition.step)
            else:
                completed_steps.add(definition.step)

            if step_result.safe_reason_code == "no_verified_evidence":
                terminal = TerminalOutcome.FAILED
            if step_result.safe_reason_code == "official_dataset_unavailable":
                terminal = TerminalOutcome.FAILED

            if definition.step is FormalRunStep.EXTRACTION and terminal is None:
                current_execution = self._transition(
                    command,
                    current_execution,
                    "extracting",
                    now_utc,
                    local_deadline,
                    boundary_deadline=stage_deadline,
                )
            elif definition.step is FormalRunStep.CONTEXT_BOUNDARY and terminal is None:
                current_execution = self._transition(
                    command,
                    current_execution,
                    "reasoning",
                    now_utc,
                    local_deadline,
                    boundary_deadline=stage_deadline,
                )
            elif definition.step is FormalRunStep.REASONING_BOUNDARY and terminal is None:
                current_execution = self._transition(
                    command,
                    current_execution,
                    "validating",
                    now_utc,
                    local_deadline,
                    boundary_deadline=stage_deadline,
                )
            elif definition.step is FormalRunStep.ARTIFACT_PLACEHOLDER and terminal is None:
                current_execution = self._transition(
                    command,
                    current_execution,
                    "publishing",
                    now_utc,
                    local_deadline,
                    boundary_deadline=stage_deadline,
                )

        if terminal is None:
            terminal = TerminalOutcome.PARTIAL if partial else TerminalOutcome.SUCCESS
        terminal_started = self._monotonic(command.operation_id)
        terminal_operation = self._step_operation(command.operation_id, FormalRunStep.TERMINAL_TRANSITION)
        terminal_record = StepRecord(
            FormalRunStep.TERMINAL_TRANSITION,
            terminal_operation,
            StepOutcome.SUCCESS,
            terminal_started,
            terminal_started,
            None,
        )
        records.append(terminal_record)

        if terminal_started < local_deadline and current_execution.state != "created":
            to_state = "completed" if terminal in {TerminalOutcome.SUCCESS, TerminalOutcome.PARTIAL} else "failed"
            reason = None
            if terminal is TerminalOutcome.TIMED_OUT:
                reason = "formal_run_timed_out"
            elif terminal is TerminalOutcome.FAILED:
                reason = "formal_run_failed"
            current_execution = self._transition(
                command,
                current_execution,
                to_state,
                now_utc,
                local_deadline,
                safe_reason_code=reason,
                partial_reason_codes=tuple(sorted(partial)),
            )
            del current_execution

        self._publish_terminal_event(command, terminal_operation, terminal, local_deadline)
        result = FormalRunResult(
            command.operation_id,
            command.execution.task_id,
            command.execution.execution_id,
            self._policy.version,
            self._policy.hard_deadline_ms,
            runtime_id,
            local_deadline,
            terminal,
            tuple(records),
            tuple(sorted(partial)),
            tuple(sorted(degraded)),
            contradiction_count,
            planned_job_ids,
        )
        self._completed[command.operation_id] = (identity, result)
        return result

    def _execute_sourcing_dag(
        self,
        command: FormalRunCommand,
        collection_request: FormalRunStepRequest,
        root_now_utc: UtcInstant,
        t0_monotonic: int,
        root_deadline: int,
    ) -> tuple[FormalRunStepResult, FormalRunStepResult, int]:
        """Run per-job collection→extraction pipelines with bounded parallelism."""

        collection_definition = self._policy.definition(FormalRunStep.COLLECTION)
        extraction_definition = self._policy.definition(FormalRunStep.EXTRACTION)
        ordered_jobs = tuple(sorted(command.plan.sourcing_jobs, key=lambda item: item.priority))
        admission_remaining = max(0, root_deadline - self._monotonic(command.operation_id))
        optional_cutoff = False
        admitted_job_ids: set[str] = set()
        for job in ordered_jobs:
            if job.requirement.value != "optional":
                admitted_job_ids.add(job.job_id)
                continue
            if (
                optional_cutoff
                or admission_remaining
                <= job.budget_ms + self._policy.allocations_ms["safety_margin"]
            ):
                optional_cutoff = True
                continue
            admitted_job_ids.add(job.job_id)

        def run_job(job: object) -> tuple[object, FormalRunStepResult, FormalRunStepResult, int]:
            current = self._monotonic(command.operation_id)
            if job.job_id not in admitted_job_ids:
                skipped = FormalRunStepResult(
                    StepOutcome.SKIPPED,
                    "low_priority_deadline_stop",
                    optional_source_failures=(job.category,),
                    completed_job_ids=(job.job_id,),
                )
                return job, skipped, FormalRunStepResult(
                    StepOutcome.SKIPPED,
                    "upstream_collection_skipped",
                    completed_job_ids=(job.job_id,),
                ), current

            collect_operation = self._derived_operation(
                command.operation_id, f"JOB:{job.job_id[4:]}:COLLECT"
            )
            collect_deadline = self._step_deadline(
                command,
                collection_definition,
                collect_operation,
                root_now_utc,
                current - t0_monotonic,
            )
            collect_request = replace(
                collection_request,
                operation_id=collect_operation,
                deadline=collect_deadline,
                planned_job_ids=(job.job_id,),
            )
            collected = self._execute_with_guard(command, collect_request)
            collection_finished = self._monotonic(command.operation_id)
            if (
                collected.outcome is not StepOutcome.TIMEOUT
                and collected.completed_job_ids != (job.job_id,)
            ):
                collected = FormalRunStepResult(
                    StepOutcome.FAILURE,
                    "planned_job_convergence_failed",
                )
            if collected.outcome in {StepOutcome.FAILURE, StepOutcome.TIMEOUT} or command.cancellation.cancelled:
                return job, collected, FormalRunStepResult(
                    StepOutcome.SKIPPED,
                    "upstream_collection_failed",
                    completed_job_ids=(job.job_id,),
                ), collection_finished

            extract_operation = self._derived_operation(
                command.operation_id, f"JOB:{job.job_id[4:]}:EXTRACT"
            )
            extract_current = self._monotonic(command.operation_id)
            extract_deadline = self._step_deadline(
                command,
                extraction_definition,
                extract_operation,
                root_now_utc,
                extract_current - t0_monotonic,
            )
            extract_request = FormalRunStepRequest(
                extract_operation,
                command.execution.task_id,
                command.execution.execution_id,
                FormalRunStep.EXTRACTION,
                command.plan.canonical_hash,
                self._policy.version,
                extract_deadline,
                (job.job_id,),
            )
            extracted = self._execute_with_guard(command, extract_request)
            if (
                extracted.outcome is not StepOutcome.TIMEOUT
                and extracted.completed_job_ids != (job.job_id,)
            ):
                extracted = FormalRunStepResult(
                    StepOutcome.FAILURE,
                    "planned_job_convergence_failed",
                )
            return job, collected, extracted, collection_finished

        workers = max(1, min(8, len(ordered_jobs)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="formal-run-job") as executor:
            futures = [executor.submit(run_job, job) for job in ordered_jobs]
            job_results = tuple(future.result() for future in futures)

        return (
            self._aggregate_job_results(job_results, index=1, step=FormalRunStep.COLLECTION),
            self._aggregate_job_results(job_results, index=2, step=FormalRunStep.EXTRACTION),
            max(values[3] for values in job_results),
        )

    @staticmethod
    def _aggregate_job_results(
        job_results: tuple[
            tuple[object, FormalRunStepResult, FormalRunStepResult, int], ...
        ],
        *,
        index: int,
        step: FormalRunStep,
    ) -> FormalRunStepResult:
        selected = tuple((job, values[index]) for values in job_results for job in (values[0],))
        outcomes = {result.outcome for _, result in selected}
        required_failures: set[str] = set()
        optional_failures: set[str] = set()
        safe_codes: list[str] = []
        completed: set[str] = set()
        evidence_count = 0
        has_evidence_count = False
        quarantined_count = 0
        contradiction_count = 0

        for job, result in selected:
            required_failures.update(result.required_source_failures)
            optional_failures.update(result.optional_source_failures)
            completed.update(result.completed_job_ids)
            if result.safe_reason_code:
                safe_codes.append(result.safe_reason_code)
            if result.evidence_count is not None:
                evidence_count += result.evidence_count
                has_evidence_count = True
            quarantined_count += result.quarantined_count
            contradiction_count = max(contradiction_count, result.contradiction_count)
            if step is FormalRunStep.COLLECTION and result.outcome in {
                StepOutcome.FAILURE, StepOutcome.SKIPPED, StepOutcome.TIMEOUT
            }:
                if job.requirement.value == "required":
                    required_failures.add(job.category)
                else:
                    optional_failures.add(job.category)

        if StepOutcome.TIMEOUT in outcomes:
            outcome = StepOutcome.TIMEOUT
        elif StepOutcome.FAILURE in outcomes and step is FormalRunStep.EXTRACTION:
            outcome = StepOutcome.FAILURE
        elif outcomes == {StepOutcome.SUCCESS}:
            outcome = StepOutcome.SUCCESS
        else:
            outcome = StepOutcome.DEGRADED
        reason = None
        if outcome is StepOutcome.TIMEOUT:
            reason = "step_timeout"
        elif outcome is StepOutcome.FAILURE:
            reason = next((code for code in safe_codes if code == "no_verified_evidence"), "step_failed")
        elif outcome is StepOutcome.DEGRADED:
            reason = "source_coverage_degraded" if step is FormalRunStep.COLLECTION else "extraction_degraded"

        return FormalRunStepResult(
            outcome,
            reason,
            tuple(sorted(required_failures)),
            tuple(sorted(optional_failures)),
            evidence_count if has_evidence_count else None,
            quarantined_count,
            contradiction_count,
            tuple(job.job_id for job, _ in selected if job.job_id in completed),
        )

    def _execute_with_guard(
        self,
        command: FormalRunCommand,
        request: FormalRunStepRequest,
    ) -> FormalRunStepResult:
        utc = self._clock.now_utc(ClockReadRequestDTO(
            self._derived_operation(command.operation_id, f"CLOCK:GUARD:UTC:{request.step.value.upper()}")
        ))
        monotonic = self._clock.monotonic_ms(ClockReadRequestDTO(
            self._derived_operation(command.operation_id, f"CLOCK:GUARD:MONO:{request.step.value.upper()}")
        ))
        if isinstance(utc, ErrorResultDTO) or isinstance(monotonic, ErrorResultDTO):
            return FormalRunStepResult(StepOutcome.FAILURE, "clock_unavailable")
        try:
            local = build_local_deadline(
                request.deadline,
                provider_timeout_ms=request.deadline.budget_ms,
                now_utc=utc.utc.as_datetime(),
                now_monotonic_ms=monotonic.monotonic_ms,
                runtime_id=monotonic.runtime_id,
            )
        except DeadlineExceededError:
            return FormalRunStepResult(StepOutcome.TIMEOUT, "deadline_exceeded")

        completed = Event()
        payload: list[object] = []

        def invoke() -> None:
            try:
                payload.append(self._steps.execute(request))
            except BaseException:
                payload.append(FormalRunStepResult(StepOutcome.FAILURE, "unexpected_provider_error"))
            finally:
                completed.set()

        Thread(target=invoke, name=f"formal-run-{request.step.value}", daemon=True).start()
        if not completed.wait(local.effective_timeout_ms / 1_000):
            return FormalRunStepResult(StepOutcome.TIMEOUT, "step_timeout")
        result = payload[0]
        if not isinstance(result, FormalRunStepResult):
            return FormalRunStepResult(StepOutcome.FAILURE, "unexpected_provider_error")
        return result

    def _validate(self, command: FormalRunCommand) -> None:
        execution = command.execution
        plan = command.plan
        if execution.state != "created" or execution.outcome != "in_progress":
            raise FormalRunValidationError("Execution must be newly created after pre-flight")
        duration = execution.absolute_deadline_at.as_datetime() - execution.started_at.as_datetime()
        if duration != timedelta(seconds=900):
            raise FormalRunValidationError("Execution must have the fixed 900-second deadline")
        if plan.ruleset_version != "planner-1.0.0":
            raise FormalRunValidationError("unsupported planner ruleset")
        expected_hash = "sha256:" + hashlib.sha256(plan.canonical_json).hexdigest()
        if plan.canonical_hash != expected_hash:
            raise FormalRunValidationError("plan canonical_hash mismatch")
        try:
            canonical_payload = json.loads(plan.canonical_json.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FormalRunValidationError("plan canonical_json is invalid") from error
        if plan.canonical_json != json.dumps(
            canonical_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"):
            raise FormalRunValidationError("plan canonical_json is not canonical")
        projected_payload = {
            "analysis_steps": list(plan.analysis_steps),
            "answer_dimensions": list(plan.answer_dimensions),
            "assets_canonical": list(plan.assets_canonical),
            "assets_requested_order": list(plan.assets_requested_order),
            "clock_snapshot": plan.clock_snapshot,
            "question_type": plan.question_type.value,
            "reporting_range": {
                "end": plan.reporting_end,
                "start": plan.reporting_start,
            },
            "ruleset_version": plan.ruleset_version,
            "source_requirements": {
                key: value.value for key, value in plan.source_requirements.items()
            },
            "sourcing_jobs": [
                {
                    "asset": job.asset,
                    "budget_ms": job.budget_ms,
                    "category": job.category,
                    "fallback_policy": job.fallback_policy,
                    "job_id": job.job_id,
                    "priority": job.priority,
                    "query": job.query,
                    "range_end": job.range_end,
                    "range_start": job.range_start,
                    "requirement": job.requirement.value,
                }
                for job in plan.sourcing_jobs
            ],
            "warmup_start": plan.warmup_start,
        }
        if canonical_payload != projected_payload:
            raise FormalRunValidationError("plan canonical projection mismatch")

    def _clock_snapshot(self, root_operation: str) -> tuple[UtcInstant, int, str]:
        utc = self._clock.now_utc(ClockReadRequestDTO(self._derived_operation(root_operation, "CLOCK:UTC")))
        monotonic = self._clock.monotonic_ms(ClockReadRequestDTO(self._derived_operation(root_operation, "CLOCK:MONOTONIC")))
        if isinstance(utc, ErrorResultDTO) or isinstance(monotonic, ErrorResultDTO):
            code = utc.error.code if isinstance(utc, ErrorResultDTO) else monotonic.error.code
            raise FormalRunDependencyError(code)
        return utc.utc, monotonic.monotonic_ms, monotonic.runtime_id

    def _monotonic(self, root_operation: str) -> int:
        result = self._clock.monotonic_ms(ClockReadRequestDTO(self._derived_operation(root_operation, "CLOCK:READ")))
        if isinstance(result, ErrorResultDTO):
            raise FormalRunDependencyError(result.error.code)
        return result.monotonic_ms

    def _step_deadline(
        self,
        command: FormalRunCommand,
        definition: StageDefinition,
        operation_id: str,
        root_now_utc: UtcInstant,
        elapsed_ms: int,
    ) -> DeadlineDTO:
        stage_remaining = max(1, definition.hard_deadline_ms - elapsed_ms)
        budget = min(definition.operation_budget_ms, stage_remaining)
        sent = self._clock.now_utc(ClockReadRequestDTO(self._derived_operation(command.operation_id, "CLOCK:SEND")))
        if isinstance(sent, ErrorResultDTO):
            raise FormalRunDependencyError(sent.error.code)
        stage_absolute = root_now_utc.as_datetime() + timedelta(milliseconds=definition.hard_deadline_ms)
        absolute = min(command.execution.absolute_deadline_at.as_datetime(), stage_absolute)
        return DeadlineDTO(
            "1.0.0",
            operation_id,
            absolute.isoformat().replace("+00:00", "Z"),
            budget,
            sent.utc,
            self._policy.allocations_ms["safety_margin"],
        )

    def _transition(
        self,
        command: FormalRunCommand,
        execution: ExecutionRecordDTO,
        to_state: str,
        occurred_at: UtcInstant,
        root_deadline: int,
        *,
        boundary_deadline: int | None = None,
        safe_reason_code: str | None = None,
        partial_reason_codes: tuple[str, ...] = (),
    ) -> ExecutionRecordDTO:
        monotonic = None
        try:
            monotonic = self._clock.monotonic_ms(ClockReadRequestDTO(
                self._derived_operation(command.operation_id, "CLOCK:STATE:MONO")
            ))
            if isinstance(monotonic, ErrorResultDTO):
                raise FormalRunDependencyError(monotonic.error.code)
            utc = self._clock.now_utc(ClockReadRequestDTO(
                self._derived_operation(command.operation_id, "CLOCK:STATE:UTC")
            ))
            if isinstance(utc, ErrorResultDTO):
                raise FormalRunDependencyError(
                    utc.error.code,
                    runtime_id=monotonic.runtime_id,
                    local_deadline=root_deadline,
                    instant=monotonic.monotonic_ms,
                )
        except FormalRunDependencyError:
            raise
        except BaseException:
            raise FormalRunDependencyError(
                "unexpected_provider_error",
                runtime_id=(
                    monotonic.runtime_id
                    if monotonic is not None and not isinstance(monotonic, ErrorResultDTO)
                    else "dependency-failed-runtime"
                ),
                local_deadline=root_deadline,
                instant=(
                    monotonic.monotonic_ms
                    if monotonic is not None and not isinstance(monotonic, ErrorResultDTO)
                    else 0
                ),
            ) from None

        effective_deadline = min(
            root_deadline,
            boundary_deadline if boundary_deadline is not None else root_deadline,
        )
        remaining_ms = effective_deadline - monotonic.monotonic_ms
        if remaining_ms <= 0:
            raise FormalRunDependencyError(
                "deadline_exceeded",
                runtime_id=monotonic.runtime_id,
                local_deadline=root_deadline,
                instant=monotonic.monotonic_ms,
            )

        operation_id = self._derived_operation(command.operation_id, f"STATE:{to_state.upper()}")
        absolute = min(
            command.execution.absolute_deadline_at.as_datetime(),
            utc.utc.as_datetime() + timedelta(milliseconds=remaining_ms),
        )
        deadline = DeadlineDTO(
            "1.0.0",
            operation_id,
            absolute.isoformat().replace("+00:00", "Z"),
            min(2_000, remaining_ms),
            utc.utc,
            100,
        )
        request = TransitionExecutionRequestDTO(
            operation_id,
            execution.execution_id,
            execution.version,
            execution.state,
            to_state,
            occurred_at,
            deadline,
            safe_reason_code,
            partial_reason_codes,
        )
        try:
            local = build_local_deadline(
                request.deadline,
                provider_timeout_ms=2_000,
                now_utc=utc.utc.as_datetime(),
                now_monotonic_ms=monotonic.monotonic_ms,
                runtime_id=monotonic.runtime_id,
            )
        except DeadlineExceededError:
            raise FormalRunDependencyError(
                "deadline_exceeded",
                runtime_id=monotonic.runtime_id,
                local_deadline=root_deadline,
                instant=monotonic.monotonic_ms,
            ) from None

        completed = Event()
        payload: list[object] = []

        def invoke() -> None:
            try:
                payload.append(self._executions.transition(request))
            except BaseException:
                payload.append("unexpected_provider_error")
            finally:
                completed.set()

        Thread(
            target=invoke,
            name=f"formal-run-transition-{to_state}",
            daemon=True,
        ).start()
        if not completed.wait(local.effective_timeout_ms / 1_000):
            raise FormalRunDependencyError(
                "deadline_exceeded",
                runtime_id=local.runtime_id,
                local_deadline=root_deadline,
                instant=monotonic.monotonic_ms,
            )
        result = payload[0]
        if isinstance(result, str):
            raise FormalRunDependencyError(
                result,
                runtime_id=local.runtime_id,
                local_deadline=root_deadline,
                instant=monotonic.monotonic_ms,
            )
        if isinstance(result, ErrorResultDTO):
            raise FormalRunDependencyError(
                result.error.code,
                runtime_id=local.runtime_id,
                local_deadline=root_deadline,
                instant=monotonic.monotonic_ms,
            )
        if not isinstance(result, ExecutionRecordDTO):
            raise FormalRunDependencyError(
                "unexpected_provider_error",
                runtime_id=local.runtime_id,
                local_deadline=root_deadline,
                instant=monotonic.monotonic_ms,
            )
        return result

    def _publish_step_event(
        self,
        command: FormalRunCommand,
        request: FormalRunStepRequest,
        outcome: StepOutcome,
        *,
        started: bool,
        duration_ms: int,
        safe_reason_code: str | None,
        root_deadline: int,
    ) -> None:
        remaining = max(0, root_deadline - self._monotonic(command.operation_id))
        if remaining <= 100:
            return
        suffix = "START" if started else "END"
        event_id = f"EVT-{command.operation_id[3:]}:{request.step.value.upper()}:{suffix}"
        status = "started" if started else {
            StepOutcome.SUCCESS: "completed",
            StepOutcome.DEGRADED: "degraded",
            StepOutcome.FAILURE: "failed",
            StepOutcome.SKIPPED: "skipped",
            StepOutcome.TIMEOUT: "failed",
        }[outcome]
        error = None
        if not started and outcome in {StepOutcome.FAILURE, StepOutcome.TIMEOUT}:
            error = EventErrorDTO(
                safe_reason_code or ("step_timeout" if outcome is StepOutcome.TIMEOUT else "step_failed"),
                "timeout" if outcome is StepOutcome.TIMEOUT else "unexpected",
                False,
                "Formal run step did not complete.",
            )
        event_operation = self._derived_operation(command.operation_id, f"EVENT:{request.step.value.upper()}:{suffix}")
        deadline = DeadlineDTO(
            "1.0.0",
            event_operation,
            command.execution.absolute_deadline_at,
            min(2_000, max(1, remaining)),
            self._clock.now_utc(ClockReadRequestDTO(self._derived_operation(command.operation_id, "CLOCK:EVENT"))).utc,
            100,
        )
        event = ExecutionEventDTO(
            event_id,
            self._clock.now_utc(ClockReadRequestDTO(self._derived_operation(command.operation_id, "CLOCK:EVENT:TIME"))).utc,
            command.execution.task_id,
            command.execution.execution_id,
            request.step.value,
            "formal_run_orchestrator",
            status,
            duration_ms,
            0,
            {"budget_version": self._policy.version},
            {"outcome": "started" if started else outcome.value},
            error,
            min(900_000, remaining),
            {"operation_id": request.operation_id, "causation_event_id": None},
        )
        self._publish_event_with_guard(command, PublishEventRequestDTO(
            event_operation, event, deadline
        ))

    def _publish_terminal_event(
        self,
        command: FormalRunCommand,
        operation_id: str,
        terminal: TerminalOutcome,
        root_deadline: int,
    ) -> None:
        remaining = max(0, root_deadline - self._monotonic(command.operation_id))
        if remaining <= 100:
            return
        event_operation = self._derived_operation(command.operation_id, "EVENT:TERMINAL")
        deadline = DeadlineDTO(
            "1.0.0",
            event_operation,
            command.execution.absolute_deadline_at,
            min(2_000, remaining),
            self._clock.now_utc(ClockReadRequestDTO(self._derived_operation(command.operation_id, "CLOCK:TERMINAL"))).utc,
            100,
        )
        event = ExecutionEventDTO(
            f"EVT-{command.operation_id[3:]}:TERMINAL",
            self._clock.now_utc(ClockReadRequestDTO(self._derived_operation(command.operation_id, "CLOCK:TERMINAL:TIME"))).utc,
            command.execution.task_id,
            command.execution.execution_id,
            FormalRunStep.TERMINAL_TRANSITION.value,
            "formal_run_orchestrator",
            "completed",
            0,
            0,
            {"budget_version": self._policy.version},
            {"outcome": terminal.value},
            None,
            min(900_000, remaining),
            {"operation_id": operation_id, "causation_event_id": None},
        )
        self._publish_event_with_guard(command, PublishEventRequestDTO(
            event_operation, event, deadline
        ))

    def _publish_event_with_guard(
        self,
        command: FormalRunCommand,
        request: PublishEventRequestDTO,
    ) -> None:
        """Bound event latency; late idempotent receipts cannot alter run outcome."""

        try:
            utc = self._clock.now_utc(ClockReadRequestDTO(
                self._derived_operation(command.operation_id, "CLOCK:EVENT:GUARD:UTC")
            ))
            monotonic = self._clock.monotonic_ms(ClockReadRequestDTO(
                self._derived_operation(command.operation_id, "CLOCK:EVENT:GUARD:MONO")
            ))
        except BaseException:
            return
        if isinstance(utc, ErrorResultDTO) or isinstance(monotonic, ErrorResultDTO):
            return
        try:
            local = build_local_deadline(
                request.deadline,
                provider_timeout_ms=2_000,
                now_utc=utc.utc.as_datetime(),
                now_monotonic_ms=monotonic.monotonic_ms,
                runtime_id=monotonic.runtime_id,
            )
        except DeadlineExceededError:
            return

        completed = Event()

        def invoke() -> None:
            try:
                self._events.publish(request)
            except BaseException:
                pass
            finally:
                completed.set()

        Thread(
            target=invoke,
            name=f"formal-run-event-{request.event.event_id}",
            daemon=True,
        ).start()
        completed.wait(local.effective_timeout_ms / 1_000)

    def _dependency_failed_result(
        self,
        command: FormalRunCommand,
        error: FormalRunDependencyError,
    ) -> FormalRunResult:
        timed_out = error.code == "deadline_exceeded"
        records = []
        for index, definition in enumerate(self._policy.stages):
            records.append(StepRecord(
                definition.step,
                self._step_operation(command.operation_id, definition.step),
                (
                    StepOutcome.TIMEOUT if timed_out else StepOutcome.FAILURE
                ) if index == 0 else StepOutcome.SKIPPED,
                error.instant,
                error.instant,
                error.code if index == 0 else "upstream_dependency_failed",
            ))
        return FormalRunResult(
            command.operation_id,
            command.execution.task_id,
            command.execution.execution_id,
            self._policy.version,
            self._policy.hard_deadline_ms,
            error.runtime_id,
            error.local_deadline,
            TerminalOutcome.TIMED_OUT if timed_out else TerminalOutcome.FAILED,
            tuple(records),
            (),
            (error.code,),
            0,
            tuple(
                job.job_id
                for job in sorted(
                    command.plan.sourcing_jobs,
                    key=lambda item: item.priority,
                )
            ),
        )

    def _expired_result(
        self,
        command: FormalRunCommand,
        runtime_id: str,
        now_monotonic: int,
    ) -> FormalRunResult:
        records = []
        for index, definition in enumerate(self._policy.stages):
            outcome = StepOutcome.TIMEOUT if index == 0 else StepOutcome.SKIPPED
            records.append(
                StepRecord(
                    definition.step,
                    self._step_operation(command.operation_id, definition.step),
                    outcome,
                    now_monotonic,
                    now_monotonic,
                    "deadline_exceeded",
                )
            )
        return FormalRunResult(
            command.operation_id,
            command.execution.task_id,
            command.execution.execution_id,
            self._policy.version,
            self._policy.hard_deadline_ms,
            runtime_id,
            now_monotonic,
            TerminalOutcome.TIMED_OUT,
            tuple(records),
            (),
            ("deadline_exceeded",),
            0,
            tuple(job.job_id for job in sorted(command.plan.sourcing_jobs, key=lambda item: item.priority)),
        )

    def _skipped_record(
        self,
        command: FormalRunCommand,
        step: FormalRunStep,
        reason: str,
        instant: int,
    ) -> StepRecord:
        return StepRecord(step, self._step_operation(command.operation_id, step), StepOutcome.SKIPPED, instant, instant, reason)

    def _timeout_record(
        self,
        command: FormalRunCommand,
        step: FormalRunStep,
        instant: int,
    ) -> StepRecord:
        return StepRecord(step, self._step_operation(command.operation_id, step), StepOutcome.TIMEOUT, instant, instant, "deadline_exceeded")

    @staticmethod
    def _step_operation(root: str, step: FormalRunStep) -> str:
        return FormalRunOrchestrator._derived_operation(root, f"STEP:{step.value.upper()}")

    @staticmethod
    def _derived_operation(root: str, suffix: str) -> str:
        candidate = f"{root}:{suffix}"
        if len(candidate) > 127:
            raise FormalRunValidationError("derived operation_id is too long")
        return candidate


__all__ = (
    "CancellationToken",
    "DEFAULT_FORMAL_RUN_BUDGET_POLICY",
    "FORMAL_RUN_BUDGET_VERSION",
    "FORMAL_RUN_HARD_DEADLINE_MS",
    "FormalRunBudgetPolicy",
    "FormalRunCommand",
    "FormalRunDependencyError",
    "FormalRunOperationConflict",
    "FormalRunOrchestrator",
    "FormalRunResult",
    "FormalRunStep",
    "FormalRunStepExecutor",
    "FormalRunStepRequest",
    "FormalRunStepResult",
    "FormalRunValidationError",
    "StageDefinition",
    "StepOutcome",
    "StepRecord",
    "TerminalOutcome",
)
