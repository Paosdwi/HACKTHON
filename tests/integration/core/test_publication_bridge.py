"""T64 Formal Run typed stage-output and artifact-publication bridge tests."""

from __future__ import annotations

import base64
import json
import sys
import unittest
from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.repositories import (
    ArtifactContentDTO,
    ArtifactDescriptorDTO,
    ArtifactExecutionRequestDTO,
    ArtifactKeyRequestDTO,
    ArtifactListDTO,
    ArtifactManifestDTO,
    ArtifactPutRequestDTO,
    EvidenceAssessmentDTO,
    EvidenceClaimLinkDTO,
    EvidenceDTO,
    ExecutionEventDTO,
    ExecutionRecordDTO,
    PutManifestRequestDTO,
    TaskRecordDTO,
)
from crypto_trust_agent.application.orchestration.artifact_assembler import (
    AssemblerValidationError,
    FormalRunArtifactAssembler,
)
from crypto_trust_agent.application.orchestration.formal_run import (
    FormalRunCommand,
    FormalRunOrchestrator,
    FormalRunPublicationSummaryDTO,
    FormalRunStep,
    FormalRunStepRequest,
    FormalRunStepResult,
    StepOutcome,
    TerminalOutcome,
)
from crypto_trust_agent.application.orchestration.pipeline_context import (
    FormalRunPipelineContext,
    PipelineDuplicateError,
    PipelineOverflowError,
    PipelineSnapshot,
    MAX_EVIDENCE,
)
from crypto_trust_agent.application.orchestration.stage_contributions import (
    AnalysisContributionDTO,
    AssessmentContributionDTO,
    CollectionContributionDTO,
    EventContributionDTO,
    EvidenceContributionDTO,
    MAX_CONTRIBUTIONS_PER_STEP,
    PipelineContributionDTO,
    PipelineContributionKind,
    ReasoningContributionDTO,
    StrategyContributionDTO,
)
from crypto_trust_agent.application.planning import QuestionType, build_plan
from crypto_trust_agent.application.publication import (
    ArtifactPublicationRequest,
    ArtifactPublicationResult,
    ArtifactPublicationService,
    PublicationError,
)
from crypto_trust_agent.domain.fingerprint import create_request_fingerprint
from crypto_trust_agent.infrastructure.fakes import (
    FakeArtifactRepository,
    FakeClock,
    FakeEventPublisher,
    FakeExecutionRepository,
    FakeFormalRunStepExecutor,
    FakePlatformStore,
)

HASH = "sha256:" + "b" * 64


class _RecordingContextFactory:
    def __init__(self) -> None:
        self.contexts: list[FormalRunPipelineContext] = []

    def __call__(
        self,
        *,
        task_id: str,
        execution_id: str,
        question: str,
        assets: tuple[str, ...],
    ) -> FormalRunPipelineContext:
        context = FormalRunPipelineContext(
            task_id=task_id,
            execution_id=execution_id,
            question=question,
            assets=assets,
        )
        self.contexts.append(context)
        return context


class _RecordingStepExecutor:
    """Protocol-compatible recorder; it owns no pipeline context."""

    def __init__(self, inner: FakeFormalRunStepExecutor) -> None:
        self._inner = inner
        self.results: list[tuple[FormalRunStepRequest, FormalRunStepResult]] = []

    def execute(self, request: FormalRunStepRequest) -> FormalRunStepResult:
        result = self._inner.execute(request)
        self.results.append((request, result))
        return result

    def configure(self, *args, **kwargs) -> None:
        self._inner.configure(*args, **kwargs)

    @property
    def calls(self) -> tuple[FormalRunStep, ...]:
        return self._inner.calls

    @property
    def requests(self) -> tuple[FormalRunStepRequest, ...]:
        return self._inner.requests

    def call_count(self, step: FormalRunStep) -> int:
        return self._inner.call_count(step)


