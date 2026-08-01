"""不呼叫 provider/LLM 的 deterministic Planner。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from crypto_trust_agent.domain.fingerprint import RequestFingerprint
from crypto_trust_agent.domain.primitives import UtcInstant


PLANNER_RULESET_VERSION = "planner-1.0.0"
_CATEGORIES = ("market", "news", "official", "on_chain", "social", "macro")
_BUDGETS_MS = {
    "market": 25_000,
    "news": 30_000,
    "official": 30_000,
    "on_chain": 20_000,
    "social": 15_000,
    "macro": 15_000,
}


class QuestionType(str, Enum):
    MARKET_STATUS = "market_status"
    HYPOTHESIS_VALIDATION = "hypothesis_validation"
    ASSET_COMPARISON = "asset_comparison"


class RequirementLevel(str, Enum):
    REQUIRED = "required"
    REQUIRED_IF_AVAILABLE = "required_if_available"
    OPTIONAL = "optional"


_MATRIX = {
    QuestionType.MARKET_STATUS: {
        "market": RequirementLevel.REQUIRED,
        "news": RequirementLevel.REQUIRED,
        "official": RequirementLevel.REQUIRED,
        "on_chain": RequirementLevel.OPTIONAL,
        "social": RequirementLevel.OPTIONAL,
        "macro": RequirementLevel.OPTIONAL,
    },
    QuestionType.HYPOTHESIS_VALIDATION: {
        "market": RequirementLevel.REQUIRED,
        "news": RequirementLevel.REQUIRED,
        "official": RequirementLevel.REQUIRED,
        "on_chain": RequirementLevel.REQUIRED_IF_AVAILABLE,
        "social": RequirementLevel.OPTIONAL,
        "macro": RequirementLevel.OPTIONAL,
    },
    QuestionType.ASSET_COMPARISON: {
        "market": RequirementLevel.REQUIRED,
        "news": RequirementLevel.REQUIRED,
        "official": RequirementLevel.REQUIRED,
        "on_chain": RequirementLevel.OPTIONAL,
        "social": RequirementLevel.OPTIONAL,
        "macro": RequirementLevel.OPTIONAL,
    },
}
_DIMENSIONS = {
    QuestionType.MARKET_STATUS: (
        "price_trend",
        "volatility",
        "volume",
        "market_regime",
        "source_coverage",
    ),
    QuestionType.HYPOTHESIS_VALIDATION: (
        "supporting_evidence",
        "counter_evidence",
        "market_context",
        "contradictions",
        "limitations",
    ),
    QuestionType.ASSET_COMPARISON: (
        "relative_price_trend",
        "relative_volatility",
        "liquidity",
        "market_regime",
        "risk",
    ),
}
_ANALYSIS_STEPS = {
    QuestionType.MARKET_STATUS: ("normalize", "market_features", "assess", "reason"),
    QuestionType.HYPOTHESIS_VALIDATION: (
        "normalize",
        "classify_support",
        "preserve_counter_evidence",
        "assess",
        "reason",
    ),
    QuestionType.ASSET_COMPARISON: (
        "normalize",
        "parallel_asset_features",
        "relative_analysis",
        "assess",
        "reason",
    ),
}


@dataclass(frozen=True, slots=True)
class SourcingJob:
    job_id: str
    asset: str
    category: str
    requirement: RequirementLevel
    query: str
    budget_ms: int
    priority: int
    range_start: str
    range_end: str
    fallback_policy: str


@dataclass(frozen=True, slots=True)
class DeterministicPlan:
    question_type: QuestionType
    ruleset_version: str
    clock_snapshot: str
    assets_requested_order: tuple[str, ...]
    assets_canonical: tuple[str, ...]
    answer_dimensions: tuple[str, ...]
    source_requirements: Mapping[str, RequirementLevel]
    sourcing_jobs: tuple[SourcingJob, ...]
    analysis_steps: tuple[str, ...]
    warmup_start: str
    reporting_start: str
    reporting_end: str
    canonical_json: bytes
    canonical_hash: str


def source_requirement_matrix(
    question_type: QuestionType | str,
) -> Mapping[str, RequirementLevel]:
    question_type = QuestionType(question_type)
    return MappingProxyType(dict(_MATRIX[question_type]))


def build_plan(
    *,
    question_type: QuestionType | str,
    fingerprint: RequestFingerprint,
    clock_snapshot: str,
) -> DeterministicPlan:
    question_type = QuestionType(question_type)
    snapshot = str(UtcInstant(clock_snapshot))
    matrix = source_requirement_matrix(question_type)
    report_start = UtcInstant(fingerprint.timeframe_start).as_datetime()
    report_end = UtcInstant(fingerprint.timeframe_end).as_datetime()
    warmup_start = (report_start - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:00Z")

    jobs: list[SourcingJob] = []
    for asset_index, asset in enumerate(fingerprint.assets_requested_order):
        for category_index, category in enumerate(_CATEGORIES):
            query = (
                f"official_dataset:{asset}:daily_ohlcv"
                if category == "market"
                else f"{question_type.value}:{asset}:{category}"
            )
            jobs.append(
                SourcingJob(
                    job_id=f"JOB-{asset}-{category.upper()}",
                    asset=asset,
                    category=category,
                    requirement=matrix[category],
                    query=query,
                    budget_ms=_BUDGETS_MS[category],
                    priority=asset_index * len(_CATEGORIES) + category_index,
                    range_start=warmup_start if category == "market" else fingerprint.timeframe_start,
                    range_end=fingerprint.timeframe_end,
                    fallback_policy="record_limitation_and_continue",
                )
            )

    payload = {
        "analysis_steps": list(_ANALYSIS_STEPS[question_type]),
        "answer_dimensions": list(_DIMENSIONS[question_type]),
        "assets_canonical": list(fingerprint.assets_canonical),
        "assets_requested_order": list(fingerprint.assets_requested_order),
        "clock_snapshot": snapshot,
        "question_type": question_type.value,
        "reporting_range": {
            "end": fingerprint.timeframe_end,
            "start": fingerprint.timeframe_start,
        },
        "ruleset_version": PLANNER_RULESET_VERSION,
        "source_requirements": {key: matrix[key].value for key in _CATEGORIES},
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
            for job in jobs
        ],
        "warmup_start": warmup_start,
    }
    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return DeterministicPlan(
        question_type=question_type,
        ruleset_version=PLANNER_RULESET_VERSION,
        clock_snapshot=snapshot,
        assets_requested_order=fingerprint.assets_requested_order,
        assets_canonical=fingerprint.assets_canonical,
        answer_dimensions=_DIMENSIONS[question_type],
        source_requirements=matrix,
        sourcing_jobs=tuple(jobs),
        analysis_steps=_ANALYSIS_STEPS[question_type],
        warmup_start=warmup_start,
        reporting_start=fingerprint.timeframe_start,
        reporting_end=fingerprint.timeframe_end,
        canonical_json=canonical_json,
        canonical_hash="sha256:" + hashlib.sha256(canonical_json).hexdigest(),
    )
