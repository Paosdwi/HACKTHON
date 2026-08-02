from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.common import DeadlineDTO
from crypto_trust_agent.application.dto.evidence_extractor import ExtractedClaimDTO, ExtractionProviderDTO, ExtractionResultDTO, UsageDTO
from crypto_trust_agent.application.dto.repositories import AppendAssessmentsRequestDTO, EvidenceAssessmentDTO
from crypto_trust_agent.application.dto.source_collector import QueryProvenanceDTO, RawRecordDTO, SecurityResultDTO
from crypto_trust_agent.application.use_cases.normalize_evidence import NormalizeEvidenceCommand, NormalizeEvidenceUseCase
from crypto_trust_agent.application.use_cases.select_evidence_assessments import (
    LatestAssessmentSelectionCommand,
    LatestAssessmentSelectionUseCase,
)
from crypto_trust_agent.infrastructure.fakes import FakeClock, FakeEventPublisher, FakeEvidenceRepository


class Ids:
    def __init__(self) -> None:
        self.count = 0
    def __call__(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}{self.count:04d}"


def deadline(operation_id: str) -> DeadlineDTO:
    return DeadlineDTO("1.0.0", operation_id, "2026-08-01T03:00:00Z", 5_000, "2026-08-01T02:00:00Z", 100)


class EvidenceWorkflowIntegrationTests(unittest.TestCase):
    def test_raw_extraction_to_latest_assessment_audit_without_source_fetch_or_model_call(self) -> None:
        clock = FakeClock("2026-08-01T02:00:00Z")
        repository = FakeEvidenceRepository(clock, snapshot_ttl_seconds=10)
        events = FakeEventPublisher(clock)
        ids = Ids()
        content = "Official update: BTC reserves increased."
        clean_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        raw = RawRecordDTO(
            "RAW-001", "TASK-001", "EXEC-001", "JOB-001", "Official", "official",
            "https://example.test/update", "https://example.test/update", 200, None,
            "2026-08-01T01:59:00Z", "sha256:" + "a" * 64, clean_hash,
            "urn:raw:001", "text/plain", content,
            QueryProvenanceDTO("fake_collector", "BTC reserves", {"asset": "BTC"}),
            SecurityResultDTO("1.0.0", True, True, True, 0),
        )
        extracted = ExtractionResultDTO(
            "valid", "RAW-001", ExtractionProviderDTO("fake", "1.0.0", "inv-1"),
            (ExtractedClaimDTO("XCL-001", "BTC reserves increased", "BTC reserves increased", ("BTC",), "official_update", "positive", "high"),),
            (), UsageDTO(1, 1), "2026-08-01T01:59:01Z", "2026-08-01T01:59:02Z",
        )
        normalized = NormalizeEvidenceUseCase(repository, clock, ids).execute(NormalizeEvidenceCommand(
            "OP-NORMALIZE-INTEGRATION", "TASK-001", "EXEC-001", "RAW-001", "sha256:" + "a" * 64,
            raw, extracted, {"XCL-001": "supports"},
        ))
        evidence_id = normalized.items[0].evidence.evidence_id
        first = EvidenceAssessmentDTO("ASSESS-001", "TASK-001", evidence_id, 1, "1.0.0", "rules-1.0.0", "0.5", "0.5", "0.5", "0.5", "group", "0.5", "0.5", "none", clock.current_utc(), ())
        second = EvidenceAssessmentDTO("ASSESS-002", "TASK-001", evidence_id, 2, "1.0.0", "rules-2.0.0", "0.6", "0.6", "0.6", "0.6", "group", "0.6", "0.6", "low", clock.current_utc(), ())
        repository.append_assessments(AppendAssessmentsRequestDTO("OP-ASSESS-1", "TASK-001", (first,), deadline("OP-ASSESS-1")))
        repository.append_assessments(AppendAssessmentsRequestDTO("OP-ASSESS-2", "TASK-001", (second,), deadline("OP-ASSESS-2")))

        selected = LatestAssessmentSelectionUseCase(repository, events, clock, ids).execute(
            LatestAssessmentSelectionCommand("OP-LATEST-INTEGRATION", "TASK-001", "EXEC-001", (evidence_id,))
        )
        self.assertEqual((second,), selected.items)
        self.assertEqual("assessment-selection-1.0.0", selected.selection_ruleset_version)
        self.assertEqual(1, len(events.recorded_events))
        audit = events.recorded_events[0]
        self.assertEqual("ASSESS-002", audit.result_summary["assessment_ids"])
        self.assertEqual("assessment-selection-1.0.0", audit.result_summary["selection_ruleset_version"])
        self.assertEqual("rules-2.0.0", audit.result_summary["assessment_rulesets"])
        self.assertEqual("1.0.0", audit.result_summary["assessment_versions"])
        self.assertNotIn(content, repr(audit.to_wire()))


if __name__ == "__main__":
    unittest.main()