class _UnknownVersionStepExecutor(_RecordingStepExecutor):
    """Inject an invalid wire version after constructor validation."""

    def execute(self, request: FormalRunStepRequest) -> FormalRunStepResult:
        result = self._inner.execute(request)
        if request.step is FormalRunStep.INITIALIZATION and result.contributions:
            original = result.contributions[0]
            tampered = object.__new__(PipelineContributionDTO)
            object.__setattr__(tampered, "kind", original.kind)
            object.__setattr__(tampered, "payload", original.payload)
            object.__setattr__(tampered, "schema_version", "9.9.9")
            result = replace(result, contributions=(tampered,))
        self.results.append((request, result))
        return result


class _RecordingArtifactRepository:
    def __init__(self, inner: FakeArtifactRepository) -> None:
        self._inner = inner
        self.write_order: list[tuple[str, str]] = []

    def put(self, request: ArtifactPutRequestDTO) -> ArtifactDescriptorDTO | ErrorResultDTO:
        self.write_order.append(("put", request.artifact_type))
        return self._inner.put(request)

    def put_manifest(
        self, request: PutManifestRequestDTO
    ) -> ArtifactDescriptorDTO | ErrorResultDTO:
        self.write_order.append(("put_manifest", "manifest"))
        return self._inner.put_manifest(request)

    def get(self, request: ArtifactKeyRequestDTO) -> ArtifactContentDTO | ErrorResultDTO:
        return self._inner.get(request)

    def list_for_execution(self, request: ArtifactExecutionRequestDTO) -> ArtifactListDTO:
        return self._inner.list_for_execution(request)

    def get_manifest(
        self, request: ArtifactExecutionRequestDTO
    ) -> ArtifactManifestDTO | ErrorResultDTO:
        return self._inner.get_manifest(request)


class _RecordingPublicationService:
    def __init__(self, inner: ArtifactPublicationService) -> None:
        self._inner = inner
        self.requests: list[ArtifactPublicationRequest] = []

    def publish(self, request: ArtifactPublicationRequest) -> ArtifactPublicationResult:
        self.requests.append(request)
        return self._inner.publish(request)


class _FailingPublicationService:
    def __init__(self) -> None:
        self.requests: list[ArtifactPublicationRequest] = []

    def publish(self, request: ArtifactPublicationRequest) -> ArtifactPublicationResult:
        self.requests.append(request)
        raise PublicationError("artifact_store_unavailable")


@dataclass(slots=True)
class _BridgeFixture:
    clock: FakeClock
    store: FakePlatformStore
    steps: _RecordingStepExecutor
    repository: _RecordingArtifactRepository
    publication: _RecordingPublicationService | _FailingPublicationService
    context_factory: _RecordingContextFactory
    orchestrator: FormalRunOrchestrator
    command: FormalRunCommand


