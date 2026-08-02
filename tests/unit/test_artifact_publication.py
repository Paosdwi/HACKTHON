from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.reasoning import (  # noqa: E402
    AnalysisRefDTO,
    ConclusionDTO,
    ConfidenceComponentsDTO,
    ContradictionDTO,
    FactDTO,
    InferenceDTO,
)
from crypto_trust_agent.application.dto.repositories import (  # noqa: E402
    EvidenceAssessmentDTO,
    EvidenceClaimLinkDTO,
    EvidenceDTO,
    ExecutionEventDTO,
)
from crypto_trust_agent.application.publication import (  # noqa: E402
    CitedStatementDTO,
    EvidenceListDTO,
    EvidenceListEntryDTO,
    ExecutionLogDTO,
    FinalReportDTO,
    KeyEvidenceDTO,
    MarketDataProvenanceDTO,
    PublicationValidationError,
    render_evidence_csv,
    render_html,
    render_markdown,
    validate_canonical_decimal_wire,
    validate_publication,
)


def evidence(evidence_id: str = "EVID-001", *, execution_id: str = "EXEC-001") -> EvidenceDTO:
    suffix = evidence_id.removeprefix("EVID-")
    return EvidenceDTO(
        evidence_id=evidence_id,
        task_id="TASK-001",
        execution_id=execution_id,
        raw_record_id=f"RAW-{suffix}",
        source_name="Official Example",
        source_type="official",
        source_url=f"https://example.test/{suffix}",
        published_at="2026-07-31T00:00:00Z",
        fetched_at="2026-08-01T02:00:00Z",
        content_reference={"kind": "quote", "value": f"Evidence {suffix}", "offset": {"start": 0, "end": 10}, "unit": "unicode_scalar"},
        raw_locator=f"urn:cryptotrust:raw:{suffix}",
        raw_content_hash="sha256:" + "a" * 64,
        clean_content_hash="sha256:" + "b" * 64,
        query_provenance={"collector": "official_collector", "query": "BTC status", "parameters": {"asset": "BTC"}, "plan_job_id": f"JOB-{suffix}"},
        validation_status="active",
        created_at="2026-08-01T02:01:00Z",
    )


def assessment(evidence_id: str = "EVID-001", *, sequence: int = 1, assessment_id: str | None = None) -> EvidenceAssessmentDTO:
    return EvidenceAssessmentDTO(
        assessment_id=assessment_id or f"ASSESS-{evidence_id.removeprefix('EVID-')}-{sequence}",
        task_id="TASK-001",
        evidence_id=evidence_id,
        assessment_sequence=sequence,
        assessment_version="1.0.0",
        ruleset_version="trust-1.0.0",
        source_trust="0.8",
        relevance="0.9",
        freshness="0.7",
        independence="0.6",
        independence_group=f"official:{evidence_id}",
        consistency="0.85",
        overall_confidence="0.82",
        contradiction_severity="low",
        computed_at="2026-08-01T02:02:00Z",
        limitations=(),
    )


def evidence_list() -> EvidenceListDTO:
    items = (evidence("EVID-001"), evidence("EVID-002"))
    links = (
        EvidenceClaimLinkDTO("LINK-001", "TASK-001", "EVID-001", "CLAIM-001", "supports", "2026-08-01T02:02:00Z"),
        EvidenceClaimLinkDTO("LINK-002", "TASK-001", "EVID-002", "CLAIM-001", "contradicts", "2026-08-01T02:02:00Z"),
    )
    assessments = (
        assessment("EVID-001", sequence=1),
        assessment("EVID-001", sequence=2, assessment_id="ASSESS-001-LATEST"),
        assessment("EVID-002", sequence=1),
    )
    return EvidenceListDTO.from_records(
        task_id="TASK-001",
        execution_id="EXEC-001",
        evidence=items,
        claim_links=links,
        assessments=assessments,
        generated_at="2026-08-01T02:10:00Z",
    )


