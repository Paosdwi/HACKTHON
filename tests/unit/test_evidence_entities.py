from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.domain.errors import (  # noqa: E402
    AssessmentSequenceConflict,
    LineageViolation,
)
from crypto_trust_agent.domain.evidence import (  # noqa: E402
    AnalysisProducer,
    AnalysisQuality,
    AnalysisResult,
    ContentOffset,
    ContentReference,
    Evidence,
    EvidenceAssessment,
    EvidenceClaimLink,
    ProducerKind,
    QueryProvenance,
    SourceType,
    Stance,
    ValidationStatus,
    append_assessment,
    select_latest_assessments,
)


class EvidenceEntityTests(unittest.TestCase):
    def evidence(self, **overrides: object) -> Evidence:
        values: dict[str, object] = {
            "evidence_id": "EVID-001",
            "task_id": "TASK-001",
            "execution_id": "EXEC-001",
            "raw_record_id": "RAW-001",
            "source_name": "Official Example",
            "source_type": SourceType.OFFICIAL,
            "source_url": "https://example.test/item",
            "published_at": None,
            "fetched_at": "2026-08-01T01:00:00Z",
            "content_reference": ContentReference(
                kind="quote",
                value="Verifiable excerpt",
                offset=ContentOffset(start=0, end=18),
                unit="unicode_scalar",
            ),
            "raw_locator": "s3://opaque/raw/001",
            "raw_content_hash": "sha256:" + "a" * 64,
            "clean_content_hash": "sha256:" + "b" * 64,
            "query_provenance": QueryProvenance(
                collector="official_collector",
                query="BTC filing",
                parameters={"asset": "BTC"},
                plan_job_id="JOB-001",
            ),
            "validation_status": ValidationStatus.ACTIVE,
            "created_at": "2026-08-01T01:01:00Z",
        }
        values.update(overrides)
        return Evidence(**values)

    def assessment(self, sequence: int, **overrides: object) -> EvidenceAssessment:
        values: dict[str, object] = {
            "assessment_id": f"ASSESS-{sequence:03d}",
            "task_id": "TASK-001",
            "evidence_id": "EVID-001",
            "assessment_sequence": sequence,
            "assessment_version": "1.0.0",
            "ruleset_version": "non-production-trust-1.0.0",
            "source_trust": "0.8",
            "relevance": "0.7",
            "freshness": "0.6",
            "independence": "0.5",
            "independence_group": "GROUP-001",
            "consistency": "0.9",
            "overall_confidence": "0.7",
            "contradiction_severity": "low",
            "computed_at": "2026-08-01T02:00:00Z",
            "limitations": (),
        }
        values.update(overrides)
        return EvidenceAssessment(**values)

    def test_evidence_is_immutable_and_contains_complete_lineage(self) -> None:
        evidence = self.evidence()
        self.assertEqual("s3://opaque/raw/001", evidence.raw_locator)
        self.assertFalse(hasattr(evidence, "source_trust"))
        with self.assertRaises(FrozenInstanceError):
            evidence.source_name = "Changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            evidence.query_provenance.parameters["asset"] = "ETH"  # type: ignore[index]

    def test_web_source_requires_https_but_dataset_may_have_null_url(self) -> None:
        with self.assertRaises(LineageViolation):
            self.evidence(source_url=None)
        dataset = self.evidence(
            source_type=SourceType.DATASET,
            source_url=None,
            source_name="Official CSV",
        )
        self.assertIsNone(dataset.source_url)

    def test_content_reference_offset_is_half_open_and_unit_is_coupled(self) -> None:
        with self.assertRaises(ValueError):
            ContentOffset(start=3, end=3)
        with self.assertRaises(ValueError):
            ContentReference(kind="quote", value="x", offset=None, unit="utf8_byte")

    def test_claim_link_rejects_cross_task_quarantined_and_invalid_stance(self) -> None:
        evidence = self.evidence()
        link = EvidenceClaimLink.for_evidence(
            link_id="LINK-001",
            task_id="TASK-001",
            evidence=evidence,
            claim_id="CLAIM-001",
            stance=Stance.SUPPORTS,
            created_at="2026-08-01T02:00:00Z",
        )
        self.assertEqual(Stance.SUPPORTS, link.stance)
        with self.assertRaises(LineageViolation):
            EvidenceClaimLink.for_evidence(
                link_id="LINK-002",
                task_id="TASK-OTHER",
                evidence=evidence,
                claim_id="CLAIM-002",
                stance=Stance.CONTEXT,
                created_at="2026-08-01T02:00:00Z",
            )
        with self.assertRaises(LineageViolation):
            EvidenceClaimLink.for_evidence(
                link_id="LINK-003",
                task_id="TASK-001",
                evidence=self.evidence(validation_status=ValidationStatus.QUARANTINED),
                claim_id="CLAIM-003",
                stance=Stance.CONTRADICTS,
                created_at="2026-08-01T02:00:00Z",
            )

    def test_assessments_are_append_only_and_latest_uses_max_sequence(self) -> None:
        first = self.assessment(1, computed_at="2026-08-01T03:00:00Z")
        second = self.assessment(2, computed_at="2026-08-01T02:00:00Z")
        history = append_assessment((), first)
        history = append_assessment(history, second)
        latest = select_latest_assessments(history)
        self.assertEqual("ASSESS-002", latest["EVID-001"].assessment_id)
        with self.assertRaises(AssessmentSequenceConflict):
            append_assessment(history, self.assessment(2, assessment_id="ASSESS-OTHER"))

    def test_assessment_probabilities_and_task_scope_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            self.assessment(1, source_trust="1.1")
        with self.assertRaises(LineageViolation):
            append_assessment(
                (self.assessment(1),),
                self.assessment(2, task_id="TASK-OTHER"),
            )

    def test_analysis_result_preserves_inputs_quality_and_versioned_producer(self) -> None:
        result = AnalysisResult(
            analysis_id="AN-001",
            task_id="TASK-001",
            execution_id="EXEC-001",
            analysis_type="market_regime",
            asset="BTC",
            as_of="2026-08-01T00:00:00Z",
            input_refs=("EVID-001",),
            source_refs=("DATASET:BTC:1-30",),
            values={"bullish_probability": "0.42"},
            quality=AnalysisQuality(status="valid", limitations=()),
            producer=AnalysisProducer(
                kind=ProducerKind.MODEL,
                name="market-regime",
                version="1.0.0",
                ruleset_version="features-1.0.0",
            ),
            computed_at="2026-08-01T02:09:00Z",
        )
        self.assertEqual(("EVID-001",), result.input_refs)
        with self.assertRaises(TypeError):
            result.values["bullish_probability"] = "0.9"  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
