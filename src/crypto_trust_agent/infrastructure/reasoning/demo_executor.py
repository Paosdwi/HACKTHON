"""Demo-first Formal Run executor with live news, Binance and Claude boundaries."""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.live_market import (
    LiveMarketDataRequestDTO,
    LiveMarketDataResultDTO,
)
from crypto_trust_agent.application.dto.reasoning import (
    AnalysisRefDTO,
    EvidenceRefDTO,
    GenerateRequestDTO,
    ReasoningContextDTO,
    ReasoningResultDTO,
)
from crypto_trust_agent.application.dto.repositories import (
    EvidenceClaimLinkDTO,
    EvidenceDTO,
)
from crypto_trust_agent.application.orchestration.formal_run import (
    FormalRunStep,
    FormalRunStepRequest,
    FormalRunStepResult,
    StepOutcome,
)
from crypto_trust_agent.application.orchestration.stage_contributions import (
    AnalysisContributionDTO,
    EvidenceContributionDTO,
    PipelineContributionDTO,
    PipelineContributionKind,
    ReasoningContributionDTO,
)
from crypto_trust_agent.application.ports.live_market import LiveMarketDataProvider
from crypto_trust_agent.application.ports.reasoning import ReasoningProvider
from crypto_trust_agent.infrastructure.collectors.google_news_demo import (
    DemoNewsItem,
    GoogleNewsRssCollector,
)
from crypto_trust_agent.infrastructure.collectors.binance_us_demo import DemoMarketSnapshot


class DemoClock(Protocol):
    def current_utc(self): ...


class DelegateExecutor(Protocol):
    def execute(self, request: FormalRunStepRequest) -> FormalRunStepResult: ...


class DemoNewsCollector(Protocol):
    def collect(self, asset: str, category: str, *, timeout_seconds: float = 8.0) -> tuple[DemoNewsItem, ...]: ...