def report(*, evidence_ref: str = "EVID-001", live_extension: bool = True) -> FinalReportDTO:
    return FinalReportDTO(
        task_id="TASK-001",
        execution_id="EXEC-001",
        assets=("BTC",),
        question="What is the BTC market status?",
        market_judgment=CitedStatementDTO("BTC is range-bound.", (evidence_ref,), ("ANALYSIS-001",)),
        facts=(FactDTO("FACT-001", "Official evidence is available.", (evidence_ref,), ()),),
        analyses=(AnalysisRefDTO("ANALYSIS-001", "1.0.0", "Market regime analysis.", ("DATASET:BTC-001",)),),
        inferences=(InferenceDTO("INFER-001", "Signals are mixed.", ("FACT-001",), "0.75"),),
        conclusions=(ConclusionDTO("CONCL-001", "Maintain a neutral view.", ("FACT-001",), ("INFER-001",), "0.72"),),
        key_evidence=(KeyEvidenceDTO(evidence_ref, None, "Official evidence anchors the judgment."),),
        supporting_evidence_ids=(evidence_ref,),
        counter_evidence_ids=("EVID-002",),
        contradictions=(ContradictionDTO("CONTRA-001", ("EVID-001", "EVID-002"), "medium", "Signals disagree."),),
        confidence_components=ConfidenceComponentsDTO("0.8", "0.7", "0.75", "0.76"),
        limitations=("Live extension has a shorter history.",),
        watchpoints=("Watch volume confirmation.",),
        source_consistency=CitedStatementDTO("Official and live sources are directionally consistent.", ("EVID-001",), ("ANALYSIS-001",)),
        market_data_provenance=MarketDataProvenanceDTO(
            official_dataset=True,
            live_extension=live_extension,
            transition_date="2026-05-31" if live_extension else None,
        ),
        generated_at="2026-08-01T02:10:00Z",
    )


def execution_log() -> ExecutionLogDTO:
    event = ExecutionEventDTO(
        event_id="EVT-001",
        timestamp="2026-08-01T02:05:00Z",
        task_id="TASK-001",
        execution_id="EXEC-001",
        step="collect_official",
        tool="official_collector",
        status="completed",
        duration_ms=20,
        retry_count=0,
        sanitized_parameters={"asset": "BTC"},
        result_summary={"records": 2},
        error=None,
        deadline_remaining_ms=300000,
        correlation={"operation_id": "OP-EVENT-001", "causation_event_id": None},
    )
    return ExecutionLogDTO.from_events(
        (event,),
        details_by_event={
            "EVT-001": {
                "query_provenance": {"query": "BTC status", "token": "TOP-SECRET"},
                "source_fetch_result": {"outcome": "success", "raw_content": "sensitive body"},
                "assessment_versions": (
                    {"assessment_id": "ASSESS-001-LATEST", "assessment_version": "1.0.0"},
                    {"assessment_id": "ASSESS-002-1", "assessment_version": "1.0.0"},
                ),
            }
        },
        generated_at="2026-08-01T02:10:00Z",
    )


