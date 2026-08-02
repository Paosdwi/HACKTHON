from __future__ import annotations

import base64
import hashlib
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.common import DeadlineDTO  # noqa: E402
from crypto_trust_agent.application.dto.reasoning import (  # noqa: E402
    AnalysisRefDTO, ConclusionDTO, ConfidenceComponentsDTO, FactDTO, InferenceDTO,
)
from crypto_trust_agent.application.dto.repositories import (  # noqa: E402
    ArtifactExecutionRequestDTO, ArtifactPutRequestDTO, EvidenceAssessmentDTO,
    EvidenceClaimLinkDTO, EvidenceDTO, ExecutionEventDTO,
)
from crypto_trust_agent.application.publication import (  # noqa: E402
    ArtifactPublicationRequest,
    ArtifactPublicationService,
    CitedStatementDTO,
    EvidenceListDTO,
    ExecutionLogDTO,
    FinalReportDTO,
    KeyEvidenceDTO,
    MarketDataProvenanceDTO,
    PublicationError,
    PublicationRenderers,
    render_evidence_csv,
    render_html,
    render_markdown,
)
from crypto_trust_agent.infrastructure.fakes.artifacts import FakeArtifactRepository  # noqa: E402
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock  # noqa: E402


def deadline(operation_id: str = "OP-PUB-001") -> DeadlineDTO:
    return DeadlineDTO("1.0.0", operation_id, "2026-08-01T02:20:00Z", 300000, "2026-08-01T02:10:00Z", 1000)


def short_deadline(operation_id: str = "OP-PUB-SHORT") -> DeadlineDTO:
    return DeadlineDTO("1.0.0", operation_id, "2026-08-01T02:10:00.250Z", 250, "2026-08-01T02:10:00Z", 100)


def bundle(question: str = "BTC status?") -> ArtifactPublicationRequest:
    evidence = EvidenceDTO(
        "EVID-001", "TASK-001", "EXEC-001", "RAW-001", "Official Example", "dataset", None,
        None, "2026-08-01T02:00:00Z",
        {"kind": "metric", "value": "0.42", "offset": None, "unit": None},
        "urn:cryptotrust:raw:001", "sha256:" + "a" * 64, "sha256:" + "b" * 64,
        {"collector": "official_dataset", "query": "BTC daily", "parameters": {"asset": "BTC"}, "plan_job_id": "JOB-001"},
        "active", "2026-08-01T02:01:00Z",
    )
    assessment = EvidenceAssessmentDTO(
        "ASSESS-001", "TASK-001", "EVID-001", 1, "1.0.0", "trust-1.0.0",
        "0.9", "0.9", "0.8", "1", "official_dataset", "0.9", "0.88", "none",
        "2026-08-01T02:02:00Z", (),
    )
    claim_link = EvidenceClaimLinkDTO(
        "LINK-001", "TASK-001", "EVID-001", "CLAIM-001", "supports",
        "2026-08-01T02:02:00Z",
    )
    evidence_output = EvidenceListDTO.from_records(
        task_id="TASK-001", execution_id="EXEC-001", evidence=(evidence,), claim_links=(claim_link,),
        assessments=(assessment,), generated_at="2026-08-01T02:10:00Z",
    )
    report = FinalReportDTO(
        task_id="TASK-001", execution_id="EXEC-001", assets=("BTC",), question=question,
        market_judgment=CitedStatementDTO("Neutral market regime.", ("EVID-001",), ("ANALYSIS-001",)),
        facts=(FactDTO("FACT-001", "Dataset signal is mixed.", ("EVID-001",), ()),),
        analyses=(AnalysisRefDTO("ANALYSIS-001", "1.0.0", "Deterministic analysis.", ("DATASET:BTC-001",)),),
        inferences=(InferenceDTO("INFER-001", "No directional edge.", ("FACT-001",), "0.7"),),
        conclusions=(ConclusionDTO("CONCL-001", "Remain neutral.", ("FACT-001",), ("INFER-001",), "0.7"),),
        key_evidence=(KeyEvidenceDTO("EVID-001", None, "Official dataset anchors the report."),),
        supporting_evidence_ids=("EVID-001",), counter_evidence_ids=(), contradictions=(),
        confidence_components=ConfidenceComponentsDTO("0.9", "0.8", "0.8", "0.84"),
        limitations=("No live order-book data.",), watchpoints=("Watch regime transition.",),
        source_consistency=CitedStatementDTO("Sources are internally consistent.", ("EVID-001",), ()),
        market_data_provenance=MarketDataProvenanceDTO(True, False, None),
        generated_at="2026-08-01T02:10:00Z",
    )
    event = ExecutionEventDTO(
        "EVT-001", "2026-08-01T02:09:00Z", "TASK-001", "EXEC-001", "reasoning", "reasoning_provider",
        "completed", 50, 0, {"model_role": "primary"}, {"outcome": "valid"}, None, 60000,
        {"operation_id": "OP-EVENT-001", "causation_event_id": None},
    )
    log = ExecutionLogDTO.from_events(
        (event,),
        details_by_event={"EVT-001": {"reasoning_sequence": {"primary": "valid"}, "assessment_versions": ({"assessment_id": "ASSESS-001", "assessment_version": "1.0.0"},)}},
        generated_at="2026-08-01T02:10:00Z",
    )
    return ArtifactPublicationRequest(report, evidence_output, log, deadline())


