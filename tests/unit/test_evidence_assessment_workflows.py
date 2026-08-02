from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.common import DeadlineDTO
from crypto_trust_agent.application.dto.repositories import (
    AppendAssessmentsRequestDTO,
    AppendEvidenceRequestDTO,
    EvidenceAssessmentDTO,
    EvidenceDTO,
)
from crypto_trust_agent.application.use_cases.append_evidence_assessment import (
    AppendEvidenceAssessmentCommand,
    AppendEvidenceAssessmentRejected,
    AppendEvidenceAssessmentUseCase,
)
from crypto_trust_agent.application.use_cases.select_evidence_assessments import (
    LatestAssessmentSelectionCommand,
    LatestAssessmentSelectionRejected,
    LatestAssessmentSelectionUseCase,
)
from crypto_trust_agent.infrastructure.fakes import (
    FakeClock,
    FakeEventPublisher,
    FakeEvidenceRepository,
)


class SequentialIds:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def __call__(self, prefix: str) -> str:
        value = self.counts.get(prefix, 0) + 1
        self.counts[prefix] = value
        return f"{prefix}{value:04d}"


def deadline(operation_id: str) -> DeadlineDTO:
    return DeadlineDTO(
        "1.0.0",
        operation_id,
        "2026-08-01T03:00:00Z",
        5_000,
        "2026-08-01T02:00:00Z",
        100,
    )


def evidence(
    evidence_id: str = "EVID-001",
    *,
    task_id: str = "TASK-001",
    execution_id: str = "EXEC-001",
    status: str = "active",
) -> EvidenceDTO:
    return EvidenceDTO(
        evidence_id,
        task_id,
        execution_id,
        "RAW-001",
        "Official",
        "official",
        "https://example.test/item",
        None,
        "2026-08-01T02:00:00Z",
        {
            "kind": "quote",
            "value": "quote",
            "offset": {"start": 0, "end": 5},
            "unit": "unicode_scalar",
        },
        "urn:raw:001",
        "sha256:" + "a" * 64,
        "sha256:" + "b" * 64,
        {"collector": "fake", "query": "q", "parameters": {}, "plan_job_id": "JOB-001"},
        status,
        "2026-08-01T02:00:01Z",
    )


def command(**changes: object) -> AppendEvidenceAssessmentCommand:
    values: dict[str, object] = {
        "operation_id": "OP-ASSESS-WORKFLOW-001",
        "task_id": "TASK-001",
        "execution_id": "EXEC-001",
        "evidence_id": "EVID-001",
        "assessment_sequence": 1,
        "assessment_version": "1.0.0",
        "ruleset_version": "rules-1.0.0",
        "source_trust": "0.5",
        "relevance": "0.6",
        "freshness": "0.7",
        "independence": "0.8",
        "independence_group": "group-1",
        "consistency": "0.9",
        "overall_confidence": "0.75",
        "contradiction_severity": "none",
        "limitations": (),
    }
    values.update(changes)
    return AppendEvidenceAssessmentCommand(**values)


class EvidenceAssessmentWorkflowUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock("2026-08-01T02:00:02Z")
        self.repository = FakeEvidenceRepository(self.clock, snapshot_ttl_seconds=1)
        self.ids = SequentialIds()
        self.repository.append_evidence(
            AppendEvidenceRequestDTO(
                "OP-SETUP-EVIDENCE",
                "TASK-001",
                evidence(),
                deadline("OP-SETUP-EVIDENCE"),
            )
        )

    def test_append_builds_versioned_assessment_with_injected_id_and_replays_safely(self) -> None:
        use_case = AppendEvidenceAssessmentUseCase(self.repository, self.clock, self.ids)
        first = use_case.execute(command())
        self.assertEqual("ASSESS-0001", first.assessment_id)
        self.assertEqual("2026-08-01T02:00:02Z", str(first.computed_at))
        self.assertEqual("1.0.0", first.assessment_version)
        self.assertIs(first, use_case.execute(command()))
        with self.assertRaises(AppendEvidenceAssessmentRejected) as raised:
            use_case.execute(command(overall_confidence="0.74"))
        self.assertEqual("operation_payload_conflict", raised.exception.code)
        self.assertEqual((first,), tuple(self.repository._assessments.values()))

    def test_append_rejects_missing_quarantined_cross_task_and_cross_execution_references(self) -> None:
        quarantined = evidence("EVID-QUARANTINED", status="quarantined")
        other_task = evidence("EVID-OTHER-TASK", task_id="TASK-OTHER")
        other_execution = evidence("EVID-OTHER-EXEC", execution_id="EXEC-OTHER")
        for index, item in enumerate((quarantined, other_task, other_execution), start=1):
            self.repository.append_evidence(
                AppendEvidenceRequestDTO(
                    f"OP-SETUP-{index}", item.task_id, item, deadline(f"OP-SETUP-{index}")
                )
            )
        cases = (
            (command(operation_id="OP-MISSING", evidence_id="EVID-MISSING"), "evidence_not_found"),
            (command(operation_id="OP-QUARANTINED", evidence_id="EVID-QUARANTINED"), "quarantined_reference"),
            (command(operation_id="OP-CROSS-TASK", evidence_id="EVID-OTHER-TASK"), "cross_task_reference"),
            (command(operation_id="OP-CROSS-EXEC", evidence_id="EVID-OTHER-EXEC"), "execution_lineage_mismatch"),
        )
        use_case = AppendEvidenceAssessmentUseCase(self.repository, self.clock, self.ids)
        for candidate, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(AppendEvidenceAssessmentRejected) as raised:
                    use_case.execute(candidate)
                self.assertEqual(code, raised.exception.code)

    def test_latest_uses_max_sequence_and_audits_id_version_without_content(self) -> None:
        first = EvidenceAssessmentDTO(
            "ASSESS-001", "TASK-001", "EVID-001", 1, "1.0.0", "rules-1.0.0",
            "0.5", "0.5", "0.5", "0.5", "group-1", "0.5", "0.5", "none",
            self.clock.current_utc(), (),
        )
        second = replace(first, assessment_id="ASSESS-002", assessment_sequence=2, assessment_version="1.1.0")
        for index, item in enumerate((first, second), start=1):
            operation_id = f"OP-ASSESS-{index}"
            self.repository.append_assessments(
                AppendAssessmentsRequestDTO(operation_id, "TASK-001", (item,), deadline(operation_id))
            )
        events = FakeEventPublisher(self.clock)
        use_case = LatestAssessmentSelectionUseCase(self.repository, events, self.clock, self.ids)
        latest = use_case.execute(
            LatestAssessmentSelectionCommand(
                "OP-LATEST-001", "TASK-001", "EXEC-001", ("EVID-001",)
            )
        )
        self.assertEqual((second,), latest.items)
        audit = events.recorded_events[0]
        self.assertEqual("ASSESS-002", audit.result_summary["assessment_ids"])
        self.assertEqual("1.1.0", audit.result_summary["assessment_versions"])
        self.assertNotIn("quote", repr(audit.to_wire()))

    def test_latest_rejects_missing_quarantined_and_cross_execution_references(self) -> None:
        quarantined = evidence("EVID-QUARANTINED", status="quarantined")
        other_execution = evidence("EVID-OTHER-EXEC", execution_id="EXEC-OTHER")
        for index, item in enumerate((quarantined, other_execution), start=1):
            self.repository.append_evidence(
                AppendEvidenceRequestDTO(
                    f"OP-LATEST-SETUP-{index}", item.task_id, item,
                    deadline(f"OP-LATEST-SETUP-{index}"),
                )
            )
        cases = (
            (("EVID-MISSING",), "evidence_not_found"),
            (("EVID-QUARANTINED",), "quarantined_reference"),
            (("EVID-OTHER-EXEC",), "execution_lineage_mismatch"),
        )
        for index, (evidence_ids, code) in enumerate(cases, start=1):
            use_case = LatestAssessmentSelectionUseCase(
                self.repository, FakeEventPublisher(self.clock), self.clock, SequentialIds()
            )
            with self.subTest(code=code):
                with self.assertRaises(LatestAssessmentSelectionRejected) as raised:
                    use_case.execute(
                        LatestAssessmentSelectionCommand(
                            f"OP-LATEST-REJECT-{index}", "TASK-001", "EXEC-001", evidence_ids
                        )
                    )
                self.assertEqual(code, raised.exception.code)


if __name__ == "__main__":
    unittest.main()