class CanonicalPublicationModelTests(unittest.TestCase):
    def test_final_report_contains_required_sections_and_market_provenance(self) -> None:
        payload = report().to_wire()
        required = {
            "assets", "question", "market_judgment", "facts", "inferences", "conclusions",
            "key_evidence", "supporting_evidence_ids", "counter_evidence_ids", "contradictions",
            "confidence_components", "limitations", "watchpoints", "source_consistency",
            "market_data_provenance", "transition_date", "renderer_failures",
        }
        self.assertTrue(required.issubset(payload))
        self.assertTrue(payload["market_data_provenance"]["official_dataset"])
        self.assertTrue(payload["market_data_provenance"]["live_extension"])
        self.assertEqual("2026-05-31", payload["transition_date"])
        with self.assertRaisesRegex(PublicationValidationError, "transition_date"):
            MarketDataProvenanceDTO(True, True, "2026-06-01")
        validate_publication(report(), evidence_list(), execution_log())

    def test_citation_traceability_and_lineage_fail_closed(self) -> None:
        with self.assertRaisesRegex(PublicationValidationError, "unresolved evidence"):
            validate_publication(report(evidence_ref="EVID-999"), evidence_list(), execution_log())
        with self.assertRaisesRegex(PublicationValidationError, "execution_id"):
            EvidenceListDTO.from_records(
                task_id="TASK-001",
                execution_id="EXEC-001",
                evidence=(evidence(execution_id="EXEC-OTHER"),),
                claim_links=(),
                assessments=(assessment(),),
                generated_at="2026-08-01T02:10:00Z",
            )

    def test_evidence_list_has_lineage_related_claim_and_latest_assessment(self) -> None:
        payload = evidence_list().to_wire()
        first = payload["items"][0]
        self.assertEqual("Official Example", first["source"])
        self.assertEqual("https://example.test/001", first["source_url"])
        self.assertEqual("sha256:" + "b" * 64, first["content_hash"])
        self.assertEqual("CLAIM-001", first["related_claims"][0]["claim_id"])
        self.assertEqual("RAW-001", first["lineage"]["raw_record_id"])
        self.assertEqual("ASSESS-001-LATEST", first["assessment_id"])
        self.assertEqual("1.0.0", first["assessment_version"])

    def test_evidence_list_entry_direct_construction_fails_closed(self) -> None:
        valid = evidence_list().items[0]
        invalid_cases = (
            {"schema_version": "2.0.0"},
            {"evidence_id": "invalid"},
            {"source": ""},
            {"source_type": ""},
            {"source_url": "http://example.test/source"},
            {"source_locator": ""},
            {"fetched_at": "not-a-utc-instant"},
            {"content_reference": {}},
            {"related_claims": ()},
            {"related_claims": ({"claim_id": "invalid", "stance": "supports"},)},
            {"related_claims": ({"claim_id": "CLAIM-001", "stance": "invalid"},)},
            {"content_hash": "not-a-sha256"},
            {"lineage": {"task_id": "TASK-001"}},
            {"assessment_id": "invalid"},
            {"assessment_version": "v1"},
            {"assessment_sequence": 0},
        )
        for changes in invalid_cases:
            with self.subTest(changes=changes), self.assertRaises(PublicationValidationError):
                replace(valid, **changes)
        self.assertIsInstance(valid, EvidenceListEntryDTO)

    def test_evidence_list_rejects_evidence_without_related_claim(self) -> None:
        with self.assertRaisesRegex(PublicationValidationError, "related claim"):
            EvidenceListDTO.from_records(
                task_id="TASK-001",
                execution_id="EXEC-001",
                evidence=(evidence(),),
                claim_links=(),
                assessments=(assessment(),),
                generated_at="2026-08-01T02:10:00Z",
            )

    def test_execution_log_is_jsonl_and_redacts_forbidden_content(self) -> None:
        content = execution_log().canonical_jsonl()
        self.assertTrue(content.endswith(b"\n"))
        line = json.loads(content.decode("utf-8"))
        self.assertIn("query_provenance", line)
        self.assertIn("source_fetch_result", line)
        self.assertIn("assessment_versions", line)
        lowered = content.decode("utf-8").lower()
        self.assertNotIn("top-secret", lowered)
        self.assertNotIn("sensitive body", lowered)
        self.assertNotIn('"token"', lowered)
        self.assertNotIn('"raw_content"', lowered)

    def test_canonical_decimal_validation_rejects_float_and_noncanonical_strings(self) -> None:
        validate_canonical_decimal_wire(report().to_wire())
        for payload in ({"confidence": 0.8}, {"overall_confidence": "0.80"}, {"probability": "1e-1"}):
            with self.subTest(payload=payload), self.assertRaises(PublicationValidationError):
                validate_canonical_decimal_wire(payload)

    def test_renderers_use_canonical_models_and_escape_html_and_csv(self) -> None:
        canonical_report = replace(report(), question="Is <BTC> safe?")
        markdown = render_markdown(canonical_report).decode("utf-8")
        html = render_html(canonical_report).decode("utf-8")
        csv_text = render_evidence_csv(evidence_list()).decode("utf-8")
        self.assertIn("# CryptoTrust Final Report", markdown)
        self.assertIn("Market judgment", markdown)
        self.assertIn("Is &lt;BTC&gt; safe?", html)
        self.assertNotIn("Is <BTC> safe?", html)
        self.assertIn("evidence_id,source,source_url", csv_text.splitlines()[0])
        self.assertIn("EVID-001", csv_text)


if __name__ == "__main__":
    unittest.main()
