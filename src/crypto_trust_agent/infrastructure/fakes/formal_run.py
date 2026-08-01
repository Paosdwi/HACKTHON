"""Deterministic scenario executor for the Core Formal Run DAG."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from crypto_trust_agent.application.dto.common import DeadlineExceededError, build_local_deadline
from crypto_trust_agent.application.orchestration.formal_run import (
    CancellationToken,
    FormalRunStep,
    FormalRunStepRequest,
    FormalRunStepResult,
    StepOutcome,
)
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock


@dataclass(frozen=True, slots=True)
class _Scenario:
    outcome: StepOutcome
    duration_ms: int
    safe_reason_code: str | None
    required_source_failures: tuple[str, ...]
    optional_source_failures: tuple[str, ...]
    evidence_count: int | None
    quarantined_count: int
    contradiction_count: int
    completed_job_ids: tuple[str, ...] | None


class FakeFormalRunStepExecutor:
    """No-network fake with explicit duration, result, and cancellation control."""

    non_production = True

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self._lock = RLock()
        self._scenarios: dict[FormalRunStep, _Scenario] = {}
        self._requests: list[FormalRunStepRequest] = []
        self._cancel_after: dict[FormalRunStep, CancellationToken] = {}

    @property
    def calls(self) -> tuple[FormalRunStep, ...]:
        with self._lock:
            return tuple(request.step for request in self._requests)

    @property
    def requests(self) -> tuple[FormalRunStepRequest, ...]:
        with self._lock:
            return tuple(self._requests)

    def call_count(self, step: FormalRunStep) -> int:
        return self.calls.count(step)

    def configure(
        self,
        step: FormalRunStep,
        *,
        outcome: StepOutcome,
        duration_ms: int = 0,
        safe_reason_code: str | None = None,
        required_source_failures: tuple[str, ...] = (),
        optional_source_failures: tuple[str, ...] = (),
        evidence_count: int | None = None,
        quarantined_count: int = 0,
        contradiction_count: int = 0,
        completed_job_ids: tuple[str, ...] | None = None,
    ) -> None:
        if type(duration_ms) is not int or duration_ms < 0:
            raise ValueError("duration_ms must be nonnegative")
        scenario = _Scenario(
            StepOutcome(outcome),
            duration_ms,
            safe_reason_code,
            tuple(required_source_failures),
            tuple(optional_source_failures),
            evidence_count,
            quarantined_count,
            contradiction_count,
            None if completed_job_ids is None else tuple(completed_job_ids),
        )
        with self._lock:
            self._scenarios[FormalRunStep(step)] = scenario

    def cancel_after(self, step: FormalRunStep, token: CancellationToken) -> None:
        with self._lock:
            self._cancel_after[FormalRunStep(step)] = token

    def execute(self, request: FormalRunStepRequest) -> FormalRunStepResult:
        with self._lock:
            self._requests.append(request)
            scenario = self._scenarios.get(
                request.step,
                _Scenario(StepOutcome.SUCCESS, 0, None, (), (), None, 0, 0, None),
            )
            try:
                local_deadline = build_local_deadline(
                    request.deadline,
                    provider_timeout_ms=request.deadline.budget_ms,
                    now_utc=self._clock.current_utc().as_datetime(),
                    now_monotonic_ms=self._clock.current_monotonic_ms(),
                    runtime_id=self._clock.runtime_id,
                )
            except DeadlineExceededError:
                return FormalRunStepResult(StepOutcome.TIMEOUT, "deadline_exceeded")
            timed_out = scenario.duration_ms >= local_deadline.effective_timeout_ms
            elapsed_ms = min(scenario.duration_ms, local_deadline.effective_timeout_ms)
            if elapsed_ms:
                self._clock.advance(
                    wall_seconds=elapsed_ms / 1_000,
                    monotonic_ms=elapsed_ms,
                )
            token = self._cancel_after.get(request.step)
            if token is not None:
                token.cancel()
            completed_job_ids = (
                request.planned_job_ids
                if scenario.completed_job_ids is None
                else scenario.completed_job_ids
            )
            return FormalRunStepResult(
                StepOutcome.TIMEOUT if timed_out else scenario.outcome,
                "step_timeout" if timed_out else scenario.safe_reason_code,
                scenario.required_source_failures,
                scenario.optional_source_failures,
                scenario.evidence_count,
                scenario.quarantined_count,
                scenario.contradiction_count,
                completed_job_ids,
            )


__all__ = ("FakeFormalRunStepExecutor",)