def _build_bridge_fixture(
    *,
    question: str = "Analyze BTC market status",
    assets: tuple[str, ...] = ("BTC",),
    question_type: QuestionType = QuestionType.MARKET_STATUS,
    populate_pipeline: bool = True,
    fail_publication: bool = False,
    unknown_contribution_version: bool = False,
) -> _BridgeFixture:
    clock = FakeClock("2026-08-01T02:00:00Z", runtime_id="t64-bridge-runtime")
    store = FakePlatformStore()
    task_id = "TASK-T64-001"
    execution_id = "EXEC-T64-001"
    record = ExecutionRecordDTO(
        execution_id,
        task_id,
        HASH,
        1,
        "user_initial",
        None,
        None,
        "created",
        "in_progress",
        "2026-08-01T02:15:00Z",
        "2026-08-01T02:00:00Z",
        "2026-08-01T02:00:00Z",
        None,
        (),
        None,
        1,
    )
    store.tasks[task_id] = TaskRecordDTO(
        task_id,
        "subject-t64",
        HASH,
        "execution_locked",
        3,
        "2026-08-01T02:00:00Z",
        execution_id,
    )
    store.executions[execution_id] = record
    fingerprint = create_request_fingerprint(
        question=question,
        assets=assets,
        timeframe_start="2026-07-01T00:00:00Z",
        timeframe_end="2026-08-01T00:00:00Z",
    )
    plan = build_plan(
        question_type=question_type,
        fingerprint=fingerprint,
        clock_snapshot="2026-08-01T02:00:00Z",
    )
    fake_steps = FakeFormalRunStepExecutor(
        clock, populate_pipeline=populate_pipeline
    )
    steps = (
        _UnknownVersionStepExecutor(fake_steps)
        if unknown_contribution_version
        else _RecordingStepExecutor(fake_steps)
    )
    repository = _RecordingArtifactRepository(FakeArtifactRepository(clock))
    publication: _RecordingPublicationService | _FailingPublicationService
    if fail_publication:
        publication = _FailingPublicationService()
    else:
        publication = _RecordingPublicationService(
            ArtifactPublicationService(repository, clock)
        )
    context_factory = _RecordingContextFactory()
    orchestrator = FormalRunOrchestrator(
        clock,
        steps,
        FakeEventPublisher(clock),
        FakeExecutionRepository(store, clock),
        artifact_assembler=FormalRunArtifactAssembler(),
        artifact_publication=publication,
        pipeline_context_factory=context_factory,
    )
    command = FormalRunCommand(
        "OP-T64-RUN-001",
        record,
        plan,
        question=question,
    )
    return _BridgeFixture(
        clock,
        store,
        steps,
        repository,
        publication,
        context_factory,
        orchestrator,
        command,
    )


def _deadline(clock: FakeClock, operation_id: str) -> DeadlineDTO:
    now = clock.current_utc()
    future = now.as_datetime() + timedelta(minutes=5)
    return DeadlineDTO(
        "1.0.0",
        operation_id,
        future.isoformat().replace("+00:00", "Z"),
        5_000,
        now,
        100,
    )


def _artifact_json(fixture: _BridgeFixture, artifact_type: str, format_: str) -> object:
    response = fixture.repository.get(
        ArtifactKeyRequestDTO(
            "OP-T64-READ",
            "TASK-T64-001",
            "EXEC-T64-001",
            artifact_type,
            format_,
            "1.0.0",
            _deadline(fixture.clock, "OP-T64-READ"),
        )
    )
    if not isinstance(response, ArtifactContentDTO):
        raise AssertionError("artifact read failed")
    raw = base64.b64decode(response.delivery["content_base64"])
    return json.loads(raw) if format_ == "json" else raw.decode("utf-8")


class PublicationBridgeSuccessTests(unittest.TestCase):
    def test_successful_run_publishes_minimum_bundle_and_typed_summary(self) -> None:
        fixture = _build_bridge_fixture()
        result = fixture.orchestrator.execute(fixture.command)

        self.assertEqual(TerminalOutcome.SUCCESS, result.terminal_outcome)
        self.assertTrue(result.publication_attempted)
        self.assertTrue(result.artifact_publication_completed)
        self.assertEqual("complete", result.publication_outcome)
        self.assertIsInstance(result.publication, FormalRunPublicationSummaryDTO)
        self.assertLessEqual(len(result.publication.available), 7)
        self.assertTrue(
            all(isinstance(item, ArtifactDescriptorDTO) for item in result.publication.available)
        )
        self.assertTrue(result.manifest_sha256.startswith("sha256:"))

        listed = fixture.repository.list_for_execution(
            ArtifactExecutionRequestDTO(
                "OP-T64-LIST",
                "TASK-T64-001",
                "EXEC-T64-001",
                _deadline(fixture.clock, "OP-T64-LIST"),
            )
        )
        self.assertTrue(
            {"final_report", "evidence_list", "execution_log", "manifest"}
            <= {item.artifact_type for item in listed.items}
        )

    def test_question_assets_and_identity_are_consistent(self) -> None:
        fixture = _build_bridge_fixture()
        fixture.orchestrator.execute(fixture.command)
        report = _artifact_json(fixture, "final_report", "json")
        evidence = _artifact_json(fixture, "evidence_list", "json")
        self.assertEqual("TASK-T64-001", report["task_id"])
        self.assertEqual("EXEC-T64-001", report["execution_id"])
        self.assertEqual("Analyze BTC market status", report["question"])
        self.assertEqual(["BTC"], report["assets"])
        self.assertEqual("TASK-T64-001", evidence["task_id"])
        self.assertEqual("EXEC-T64-001", evidence["execution_id"])

    def test_different_question_produces_different_final_report_hash(self) -> None:
        first = _build_bridge_fixture(question="Analyze BTC market status")
        second = _build_bridge_fixture(
            question="Analyze ETH volatility trends", assets=("ETH",)
        )
        first_result = first.orchestrator.execute(first.command)
        second_result = second.orchestrator.execute(second.command)
        first_hash = next(
            item.sha256
            for item in first_result.publication.available
            if item.artifact_type == "final_report" and item.format == "json"
        )
        second_hash = next(
            item.sha256
            for item in second_result.publication.available
            if item.artifact_type == "final_report" and item.format == "json"
        )
        self.assertNotEqual(first_hash, second_hash)