class DemoMarketCollector(Protocol):
    def collect(self, asset: str, start, end, *, timeout_ms: int = 10_000) -> DemoMarketSnapshot: ...


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class AwsDemoFormalRunStepExecutor:
    """Replace only external-facing demo stages; keep Core orchestration intact."""

    non_production = False

    def __init__(
        self,
        delegate: DelegateExecutor,
        *,
        clock: DemoClock,
        reasoning_provider: ReasoningProvider,
        market_provider: LiveMarketDataProvider,
        news_collector: DemoNewsCollector | None = None,
        market_fallback: DemoMarketCollector | None = None,
    ) -> None:
        self._delegate = delegate
        self._clock = clock
        self._reasoning = reasoning_provider
        self._market = market_provider
        self._news = news_collector or GoogleNewsRssCollector()
        self._market_fallback = market_fallback

    def execute(self, request: FormalRunStepRequest) -> FormalRunStepResult:
        if request.step is FormalRunStep.EXTRACTION:
            result = self._extract_news(request)
        elif request.step is FormalRunStep.MARKET_ANALYSIS:
            result = self._market_analysis(request)
        elif request.step is FormalRunStep.REASONING_BOUNDARY:
            result = self._reason(request)
        else:
            result = self._delegate.execute(request)
        _LOGGER.warning(
            "aws_demo_step step=%s outcome=%s reason_code=%s",
            request.step.value,
            result.outcome.value,
            result.safe_reason_code or "none",
        )
        return result

    def _delegate_events(self, request: FormalRunStepRequest) -> tuple[PipelineContributionDTO, ...]:
        result = self._delegate.execute(request)
        return tuple(
            item
            for item in result.contributions
            if item.kind is PipelineContributionKind.EVENT
        )

    @staticmethod
    def _job(request: FormalRunStepRequest) -> tuple[str, str, str]:
        if len(request.planned_job_ids) != 1:
            raise ValueError("demo extraction requires one planned job")
        job_id = request.planned_job_ids[0]
        parts = job_id.split("-", 2)
        if len(parts) != 3:
            raise ValueError("invalid planned job")
        return job_id, parts[1], parts[2].lower()

    def _extract_news(self, request: FormalRunStepRequest) -> FormalRunStepResult:
        job_id, asset, category = self._job(request)
        events = self._delegate_events(request)
        if category not in {"news", "official"}:
            return FormalRunStepResult(
                StepOutcome.SUCCESS,
                completed_job_ids=(job_id,),
                contributions=events,
            )
        try:
            items = self._news.collect(asset, category)
        except Exception:
            items = ()
        if not items:
            return FormalRunStepResult(
                StepOutcome.DEGRADED,
                "source_unavailable",
                required_source_failures=(category,),
                evidence_count=0,
                completed_job_ids=(job_id,),
                contributions=events,
            )

        now = str(self._clock.current_utc())
        identity = f"{request.task_id[5:]}:{job_id[4:]}"
        evidence: list[EvidenceDTO] = []
        links: list[EvidenceClaimLinkDTO] = []
        for index, item in enumerate(items, start=1):
            suffix = f"{identity}:{index:03d}"
            content = f"{item.title}. {item.summary}"[:2_000]
            evidence_id = f"EVID-{suffix}"
            evidence.append(EvidenceDTO(
                evidence_id=evidence_id,
                task_id=request.task_id,
                execution_id=request.execution_id,
                raw_record_id=f"RAW-{identity}",
                source_name=item.source_name or "Google News",
                source_type=category,
                source_url=item.url,
                published_at=item.published_at,
                fetched_at=now,
                content_reference={
                    "kind": "quote",
                    "value": content,
                    "unit": "unicode_scalar",
                    "offset": {"start": 0, "end": len(content)},
                },
                raw_locator=item.url,
                raw_content_hash=_digest(content),
                clean_content_hash=_digest(" ".join(content.split())),
                query_provenance={"asset": asset, "category": category, "provider": "google_news_rss"},
                validation_status="active",
                created_at=now,
            ))
            links.append(EvidenceClaimLinkDTO(
                link_id=f"LINK-{suffix}",
                task_id=request.task_id,
                evidence_id=evidence_id,
                claim_id=f"CLAIM-{suffix}",
                stance="context",
                created_at=now,
            ))
        contribution = PipelineContributionDTO(
            PipelineContributionKind.EVIDENCE,
            EvidenceContributionDTO(
                request.task_id,
                request.execution_id,
                tuple(evidence),
                tuple(links),
            ),
        )
        return FormalRunStepResult(
            StepOutcome.SUCCESS,
            evidence_count=len(evidence),
            completed_job_ids=(job_id,),
            contributions=(contribution, *events),
        )

    def _market_analysis(self, request: FormalRunStepRequest) -> FormalRunStepResult:
        snapshot = request.pipeline_snapshot
        if snapshot is None:
            return FormalRunStepResult(StepOutcome.FAILURE, "pipeline_context_invalid")
        now = self._clock.current_utc()
        end = now.as_datetime().date() - timedelta(days=1)
        start = end - timedelta(days=13)
        analyses = []
        for index, asset in enumerate(snapshot.assets, start=1):
            operation_id = f"{request.operation_id}:{asset}"
            deadline = DeadlineDTO(
                request.deadline.schema_version,
                operation_id,
                request.deadline.deadline_at_utc,
                min(30_000, request.deadline.budget_ms),
                request.deadline.sent_at_utc,
                request.deadline.safety_margin_ms,
            )
            result = self._market.fetch_daily_ohlcv(LiveMarketDataRequestDTO(
                operation_id,
                asset,
                f"{asset}USDT",
                start,
                end,
                str(now),
                deadline,
            ))
            if isinstance(result, LiveMarketDataResultDTO) and result.bars:
                first, last = result.bars[0], result.bars[-1]
                first_close = first.close.as_decimal()
                last_close = last.close.as_decimal()
                first_day = first.day
                last_day = last.day
                latest_volume = last.volume
                source_url = last.source_url
                provider_label = "Binance"
            elif self._market_fallback is not None:
                try:
                    fallback = self._market_fallback.collect(asset, start, end)
                except Exception:
                    continue
                first_close = fallback.first_close
                last_close = fallback.last_close
                first_day = fallback.first_day
                last_day = fallback.last_day
                latest_volume = str(fallback.latest_volume)
                source_url = fallback.source_url
                provider_label = "Binance.US"
            else:
                continue
            change = Decimal("0") if first_close == 0 else ((last_close - first_close) / first_close) * Decimal("100")
            summary = (
                f"{asset}/USDT {provider_label} daily OHLCV {first_day.isoformat()} to {last_day.isoformat()}: "
                f"latest close {last_close} USDT; period change {change.quantize(Decimal('0.01'))}% ; "
                f"latest volume {latest_volume}."
            )
            analyses.append(AnalysisRefDTO(
                f"ANALYSIS-{request.task_id[5:]}:{index:03d}",
                "1.0.0",
                summary,
                (source_url,),
            ))
        events = self._delegate_events(request)
        if not analyses:
            return FormalRunStepResult(
                StepOutcome.DEGRADED,
                "live_market_unavailable",
                contributions=events,
            )
        contribution = PipelineContributionDTO(
            PipelineContributionKind.ANALYSIS,
            AnalysisContributionDTO(request.task_id, request.execution_id, tuple(analyses)),
        )
        return FormalRunStepResult(
            StepOutcome.SUCCESS,
            contributions=(contribution, *events),
        )

    def _reason(self, request: FormalRunStepRequest) -> FormalRunStepResult:
        snapshot = request.pipeline_snapshot
        if snapshot is None:
            return FormalRunStepResult(StepOutcome.FAILURE, "pipeline_context_invalid")
        latest = {}
        for item in snapshot.assessments:
            previous = latest.get(item.evidence_id)
            if previous is None or item.assessment_sequence > previous.assessment_sequence:
                latest[item.evidence_id] = item
        stances = {item.evidence_id: item.stance for item in snapshot.claim_links}
        refs = tuple(
            EvidenceRefDTO(
                item.evidence_id,
                latest[item.evidence_id].assessment_id,
                str(item.content_reference.get("value", ""))[:2_000],
                stances.get(item.evidence_id, "context"),
                latest[item.evidence_id].overall_confidence,
            )
            for item in snapshot.evidence
            if item.evidence_id in latest and item.validation_status == "active"
        )
        limitations = tuple(dict.fromkeys((
            *snapshot.limitations,
            "Public demo uses bounded public news RSS and Binance/Binance.US daily closed OHLCV.",
        )))
        context = ReasoningContextDTO(
            snapshot.question,
            refs,
            snapshot.analysis_refs,
            snapshot.contradictions,
            limitations,
            (),
        )
        result = self._reasoning.generate(GenerateRequestDTO(
            request.operation_id,
            request.task_id,
            request.execution_id,
            "primary",
            context,
            context.context_hash(),
            "1.0.0",
            "reasoning-guardrail-1.0.0",
            request.deadline,
        ))
        events = self._delegate_events(request)
        if not isinstance(result, ReasoningResultDTO) or result.outcome != "valid":
            if isinstance(result, ErrorResultDTO):
                safe_code = result.error.code
            elif isinstance(result, ReasoningResultDTO):
                safe_code = ",".join(item.code for item in result.validation_diagnostics) or result.outcome
            else:
                safe_code = "invalid_result_type"
            _LOGGER.warning("aws_demo_reasoning_rejected safe_code=%s", safe_code)
            return FormalRunStepResult(
                StepOutcome.FAILURE,
                "reasoning_provider_unavailable",
                contributions=events,
            )
        contribution = PipelineContributionDTO(
            PipelineContributionKind.REASONING,
            ReasoningContributionDTO(request.task_id, request.execution_id, result),
        )
        return FormalRunStepResult(
            StepOutcome.SUCCESS,
            contributions=(contribution, *events),
        )


__all__ = ("AwsDemoFormalRunStepExecutor",)
_LOGGER = logging.getLogger(__name__)
