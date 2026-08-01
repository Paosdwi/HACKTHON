"""Deterministic no-network Formal Run step executor."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from threading import RLock

from crypto_trust_agent.application.dto.common import DeadlineExceededError, build_local_deadline
from crypto_trust_agent.application.dto.reasoning import (
    AnalysisRefDTO,
    ConclusionDTO,
    ConfidenceComponentsDTO,
    ContradictionDTO,
    FactDTO,
    InferenceDTO,
    ProviderDTO,
    ReasoningResultDTO,
)
from crypto_trust_agent.application.dto.repositories import (
    EvidenceAssessmentDTO,
    EvidenceClaimLinkDTO,
    EvidenceDTO,
    ExecutionEventDTO,
)
from crypto_trust_agent.application.orchestration.formal_run import (
    CancellationToken,
    FormalRunStep,
    FormalRunStepRequest,
    FormalRunStepResult,
    StepOutcome,
)
from crypto_trust_agent.application.orchestration.stage_contributions import (
    AnalysisContributionDTO,
    AssessmentContributionDTO,
    CollectionContributionDTO,
    EventContributionDTO,
    EvidenceContributionDTO,
    PipelineContributionDTO,
    PipelineContributionKind,
    ReasoningContributionDTO,
    StrategyContributionDTO,
)
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock


def _deterministic_hash(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _token(seed: str, length: int = 12) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:length]


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
    """Return bounded typed stage outputs without retaining pipeline state."""

    non_production = True

    def __init__(self, clock: FakeClock, *, populate_pipeline: bool = False) -> None:
        self._clock = clock
        self._lock = RLock()
        self._scenarios: dict[FormalRunStep, _Scenario] = {}
        self._requests: list[FormalRunStepRequest] = []
        self._cancel_after: dict[FormalRunStep, CancellationToken] = {}
        self._populate_pipeline = populate_pipeline
        self._report_limitations: tuple[str, ...] = ()

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

    def configure_report_limitations(
        self, limitations: str | tuple[str, ...]
    ) -> None:
        configured = (limitations,) if isinstance(limitations, str) else tuple(limitations)
        if not configured or any(not isinstance(item, str) or not item for item in configured):
            raise ValueError("limitations must contain nonempty strings")
        with self._lock:
            self._report_limitations = configured

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
            effective_outcome = StepOutcome.TIMEOUT if timed_out else scenario.outcome
            contributions: tuple[PipelineContributionDTO, ...] = ()
            if (
                self._populate_pipeline
                and effective_outcome in {StepOutcome.SUCCESS, StepOutcome.DEGRADED}
            ):
                contributions = self._stage_contributions(
                    request,
                    effective_outcome,
                    scenario.contradiction_count,
                )
            return FormalRunStepResult(
                effective_outcome,
                "step_timeout" if timed_out else scenario.safe_reason_code,
                scenario.required_source_failures,
                scenario.optional_source_failures,
                scenario.evidence_count,
                scenario.quarantined_count,
                scenario.contradiction_count,
                completed_job_ids,
                contributions,
            )

    def _stage_contributions(
        self,
        request: FormalRunStepRequest,
        outcome: StepOutcome,
        contradiction_count: int,
    ) -> tuple[PipelineContributionDTO, ...]:
        snapshot = request.pipeline_snapshot
        if snapshot is None:
            return ()
        task_id = request.task_id
        execution_id = request.execution_id
        now = self._clock.current_utc()
        question = snapshot.question
        assets = snapshot.assets
        contributions: list[PipelineContributionDTO] = []

        if request.step is FormalRunStep.COLLECTION:
            raw_ids = tuple(
                f"RAW-{task_id[5:]}:{job_id[4:]}" for job_id in request.planned_job_ids
            )
            contributions.append(
                PipelineContributionDTO(
                    PipelineContributionKind.COLLECTION,
                    CollectionContributionDTO(task_id, execution_id, raw_ids),
                )
            )

        elif request.step is FormalRunStep.EXTRACTION:
            evidence_items: list[EvidenceDTO] = []
            links: list[EvidenceClaimLinkDTO] = []
            for job_id in request.planned_job_ids:
                job_parts = job_id.split("-", 2)
                asset = job_parts[1] if len(job_parts) > 1 else assets[0]
                category = job_parts[2].lower() if len(job_parts) > 2 else "news"
                identity = f"{task_id[5:]}:{job_id[4:]}"
                evidence_id = f"EVID-{identity}"
                raw_record_id = f"RAW-{identity}"
                seed = f"{question}:{','.join(assets)}:{task_id}:{execution_id}:{job_id}"
                clean_content = f"可重現的 {asset} 本機 fake 內容：{question[:100]}"
                evidence_items.append(
                    EvidenceDTO(
                        evidence_id=evidence_id,
                        task_id=task_id,
                        execution_id=execution_id,
                        raw_record_id=raw_record_id,
                        source_name=f"fake_{category}_{asset.lower()}",
                        source_type="dataset" if category == "market" else "news",
                        source_url=f"https://fake.example.com/{asset.lower()}/{category}",
                        published_at=str(now),
                        fetched_at=str(now),
                        content_reference={
                            "kind": "quote",
                            "value": clean_content[:200],
                            "unit": "unicode_scalar",
                            "offset": {
                                "start": 0,
                                "end": min(200, len(clean_content)),
                            },
                        },
                        raw_locator=f"urn:fake:{asset.lower()}:{category}:{_token(seed)}",
                        raw_content_hash=_deterministic_hash(f"{seed}:raw"),
                        clean_content_hash=_deterministic_hash(f"{seed}:clean"),
                        query_provenance={
                            "query": f"{request.step.value}:{asset}:{category}",
                            "source_category": category,
                        },
                        validation_status="active",
                        created_at=str(now),
                    )
                )
                links.append(
                    EvidenceClaimLinkDTO(
                        link_id=f"LINK-{identity}",
                        task_id=task_id,
                        evidence_id=evidence_id,
                        claim_id=f"CLAIM-{identity}",
                        stance="supports",
                        created_at=str(now),
                    )
                )
            contributions.append(
                PipelineContributionDTO(
                    PipelineContributionKind.EVIDENCE,
                    EvidenceContributionDTO(
                        task_id,
                        execution_id,
                        tuple(evidence_items),
                        tuple(links),
                    ),
                )
            )

        elif request.step is FormalRunStep.ASSESSMENT:
            assessments = tuple(
                EvidenceAssessmentDTO(
                    assessment_id=f"ASSESS-{item.evidence_id[5:]}",
                    task_id=task_id,
                    evidence_id=item.evidence_id,
                    assessment_sequence=1,
                    assessment_version="1.0.0",
                    ruleset_version="assessment-1.0.0",
                    source_trust="0.8",
                    relevance="0.9",
                    freshness="0.85",
                    independence="0.7",
                    independence_group=f"group_{index:03d}",
                    consistency="0.8",
                    overall_confidence="0.8",
                    contradiction_severity="low",
                    computed_at=str(now),
                    limitations=(),
                )
                for index, item in enumerate(snapshot.evidence, start=1)
            )
            contributions.append(
                PipelineContributionDTO(
                    PipelineContributionKind.ASSESSMENT,
                    AssessmentContributionDTO(task_id, execution_id, assessments),
                )
            )

        elif request.step is FormalRunStep.MARKET_ANALYSIS:
            analyses = tuple(
                AnalysisRefDTO(
                    analysis_id=f"ANALYSIS-{task_id[5:]}:{index:03d}",
                    analysis_version="1.0.0",
                    summary=f"針對 {asset} 的可重現市場情勢分析。",
                    source_refs=(f"DATASET:{asset}_daily_ohlcv.csv:lines:1-100",),
                )
                for index, asset in enumerate(assets, start=1)
            )
            contributions.append(
                PipelineContributionDTO(
                    PipelineContributionKind.ANALYSIS,
                    AnalysisContributionDTO(task_id, execution_id, analyses),
                )
            )

        elif request.step is FormalRunStep.STRATEGY_EVALUATION:
            contradictions: tuple[ContradictionDTO, ...] = ()
            evidence_ids = tuple(item.evidence_id for item in snapshot.evidence)
            if contradiction_count and len(evidence_ids) >= 2:
                contradictions = tuple(
                    ContradictionDTO(
                        f"CONTRA-{task_id[5:]}:{index:03d}",
                        (evidence_ids[0], evidence_ids[index % len(evidence_ids)]),
                        "low",
                        "可重現的 fake 策略矛盾訊號。",
                    )
                    for index in range(1, min(contradiction_count, len(evidence_ids) - 1) + 1)
                )
            contributions.append(
                PipelineContributionDTO(
                    PipelineContributionKind.STRATEGY,
                    StrategyContributionDTO(
                        task_id,
                        execution_id,
                        contradictions,
                        (),
                    ),
                )
            )

        elif request.step is FormalRunStep.REASONING_BOUNDARY:
            evidence_ids = tuple(item.evidence_id for item in snapshot.evidence)
            analysis_ids = tuple(item.analysis_id for item in snapshot.analysis_refs)
            evidence_refs = evidence_ids[:1]
            analysis_refs = analysis_ids[:1]
            fact = FactDTO(
                f"FACT-{task_id[5:]}:001",
                f"可用且已驗證的輸入顯示 {', '.join(assets)} 訊號分歧。",
                evidence_refs,
                analysis_refs,
            )
            inference = InferenceDTO(
                f"INFER-{task_id[5:]}:001",
                "可用輸入尚未建立明確方向優勢。",
                (fact.fact_id,),
                "0.7",
            )
            conclusion = ConclusionDTO(
                f"CONCL-{task_id[5:]}:001",
                f"可用證據支持對 {assets[0]} 採取中性評估，方向信心有限。",
                (fact.fact_id,),
                (inference.inference_id,),
                "0.7",
            )
            reasoning_result = ReasoningResultDTO(
                outcome="valid",
                provider=ProviderDTO(
                    "fake_reasoning",
                    "fake-1.0.0",
                    "primary",
                    f"INV-{task_id[5:]}",
                ),
                facts=(fact,),
                inferences=(inference,),
                conclusions=(conclusion,),
                limitations=(
                    "分析僅使用可重現的本機 fake 資料。",
                    *self._report_limitations,
                ),
                watchpoints=(f"持續觀察 {assets[0]} 的市場情勢變化。",),
                confidence_components=ConfidenceComponentsDTO(
                    "0.8", "0.75", "0.7", "0.75"
                ),
                validation_diagnostics=(),
                started_at=str(now),
                finished_at=str(now),
            )
            contributions.append(
                PipelineContributionDTO(
                    PipelineContributionKind.REASONING,
                    ReasoningContributionDTO(task_id, execution_id, reasoning_result),
                )
            )

        event = ExecutionEventDTO(
            event_id=f"EVT-{request.step.value.upper()}-{_token(request.operation_id)}",
            timestamp=str(now),
            task_id=task_id,
            execution_id=execution_id,
            step=request.step.value,
            tool=f"fake_{request.step.value}",
            status="completed" if outcome is StepOutcome.SUCCESS else "degraded",
            duration_ms=0,
            retry_count=0,
            sanitized_parameters={"step": request.step.value},
            result_summary={"outcome": outcome.value},
            error=None,
            deadline_remaining_ms=request.deadline.budget_ms,
            correlation={
                "operation_id": request.operation_id,
                "causation_event_id": None,
            },
        )
        contributions.append(
            PipelineContributionDTO(
                PipelineContributionKind.EVENT,
                EventContributionDTO(task_id, execution_id, (event,)),
            )
        )
        return tuple(contributions)


__all__ = ("FakeFormalRunStepExecutor",)