class TypedStageContributionTests(unittest.TestCase):
    def test_stage_ownership_has_no_duplicate_evidence_or_fake_context_property(self) -> None:
        fixture = _build_bridge_fixture()
        result = fixture.orchestrator.execute(fixture.command)
        self.assertEqual(TerminalOutcome.SUCCESS, result.terminal_outcome)
        self.assertFalse(hasattr(fixture.steps, "pipeline_context"))
        self.assertFalse(hasattr(fixture.steps, "bind_pipeline_context"))
        self.assertEqual(1, len(fixture.context_factory.contexts))

        evidence_ids: list[str] = []
        for request, step_result in fixture.steps.results:
            self.assertIsInstance(request.pipeline_snapshot, PipelineSnapshot)
            with self.assertRaises(FrozenInstanceError):
                request.pipeline_snapshot.question = "mutated"
            self.assertLessEqual(
                len(step_result.contributions), MAX_CONTRIBUTIONS_PER_STEP
            )
            kinds = {item.kind for item in step_result.contributions}
            if request.step is FormalRunStep.COLLECTION:
                self.assertNotIn(PipelineContributionKind.EVIDENCE, kinds)
                self.assertTrue(
                    kinds <= {
                        PipelineContributionKind.COLLECTION,
                        PipelineContributionKind.EVENT,
                    }
                )
            if request.step is FormalRunStep.EXTRACTION:
                self.assertTrue(
                    kinds <= {
                        PipelineContributionKind.EVIDENCE,
                        PipelineContributionKind.EVENT,
                    }
                )
                for contribution in step_result.contributions:
                    if contribution.kind is PipelineContributionKind.EVIDENCE:
                        evidence_ids.extend(
                            item.evidence_id for item in contribution.payload.evidence
                        )
                        self.assertIsInstance(contribution.to_wire(), dict)
        self.assertTrue(evidence_ids)
        self.assertEqual(len(evidence_ids), len(set(evidence_ids)))

    def _canonical_contributions(self) -> tuple[PipelineContributionDTO, ...]:
        fixture = _build_bridge_fixture()
        result = fixture.orchestrator.execute(fixture.command)
        self.assertEqual(TerminalOutcome.SUCCESS, result.terminal_outcome)
        return tuple(
            contribution
            for _, step_result in fixture.steps.results
            for contribution in step_result.contributions
        )

    def test_each_payload_dto_rejects_unknown_schema_version(self) -> None:
        contributions = self._canonical_contributions()
        payload_by_type = {type(item.payload): item.payload for item in contributions}
        expected_types = (
            CollectionContributionDTO,
            EvidenceContributionDTO,
            AssessmentContributionDTO,
            AnalysisContributionDTO,
            StrategyContributionDTO,
            ReasoningContributionDTO,
            EventContributionDTO,
        )
        self.assertEqual(set(expected_types), set(payload_by_type))
        for payload_type in expected_types:
            with self.subTest(payload_type=payload_type.__name__):
                with self.assertRaises(ValueError):
                    replace(payload_by_type[payload_type], schema_version="9.9.9")

    def test_outer_contribution_rejects_unknown_schema_version(self) -> None:
        contribution = self._canonical_contributions()[0]
        with self.assertRaises(ValueError):
            replace(contribution, schema_version="9.9.9")

    def test_canonical_version_to_wire_shape_is_unchanged(self) -> None:
        expected_payload_keys = {
            CollectionContributionDTO: {
                "schema_version", "task_id", "execution_id", "raw_record_ids"
            },
            EvidenceContributionDTO: {
                "schema_version", "task_id", "execution_id", "evidence", "claim_links"
            },
            AssessmentContributionDTO: {
                "schema_version", "task_id", "execution_id", "assessments"
            },
            AnalysisContributionDTO: {
                "schema_version", "task_id", "execution_id", "analysis_refs"
            },
            StrategyContributionDTO: {
                "schema_version", "task_id", "execution_id", "contradictions", "limitations"
            },
            ReasoningContributionDTO: {
                "schema_version", "task_id", "execution_id", "reasoning_result"
            },
            EventContributionDTO: {
                "schema_version", "task_id", "execution_id", "events"
            },
        }
        seen: set[type[object]] = set()
        for contribution in self._canonical_contributions():
            wire = contribution.to_wire()
            self.assertEqual(
                {"schema_version", "kind", "payload"}, set(wire)
            )
            self.assertEqual("1.0.0", wire["schema_version"])
            self.assertEqual(contribution.kind.value, wire["kind"])
            payload_wire = wire["payload"]
            payload_type = type(contribution.payload)
            self.assertEqual(expected_payload_keys[payload_type], set(payload_wire))
            self.assertEqual("1.0.0", payload_wire["schema_version"])
            seen.add(payload_type)
        self.assertEqual(set(expected_payload_keys), seen)

    def test_unknown_contribution_version_fails_closed_before_publication(self) -> None:
        fixture = _build_bridge_fixture(unknown_contribution_version=True)
        result = fixture.orchestrator.execute(fixture.command)
        self.assertEqual(TerminalOutcome.FAILED, result.terminal_outcome)
        self.assertEqual(
            "stage_contribution_invalid",
            result.step(FormalRunStep.INITIALIZATION).safe_reason_code,
        )
        self.assertFalse(result.publication_attempted)
        self.assertEqual(0, len(fixture.publication.requests))
        self.assertEqual([], fixture.repository.write_order)


