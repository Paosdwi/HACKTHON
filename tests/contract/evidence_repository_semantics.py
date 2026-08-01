from __future__ import annotations

from dataclasses import replace

from crypto_trust_agent.application.dto.common import DeadlineDTO
from crypto_trust_agent.application.dto.repositories import (
    AppendAssessmentsRequestDTO,
    AppendEvidenceRequestDTO,
    EvidenceAssessmentDTO,
    EvidenceDTO,
    GetLatestAssessmentsRequestDTO,
    ListEvidenceRequestDTO,
)

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


def deadline(operation_id: str) -> DeadlineDTO:
    return DeadlineDTO("1.0.0", operation_id, "2026-08-01T03:00:00Z", 5_000, "2026-08-01T02:00:00Z", 100)


def evidence(evidence_id: str, *, task_id: str = "TASK-001", source_type: str = "news", status: str = "active") -> EvidenceDTO:
    return EvidenceDTO(
        evidence_id, task_id, "EXEC-001", f"RAW-{evidence_id}", "Example", source_type,
        "https://example.test/item", None, "2026-08-01T02:00:00Z",
        {"kind": "quote", "value": "quote", "offset": {"start": 0, "end": 5}, "unit": "unicode_scalar"},
        f"urn:raw:{evidence_id}", HASH_A, HASH_B,
        {"collector": "fake", "query": "q", "parameters": {}, "plan_job_id": "JOB-001"},
        status, "2026-08-01T02:00:01Z",
    )


def assessment(sequence: int, assessment_id: str) -> EvidenceAssessmentDTO:
    return EvidenceAssessmentDTO(
        assessment_id, "TASK-001", "EVID-001", sequence, "1.0.0", "rules-1.0.0",
        "0.5", "0.5", "0.5", "0.5", "group-1", "0.5", "0.5", "none",
        "2026-08-01T02:00:02Z", (),
    )