class RecordingArtifactRepository:
    def __init__(self, delegate: FakeArtifactRepository) -> None:
        self.delegate = delegate
        self.calls: list[str] = []
        self.requests: list[object] = []

    def put(self, request):
        self.calls.append(f"put:{request.artifact_type}")
        self.requests.append(request)
        return self.delegate.put(request)

    def get(self, request):
        return self.delegate.get(request)

    def list_for_execution(self, request):
        return self.delegate.list_for_execution(request)

    def put_manifest(self, request):
        self.calls.append("put_manifest")
        self.requests.append(request)
        return self.delegate.put_manifest(request)

    def get_manifest(self, request):
        return self.delegate.get_manifest(request)


class BlockingArtifactRepository(RecordingArtifactRepository):
    def __init__(self, delegate: FakeArtifactRepository, block_method: str) -> None:
        super().__init__(delegate)
        self.block_method = block_method
        self.entered = Event()
        self.release = Event()
        self.late_finished = Event()
        self.put_attempts = 0
        self.manifest_attempts = 0
        self._blocked = False

    def put(self, request):
        self.put_attempts += 1
        self.calls.append(f"put:{request.artifact_type}")
        self.requests.append(request)
        if self.block_method == "put" and not self._blocked:
            self._blocked = True
            self.entered.set()
            self.release.wait()
            result = self.delegate.put(request)
            self.late_finished.set()
            return result
        return self.delegate.put(request)

    def put_manifest(self, request):
        self.manifest_attempts += 1
        self.calls.append("put_manifest")
        self.requests.append(request)
        if self.block_method == "put_manifest" and not self._blocked:
            self._blocked = True
            self.entered.set()
            self.release.wait()
            self.late_finished.set()
            return object()
        return self.delegate.put_manifest(request)


class AdvancingArtifactRepository(RecordingArtifactRepository):
    def __init__(self, delegate: FakeArtifactRepository, clock: FakeClock) -> None:
        super().__init__(delegate)
        self.clock = clock
        self.advanced = False

    def put(self, request):
        result = super().put(request)
        if not self.advanced:
            self.advanced = True
            self.clock.advance(wall_seconds=25, monotonic_ms=25_000)
        return result


class ExplodingClock:
    def now_utc(self, request):
        del request
        raise RuntimeError("clock secret must not escape")

    def monotonic_ms(self, request):
        del request
        raise RuntimeError("clock secret must not escape")


class ArtifactPublicationFakeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock("2026-08-01T02:10:00Z")
        self.fake = FakeArtifactRepository(self.clock)
        self.repository = RecordingArtifactRepository(self.fake)

    def test_normal_bundle_hashes_manifest_last_and_excludes_itself(self) -> None:
        result = ArtifactPublicationService(self.repository, self.clock).publish(bundle())
        self.assertEqual("put_manifest", self.repository.calls[-1])
        self.assertTrue(all(request.deadline.budget_ms <= 5_000 for request in self.repository.requests))
        self.assertEqual("complete", result.manifest.publication_outcome)
        generation_entries = [
            item for item in result.execution_log.entries
            if item.artifact_generation is not None
        ]
        self.assertTrue(generation_entries)
        self.assertTrue(generation_entries[-1].artifact_generation["manifest_last"])
        self.assertEqual(6, len(result.manifest.available))
        self.assertEqual((), result.manifest.missing)
        self.assertNotIn("manifest", {item["artifact_type"] for item in result.manifest.available})
        listing = self.fake.list_for_execution(ArtifactExecutionRequestDTO("OP-LIST-001", "TASK-001", "EXEC-001", deadline("OP-LIST-001")))
        self.assertEqual(7, len(listing.items))
        for descriptor in listing.items:
            if descriptor.artifact_type == "manifest":
                continue
            stored = self.fake.get_manifest(ArtifactExecutionRequestDTO("OP-MANIFEST-GET", "TASK-001", "EXEC-001", deadline("OP-MANIFEST-GET")))
            entry = next(item for item in stored.available if item["artifact_id"] == descriptor.artifact_id)
            self.assertEqual(descriptor.sha256, entry["sha256"])
            self.assertEqual(descriptor.size_bytes, entry["size_bytes"])
        manifest_bytes = base64.b64decode(result.manifest_descriptor_content_base64)
        self.assertEqual(result.manifest_descriptor.sha256, "sha256:" + hashlib.sha256(manifest_bytes).hexdigest())
        self.assertEqual(result.manifest.to_wire(), json.loads(manifest_bytes))

    def test_same_payload_is_idempotent_and_changed_payload_conflicts(self) -> None:
        service = ArtifactPublicationService(self.repository, self.clock)
        first = service.publish(bundle())
        second = service.publish(bundle())
        self.assertEqual(first.manifest_descriptor, second.manifest_descriptor)
        with self.assertRaisesRegex(PublicationError, "artifact_conflict"):
            service.publish(bundle("Changed BTC status?"))

    def test_renderer_failures_publish_only_minimum_bundle_and_disclose_partial(self) -> None:
        def fail(_value):
            raise RuntimeError("vendor secret must not escape")

        renderers = PublicationRenderers(markdown=fail, html=fail, csv=fail)
        result = ArtifactPublicationService(self.repository, self.clock, renderers=renderers).publish(bundle())
        self.assertEqual("partial", result.manifest.publication_outcome)
        self.assertEqual(3, len(result.manifest.available))
        self.assertEqual(3, len(result.manifest.missing))
        self.assertEqual({"renderer_failed"}, {item["reason_code"] for item in result.manifest.missing})
        self.assertEqual({"markdown_report", "html_report", "csv_evidence"}, {item["artifact_type"] for item in result.manifest.missing})
        self.assertEqual(3, len(result.final_report.renderer_failures))
        log_text = result.execution_log.canonical_jsonl().decode("utf-8")
        self.assertIn("renderer_failed", log_text)
        self.assertNotIn("vendor secret", log_text)
        listing = self.fake.list_for_execution(ArtifactExecutionRequestDTO("OP-LIST-MIN", "TASK-001", "EXEC-001", deadline("OP-LIST-MIN")))
        self.assertEqual({"final_report", "evidence_list", "execution_log", "manifest"}, {item.artifact_type for item in listing.items})

    def test_required_artifact_conflict_never_publishes_manifest(self) -> None:
        content = b"{}"
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        seeded = self.fake.put(
            ArtifactPutRequestDTO(
                "OP-SEED-EVIDENCE", "TASK-001", "EXEC-001", "evidence_list", "json",
                "application/json", "1.0.0", digest, len(content), "2026-08-01T02:10:00Z",
                base64.b64encode(content).decode("ascii"), deadline("OP-SEED-EVIDENCE"),
            )
        )
        self.assertEqual("evidence_list", seeded.artifact_type)
        with self.assertRaisesRegex(PublicationError, "artifact_conflict"):
            ArtifactPublicationService(self.repository, self.clock).publish(bundle())
        manifest = self.fake.get_manifest(
            ArtifactExecutionRequestDTO("OP-NO-MANIFEST", "TASK-001", "EXEC-001", deadline("OP-NO-MANIFEST"))
        )
        self.assertEqual("artifact_not_found", manifest.error.code)

    def test_final_write_hard_boundary_stops_before_next_write_and_manifest(self) -> None:
        repository = AdvancingArtifactRepository(self.fake, self.clock)
        with self.assertRaisesRegex(PublicationError, "deadline_exceeded"):
            ArtifactPublicationService(repository, self.clock).publish(bundle())
        self.assertEqual(["put:final_report"], repository.calls)
        manifest = self.fake.get_manifest(
            ArtifactExecutionRequestDTO("OP-HARD-LIMIT-GET", "TASK-001", "EXEC-001", deadline("OP-HARD-LIMIT-GET"))
        )
        self.assertEqual("artifact_not_found", manifest.error.code)

    def test_blocking_required_put_times_out_once_and_late_result_cannot_publish_manifest(self) -> None:
        repository = BlockingArtifactRepository(self.fake, "put")
        request = replace(bundle(), deadline=short_deadline("OP-PUB-BLOCK-PUT"))
        with self.assertRaisesRegex(PublicationError, "deadline_exceeded"):
            ArtifactPublicationService(repository, self.clock).publish(request)
        self.assertTrue(repository.entered.is_set())
        self.assertEqual(1, repository.put_attempts)
        self.assertEqual(0, repository.manifest_attempts)
        repository.release.set()
        self.assertTrue(repository.late_finished.wait(1))
        self.assertEqual(0, repository.manifest_attempts)
        manifest = self.fake.get_manifest(
            ArtifactExecutionRequestDTO("OP-BLOCK-PUT-GET", "TASK-001", "EXEC-001", deadline("OP-BLOCK-PUT-GET"))
        )
        self.assertEqual("artifact_not_found", manifest.error.code)

    def test_blocking_put_manifest_times_out_once_without_late_manifest_write(self) -> None:
        repository = BlockingArtifactRepository(self.fake, "put_manifest")
        request = replace(bundle(), deadline=short_deadline("OP-PUB-BLOCK-MANIFEST"))
        with self.assertRaisesRegex(PublicationError, "deadline_exceeded"):
            ArtifactPublicationService(repository, self.clock).publish(request)
        self.assertTrue(repository.entered.is_set())
        self.assertEqual(1, repository.manifest_attempts)
        self.assertEqual("put_manifest", repository.calls[-1])
        repository.release.set()
        self.assertTrue(repository.late_finished.wait(1))
        self.assertEqual(1, repository.manifest_attempts)
        manifest = self.fake.get_manifest(
            ArtifactExecutionRequestDTO("OP-BLOCK-MANIFEST-GET", "TASK-001", "EXEC-001", deadline("OP-BLOCK-MANIFEST-GET"))
        )
        self.assertEqual("artifact_not_found", manifest.error.code)

    def test_clock_exception_is_safe_and_repository_is_not_called(self) -> None:
        with self.assertRaises(PublicationError) as raised:
            ArtifactPublicationService(self.repository, ExplodingClock()).publish(bundle())
        self.assertIn("unexpected_provider_error", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception).lower())
        self.assertEqual([], self.repository.calls)

    def test_default_renderers_are_explicit_and_canonical(self) -> None:
        renderers = PublicationRenderers(render_markdown, render_html, render_evidence_csv)
        result = ArtifactPublicationService(self.repository, self.clock, renderers=renderers).publish(bundle())
        self.assertEqual("complete", result.manifest.publication_outcome)


if __name__ == "__main__":
    unittest.main()