class CitationAndLineageTests(unittest.TestCase):
    def test_conclusions_are_traceable_to_evidence(self) -> None:
        fixture = _build_bridge_fixture()
        fixture.orchestrator.execute(fixture.command)
        report = _artifact_json(fixture, "final_report", "json")
        evidence = _artifact_json(fixture, "evidence_list", "json")
        evidence_ids = {item["evidence_id"] for item in evidence["items"]}
        fact_ids = {item["fact_id"] for item in report["facts"]}
        inference_ids = {item["inference_id"] for item in report["inferences"]}
        for fact in report["facts"]:
            self.assertTrue(fact["evidence_refs"] or fact["analysis_refs"])
            self.assertTrue(set(fact["evidence_refs"]) <= evidence_ids)
        for conclusion in report["conclusions"]:
            self.assertTrue(set(conclusion["fact_refs"]) <= fact_ids)
            self.assertTrue(set(conclusion["inference_refs"]) <= inference_ids)

    def test_evidence_has_complete_lineage(self) -> None:
        fixture = _build_bridge_fixture()
        fixture.orchestrator.execute(fixture.command)
        evidence = _artifact_json(fixture, "evidence_list", "json")
        for item in evidence["items"]:
            self.assertTrue(item["source_url"].startswith("https://"))
            self.assertTrue(item["fetched_at"])
            self.assertTrue(item["content_hash"].startswith("sha256:"))
            self.assertTrue(item["assessment_id"].startswith("ASSESS-"))
            lineage = item["lineage"]
            self.assertEqual("TASK-T64-001", lineage["task_id"])
            self.assertEqual("EXEC-T64-001", lineage["execution_id"])
            self.assertTrue(lineage["raw_record_id"].startswith("RAW-"))
            self.assertTrue(lineage["raw_content_hash"].startswith("sha256:"))
            self.assertTrue(lineage["clean_content_hash"].startswith("sha256:"))
            self.assertTrue(lineage["query_provenance"])