class EvidenceRepositorySemanticAssertions:
    """Reusable Core-owned semantic assertions for any EvidenceRepository fake harness."""

    clock: object
    repository: object

    def append(self, item: EvidenceDTO, operation_id: str) -> None:
        result = self.repository.append_evidence(
            AppendEvidenceRequestDTO(operation_id, item.task_id, item, deadline(operation_id))
        )
        self.assertFalse(hasattr(result, "error"), getattr(result, "error", None))

    def test_content_reference_offset_is_unicode_scalar_half_open(self) -> None:
        with self.assertRaises(ValueError):
            replace(evidence("EVID-BAD"), content_reference={"kind": "quote", "value": "x", "offset": {"start": 1, "end": 1}, "unit": "unicode_scalar"})

    def test_snapshot_token_is_opaque_tamper_proof_and_task_filter_bound(self) -> None:
        self.append(evidence("EVID-001"), "OP-SEM-APPEND-1")
        page = self.repository.list_for_task(ListEvidenceRequestDTO("OP-SEM-LIST-1", "TASK-001", deadline("OP-SEM-LIST-1"), source_type="news", validation_status="active", limit=1))
        self.assertNotIn("TASK-001", page.snapshot_token)
        self.assertNotIn("news", page.snapshot_token)
        tampered = page.snapshot_token[:-1] + ("A" if page.snapshot_token[-1] != "A" else "B")
        rejects = (
            ListEvidenceRequestDTO("OP-SEM-TAMPER", "TASK-001", deadline("OP-SEM-TAMPER"), source_type="news", validation_status="active", snapshot_token=tampered),
            ListEvidenceRequestDTO("OP-SEM-TASK", "TASK-OTHER", deadline("OP-SEM-TASK"), source_type="news", validation_status="active", snapshot_token=page.snapshot_token),
            ListEvidenceRequestDTO("OP-SEM-SOURCE", "TASK-001", deadline("OP-SEM-SOURCE"), source_type="official", validation_status="active", snapshot_token=page.snapshot_token),
            ListEvidenceRequestDTO("OP-SEM-STATUS", "TASK-001", deadline("OP-SEM-STATUS"), source_type="news", validation_status="quarantined", snapshot_token=page.snapshot_token),
        )
        for request in rejects:
            result = self.repository.list_for_task(request)
            self.assertEqual("snapshot_expired", result.error.code)

    def test_cursor_must_have_been_issued_for_the_same_snapshot(self) -> None:
        self.append(evidence("EVID-001"), "OP-SEM-CURSOR-APPEND-1")
        self.append(evidence("EVID-002"), "OP-SEM-CURSOR-APPEND-2")
        page = self.repository.list_for_task(ListEvidenceRequestDTO("OP-SEM-CURSOR-LIST", "TASK-001", deadline("OP-SEM-CURSOR-LIST"), limit=1))
        forged = self.repository.list_for_task(ListEvidenceRequestDTO("OP-SEM-CURSOR-FORGED", "TASK-001", deadline("OP-SEM-CURSOR-FORGED"), snapshot_token=page.snapshot_token, cursor=f"{page.snapshot_token}:999", limit=1))
        self.assertEqual("snapshot_expired", forged.error.code)

    def test_explicit_ttl_is_valid_before_boundary_and_expired_at_boundary(self) -> None:
        self.append(evidence("EVID-001"), "OP-SEM-TTL-APPEND")
        page = self.repository.list_for_task(ListEvidenceRequestDTO("OP-SEM-TTL-LIST", "TASK-001", deadline("OP-SEM-TTL-LIST")))
        self.clock.advance(monotonic_ms=999)
        before = self.repository.list_for_task(ListEvidenceRequestDTO("OP-SEM-TTL-BEFORE", "TASK-001", deadline("OP-SEM-TTL-BEFORE"), snapshot_token=page.snapshot_token))
        self.assertFalse(hasattr(before, "error"))
        self.clock.advance(monotonic_ms=1)
        expired = self.repository.list_for_task(ListEvidenceRequestDTO("OP-SEM-TTL-BOUNDARY", "TASK-001", deadline("OP-SEM-TTL-BOUNDARY"), snapshot_token=page.snapshot_token))
        self.assertEqual("snapshot_expired", expired.error.code)

    def test_latest_assessments_are_frozen_and_expire_with_the_snapshot(self) -> None:
        self.append(evidence("EVID-001"), "OP-SEM-LATEST-APPEND")
        first = assessment(1, "ASSESS-001")
        second = assessment(2, "ASSESS-002")
        self.repository.append_assessments(AppendAssessmentsRequestDTO("OP-SEM-ASSESS-1", "TASK-001", (first,), deadline("OP-SEM-ASSESS-1")))
        page = self.repository.list_for_task(ListEvidenceRequestDTO("OP-SEM-LATEST-LIST", "TASK-001", deadline("OP-SEM-LATEST-LIST"), validation_status="active"))
        self.repository.append_assessments(AppendAssessmentsRequestDTO("OP-SEM-ASSESS-2", "TASK-001", (second,), deadline("OP-SEM-ASSESS-2")))
        latest_request = GetLatestAssessmentsRequestDTO("OP-SEM-LATEST", "TASK-001", ("EVID-001",), page.snapshot_token, deadline("OP-SEM-LATEST"))
        latest = self.repository.get_latest_assessments(latest_request)
        self.assertEqual((first,), latest.items)
        self.clock.advance(monotonic_ms=1_000)
        expired = self.repository.get_latest_assessments(replace(latest_request, operation_id="OP-SEM-LATEST-EXPIRED", deadline=deadline("OP-SEM-LATEST-EXPIRED")))
        self.assertEqual("snapshot_expired", expired.error.code)

    def test_duplicate_write_operations_replay_same_payload_and_reject_different_payload(self) -> None:
        first = evidence("EVID-001")
        request = AppendEvidenceRequestDTO("OP-SEM-REPLAY", "TASK-001", first, deadline("OP-SEM-REPLAY"))
        self.assertEqual(first, self.repository.append_evidence(request))
        self.assertEqual(first, self.repository.append_evidence(request))
        conflict = self.repository.append_evidence(replace(request, evidence=replace(first, source_name="Changed")))
        self.assertEqual("evidence_immutable", conflict.error.code)
