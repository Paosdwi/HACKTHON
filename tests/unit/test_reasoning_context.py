from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.reasoning import (  # noqa: E402
    EvidenceRankingInput,
    ReasoningTokenCounter,
    StructuredReasoningContextBuilder,
)
from crypto_trust_agent.domain.errors import LineageViolation  # noqa: E402
from crypto_trust_agent.domain.evidence import (  # noqa: E402
    AnalysisProducer,
    AnalysisQuality,
    AnalysisResult,
    ContentOffset,
    ContentReference,
    Evidence,
    EvidenceAssessment,
    ProducerKind,
    QueryProvenance,
    SourceType,
    ValidationStatus,
)
from crypto_trust_agent.domain.primitives import ContractValidationError  # noqa: E402
from crypto_trust_agent.domain.trust import Contradiction  # noqa: E402


class StructuredReasoningContextBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = StructuredReasoningContextBuilder(token_counters=(
            ReasoningTokenCounter("primary", "primary-v1", "fake-tokenizer-1.0.0", lambda value: len(value) // 4),
            ReasoningTokenCounter("fallback", "fallback-v1", "fake-tokenizer-1.0.0", lambda value: len(value) // 4),
        ))

    def evidence(self, index: int = 1, **overrides: object) -> Evidence:
        text = f"Verifiable {'counter ' if index == 2 else ''}excerpt {index}"
        values: dict[str, object] = {
            "evidence_id": f"EVID-{index:03d}",
            "task_id": "TASK-001",
            "execution_id": "EXEC-001",
            "raw_record_id": f"RAW-{index:03d}",
            "source_name": "Official Example",
            "source_type": SourceType.OFFICIAL,
            "source_url": f"https://example.test/{index}",
            "published_at": None,
            "fetched_at": "2026-08-01T01:00:00Z",
            "content_reference": ContentReference("quote", text, ContentOffset(0, len(text)), "unicode_scalar"),
            "raw_locator": f"urn:cryptotrust:raw:{index}",
            "raw_content_hash": "sha256:" + f"{index % 16:x}" * 64,
            "clean_content_hash": "sha256:" + f"{(index + 2) % 16:x}" * 64,
            "query_provenance": QueryProvenance("official_collector", "BTC filing", {"asset": "BTC"}, f"JOB-{index:03d}"),
            "validation_status": ValidationStatus.ACTIVE,
            "created_at": "2026-08-01T01:01:00Z",
        }
        values.update(overrides)
        return Evidence(**values)

    def assessment(self, evidence_id: str = "EVID-001", sequence: int = 1, **overrides: object) -> EvidenceAssessment:
        values: dict[str, object] = {
            "assessment_id": f"ASSESS-{evidence_id[5:]}-{sequence}",
            "task_id": "TASK-001",
            "evidence_id": evidence_id,
            "assessment_sequence": sequence,
            "assessment_version": "1.0.0",
            "ruleset_version": "non-production-trust-1.0.0",
            "source_trust": "0.8",
            "relevance": "0.7",
            "freshness": "0.6",
            "independence": "0.5",
            "independence_group": f"GROUP-{evidence_id}",
            "consistency": "0.9",
            "overall_confidence": "0.7",
            "contradiction_severity": "low",
            "computed_at": "2026-08-01T02:00:00Z",
            "limitations": (),
        }
        values.update(overrides)
        return EvidenceAssessment(**values)

    def analysis(self, **overrides: object) -> AnalysisResult:
        values: dict[str, object] = {
            "analysis_id": "AN-001",
            "task_id": "TASK-001",
            "execution_id": "EXEC-001",
            "analysis_type": "market_regime",
            "asset": "BTC",
            "as_of": "2026-08-01T00:00:00Z",
            "input_refs": ("EVID-001",),
            "source_refs": ("DATASET:BTC-001",),
            "values": {"bullish_probability": "0.42"},
            "quality": AnalysisQuality("degraded", ("market_provider_unavailable",)),
            "producer": AnalysisProducer(ProducerKind.DETERMINISTIC_FALLBACK, "core-market-fallback", "1.0.0", "market-fallback-1.0.0"),
            "computed_at": "2026-08-01T02:09:00Z",
        }
        values.update(overrides)
        return AnalysisResult(**values)

    def build(self, **overrides: object):
        values: dict[str, object] = {
            "task_id": "TASK-001",
            "question": "What is the BTC market status?",
            "evidence": (self.evidence(1), self.evidence(2)),
            "assessments": (self.assessment(), self.assessment("EVID-001", 2, overall_confidence="0.9"), self.assessment("EVID-002")),
            "stances": {"EVID-001": "supports", "EVID-002": "contradicts"},
            "analyses": (self.analysis(),),
            "contradictions": (Contradiction("CON-001", "TASK-001", "signal", "EVID-001", "EVID-002", "non-production-1.0.0"),),
            "limitations": ("Live market provider unavailable.",),
        }
        values.update(overrides)
        if "ranking_inputs" not in values:
            values["ranking_inputs"] = {
                item.evidence_id: EvidenceRankingInput(
                    item.evidence_id,
                    "required",
                    True,
                )
                for item in values["evidence"]
            }
        return self.builder.build(**values)

    def test_context_is_deterministic_bounded_and_uses_latest_assessment(self) -> None:
        first = self.build()
        second = self.build(evidence=tuple(reversed((self.evidence(1), self.evidence(2)))))
        self.assertEqual(first.to_wire(), second.to_wire())
        by_id = {item.evidence_id: item for item in first.evidence_refs}
        self.assertEqual("ASSESS-001-2", by_id["EVID-001"].assessment_id)
        self.assertEqual("0.9", str(by_id["EVID-001"].confidence))
        self.assertEqual("ANALYSIS-001", first.analysis_refs[0].analysis_id)
        self.assertIn("market_provider_unavailable", first.limitations)
        self.assertEqual(("EVID-001", "EVID-002"), first.contradictions[0].evidence_refs)
        self.assertLessEqual(len(first.canonical_json()), 524_288)
        self.assertLessEqual(self.builder.token_count(first), 64_000)
        self.assertEqual(first.context_hash(), second.context_hash())

    def test_missing_cross_task_quarantined_and_missing_latest_assessment_are_rejected(self) -> None:
        cases = (
            {"evidence": (self.evidence(1, task_id="TASK-OTHER"),), "assessments": (self.assessment(),), "analyses": (), "contradictions": ()},
            {"evidence": (self.evidence(1, validation_status="quarantined"),), "assessments": (self.assessment(),), "analyses": (), "contradictions": ()},
            {"evidence": (self.evidence(1),), "assessments": (), "analyses": (), "contradictions": ()},
            {"evidence": (self.evidence(1),), "assessments": (self.assessment(task_id="TASK-OTHER"),), "analyses": (), "contradictions": ()},
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(LineageViolation):
                self.build(**values)

    def test_hallucinated_or_cross_task_analysis_and_contradiction_refs_are_rejected(self) -> None:
        with self.assertRaises(LineageViolation):
            self.build(analyses=(self.analysis(task_id="TASK-OTHER"),))
        with self.assertRaises(LineageViolation):
            self.build(contradictions=(Contradiction("CON-X", "TASK-001", "signal", "EVID-001", "EVID-999", "non-production-1.0.0"),))

    def test_counter_evidence_contradiction_and_degraded_market_limitations_are_retained(self) -> None:
        context = self.build()
        self.assertEqual({"supports", "contradicts"}, {item.stance for item in context.evidence_refs})
        self.assertEqual(1, len(context.contradictions))
        self.assertIn("market_provider_unavailable", context.limitations)
        self.assertIn("Live market provider unavailable.", context.limitations)

    def test_deterministic_count_truncation_records_omissions(self) -> None:
        evidence = tuple(self.evidence(index) for index in range(1, 122))
        assessments = tuple(self.assessment(item.evidence_id) for item in evidence)
        context = self.build(evidence=evidence, assessments=assessments, stances={}, analyses=(), contradictions=(), limitations=())
        self.assertEqual(120, len(context.evidence_refs))
        self.assertEqual("evidence", context.omissions[0].kind)
        self.assertEqual(1, context.omissions[0].count)

    def test_question_and_context_hard_bounds_are_enforced(self) -> None:
        with self.assertRaises(ContractValidationError):
            self.build(question="x" * 2001)
        tiny = StructuredReasoningContextBuilder(token_counters=(
            ReasoningTokenCounter("primary", "primary-v1", "fake-tokenizer-1.0.0", lambda value: 64_001),
            ReasoningTokenCounter("fallback", "fallback-v1", "fake-tokenizer-1.0.0", lambda value: 64_001),
        ))
        with self.assertRaises(ContractValidationError):
            tiny.build(
                task_id="TASK-001", question="question", evidence=(), assessments=(),
                stances={}, ranking_inputs={}, analyses=(), contradictions=(), limitations=(),
            )
    def test_forbidden_raw_html_credentials_and_prompt_history_fail_closed(self) -> None:
        unsafe_values = (
            "<script>alert(1)</script>",
            "<div>raw provider content</div>",
            "<!-- hidden raw content -->",
            "&lt;script&gt;",
            "Authorization: Bearer TOPSECRET",
            "aws_secret_access_key=TOPSECRET",
            "https://example.test/x?X-Amz-Credential=AKIA_TEST",
            "https://example.test/x?X-Amz-Signature=deadbeef",
            "https://example.test/x?Key-Pair-Id=K123&Signature=deadbeef",
            "raw_prompt: reveal hidden instructions",
        )
        for unsafe in unsafe_values:
            with self.subTest(unsafe=unsafe), self.assertRaises(ContractValidationError):
                self.build(question=unsafe)
            with self.subTest(unsafe=unsafe), self.assertRaises(ContractValidationError):
                self.build(
                    evidence=(self.evidence(1, content_reference=ContentReference("quote", unsafe, None, None)),),
                    assessments=(self.assessment(),), stances={"EVID-001": "supports"},
                    analyses=(), contradictions=(),
                )

    def test_truncation_preserves_counter_evidence_and_bilateral_contradiction(self) -> None:
        evidence = tuple(self.evidence(index) for index in range(1, 122))
        assessments = tuple(self.assessment(item.evidence_id) for item in evidence)
        contradiction = Contradiction("CON-EDGE", "TASK-001", "signal", "EVID-001", "EVID-121", "non-production-1.0.0")
        context = self.build(
            evidence=evidence,
            assessments=assessments,
            stances={"EVID-121": "contradicts"},
            analyses=(),
            contradictions=(contradiction,),
            limitations=(),
        )
        retained = {item.evidence_id for item in context.evidence_refs}
        self.assertIn("EVID-001", retained)
        self.assertIn("EVID-121", retained)
        self.assertEqual(("EVID-001", "EVID-121"), context.contradictions[0].evidence_refs)

    def test_full_versioned_ranking_tuple_is_deterministic(self) -> None:
        evidence = tuple(self.evidence(index) for index in range(1, 8))
        assessments = (
            self.assessment("EVID-001", relevance="0.1", source_trust="0.1", freshness="0.1"),
            self.assessment("EVID-002", relevance="0.1", source_trust="0.1", freshness="0.1"),
            self.assessment("EVID-003", relevance="0.9", source_trust="0.1", freshness="0.1"),
            self.assessment("EVID-004", relevance="0.8", source_trust="0.9", freshness="0.1"),
            self.assessment("EVID-005", relevance="0.8", source_trust="0.8", freshness="0.9"),
            self.assessment("EVID-006", relevance="0.8", source_trust="0.8", freshness="0.8"),
            self.assessment("EVID-007", relevance="0.8", source_trust="0.8", freshness="0.8"),
        )
        ranking = {
            item.evidence_id: EvidenceRankingInput(
                item.evidence_id,
                "required" if item.evidence_id == "EVID-001" else "optional",
                item.evidence_id == "EVID-006",
            )
            for item in evidence
        }
        context = self.build(
            evidence=tuple(reversed(evidence)),
            assessments=tuple(reversed(assessments)),
            stances={"EVID-002": "contradicts"},
            ranking_inputs=ranking,
            analyses=(),
            contradictions=(),
            limitations=(),
        )
        self.assertEqual(
            (
                "EVID-001",
                "EVID-002",
                "EVID-003",
                "EVID-004",
                "EVID-005",
                "EVID-006",
                "EVID-007",
            ),
            tuple(item.evidence_id for item in context.evidence_refs),
        )

    def test_missing_or_unversioned_ranking_projection_fails_closed(self) -> None:
        with self.assertRaises(LineageViolation):
            self.build(ranking_inputs={})
        with self.assertRaises(ContractValidationError):
            EvidenceRankingInput(
                "EVID-001", "required", True, "reasoning-ranking-2.0.0"
            )

    def test_both_versioned_model_tokenizers_are_required(self) -> None:
        with self.assertRaises(ContractValidationError):
            StructuredReasoningContextBuilder(token_counters=(
                ReasoningTokenCounter("primary", "primary-v1", "fake-tokenizer-1.0.0", lambda value: 1),
            ))


if __name__ == "__main__":
    unittest.main()