class PublicationBoundaryTests(unittest.TestCase):
    def test_publish_is_called_exactly_once_with_valid_bounded_deadline(self) -> None:
        fixture = _build_bridge_fixture()
        result = fixture.orchestrator.execute(fixture.command)
        self.assertTrue(result.publication_attempted)
        self.assertEqual(1, len(fixture.publication.requests))
        request = fixture.publication.requests[0]
        deadline = request.deadline
        delta_ms = (
            deadline.deadline_at_utc.as_datetime()
            - deadline.sent_at_utc.as_datetime()
        ) // timedelta(milliseconds=1)
        self.assertGreater(delta_ms, deadline.safety_margin_ms)
        self.assertLessEqual(delta_ms, 25_000)
        self.assertEqual(delta_ms, deadline.budget_ms)

    def test_manifest_write_is_last(self) -> None:
        fixture = _build_bridge_fixture()
        fixture.orchestrator.execute(fixture.command)
        self.assertTrue(fixture.repository.write_order)
        self.assertEqual(("put_manifest", "manifest"), fixture.repository.write_order[-1])
        self.assertEqual(1, fixture.repository.write_order.count(("put_manifest", "manifest")))

    def test_assembler_failure_is_not_counted_as_publish_attempt(self) -> None:
        fixture = _build_bridge_fixture(populate_pipeline=False)
        result = fixture.orchestrator.execute(fixture.command)
        self.assertEqual(TerminalOutcome.FAILED, result.terminal_outcome)
        self.assertFalse(result.publication_attempted)
        self.assertEqual("failed", result.publication_outcome)
        self.assertEqual(("assembler_invalid",), result.missing_reason_codes)
        self.assertEqual(0, len(fixture.publication.requests))

    def test_repository_failure_is_counted_as_one_publish_attempt(self) -> None:
        fixture = _build_bridge_fixture(fail_publication=True)
        result = fixture.orchestrator.execute(fixture.command)
        self.assertEqual(TerminalOutcome.FAILED, result.terminal_outcome)
        self.assertTrue(result.publication_attempted)
        self.assertEqual("failed", result.publication_outcome)
        self.assertEqual(("artifact_repository_failure",), result.missing_reason_codes)
        self.assertEqual(1, len(fixture.publication.requests))

    def test_reasoning_failure_prevents_publication(self) -> None:
        fixture = _build_bridge_fixture()
        fixture.steps.configure(
            FormalRunStep.REASONING_BOUNDARY,
            outcome=StepOutcome.FAILURE,
            safe_reason_code="no_verified_evidence",
        )
        result = fixture.orchestrator.execute(fixture.command)
        self.assertEqual(TerminalOutcome.FAILED, result.terminal_outcome)
        self.assertFalse(result.publication_attempted)
        self.assertEqual(0, len(fixture.publication.requests))


class BoundedAndFailClosedTests(unittest.TestCase):
    @staticmethod
    def _evidence(index: int) -> EvidenceDTO:
        return EvidenceDTO(
            evidence_id=f"EVID-{index:04d}",
            task_id="TASK-001",
            execution_id="EXEC-001",
            raw_record_id=f"RAW-{index:04d}",
            source_name="test",
            source_type="dataset",
            source_url="https://example.com/data",
            published_at=None,
            fetched_at="2026-08-01T02:00:00Z",
            content_reference={"kind": "quote", "value": "test"},
            raw_locator=f"urn:test:raw:{index}",
            raw_content_hash="sha256:" + "a" * 64,
            clean_content_hash="sha256:" + "b" * 64,
            query_provenance={"query": "test"},
            validation_status="active",
            created_at="2026-08-01T02:00:00Z",
        )

    def test_context_rejects_duplicate_and_overflow_evidence(self) -> None:
        context = FormalRunPipelineContext(
            task_id="TASK-001",
            execution_id="EXEC-001",
            question="test",
            assets=("BTC",),
        )
        first = self._evidence(0)
        context.append_evidence(first)
        with self.assertRaises(PipelineDuplicateError):
            context.append_evidence(first)
        for index in range(1, MAX_EVIDENCE):
            context.append_evidence(self._evidence(index))
        with self.assertRaises(PipelineOverflowError):
            context.append_evidence(self._evidence(MAX_EVIDENCE))

    def test_assembler_rejects_foreign_evidence_identity(self) -> None:
        context = FormalRunPipelineContext(
            task_id="TASK-001",
            execution_id="EXEC-001",
            question="test",
            assets=("BTC",),
        )
        context.append_evidence(
            EvidenceDTO(
                evidence_id="EVID-FOREIGN",
                task_id="TASK-OTHER",
                execution_id="EXEC-001",
                raw_record_id="RAW-001",
                source_name="test",
                source_type="news",
                source_url="https://example.com/x",
                published_at=None,
                fetched_at="2026-08-01T02:00:00Z",
                content_reference={"kind": "quote", "value": "test"},
                raw_locator="https://example.com/raw",
                raw_content_hash="sha256:" + "a" * 64,
                clean_content_hash="sha256:" + "b" * 64,
                query_provenance={"query": "test"},
                validation_status="active",
                created_at="2026-08-01T02:00:00Z",
            )
        )
        context.append_claim_links(
            (
                EvidenceClaimLinkDTO(
                    "LINK-001",
                    "TASK-001",
                    "EVID-FOREIGN",
                    "CLAIM-001",
                    "supports",
                    "2026-08-01T02:00:00Z",
                ),
            )
        )
        context.append_assessments(
            (
                EvidenceAssessmentDTO(
                    "ASSESS-001",
                    "TASK-001",
                    "EVID-FOREIGN",
                    1,
                    "1.0.0",
                    "r-1.0.0",
                    "0.8",
                    "0.8",
                    "0.8",
                    "0.8",
                    "g1",
                    "0.8",
                    "0.8",
                    "low",
                    "2026-08-01T02:00:00Z",
                    (),
                ),
            )
        )
        context.append_event(
            ExecutionEventDTO(
                "EVT-001",
                "2026-08-01T02:00:00Z",
                "TASK-001",
                "EXEC-001",
                "collection",
                "fake",
                "completed",
                0,
                0,
                {},
                {},
                None,
                5_000,
                {"operation_id": "OP-1", "causation_event_id": None},
            )
        )
        with self.assertRaises(AssemblerValidationError) as error:
            FormalRunArtifactAssembler().assemble(
                context.snapshot(),
                generated_at="2026-08-01T02:10:00Z",
                deadline=DeadlineDTO(
                    "1.0.0",
                    "OP-TEST",
                    "2026-08-01T03:00:00Z",
                    5_000,
                    "2026-08-01T02:00:00Z",
                    100,
                ),
            )
        self.assertIn("task_id mismatch", error.exception.reason)


class SecretRedactionTests(unittest.TestCase):
    def test_artifacts_do_not_contain_sensitive_payloads(self) -> None:
        fixture = _build_bridge_fixture()
        fixture.orchestrator.execute(fixture.command)
        for artifact_type, format_ in (
            ("final_report", "json"),
            ("evidence_list", "json"),
            ("execution_log", "jsonl"),
        ):
            payload = _artifact_json(fixture, artifact_type, format_)
            text = (
                json.dumps(payload, separators=(",", ":"))
                if not isinstance(payload, str)
                else payload
            ).lower()
            for forbidden in (
                '"secret":',
                '"jwt":',
                '"authorization":',
                '"password":',
                '"prompt":',
                '"raw_content":',
                "bearer ",
            ):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
