from __future__ import annotations

import base64
import hashlib
import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.common import DeadlineDTO  # noqa: E402
from crypto_trust_agent.application.dto.repositories import (  # noqa: E402
    AcquireExecutionRequestDTO,
    AdminAuthorizationDTO,
    AppendAssessmentsRequestDTO,
    AppendClaimLinksRequestDTO,
    AppendEvidenceRequestDTO,
    AppendPreflightResultRequestDTO,
    ArtifactExecutionRequestDTO,
    ArtifactKeyRequestDTO,
    ArtifactManifestDTO,
    ArtifactPutRequestDTO,
    ClockReadRequestDTO,
    ConsumePreflightSlotRequestDTO,
    ConsumeTaskCreateSlotRequestDTO,
    CreateOrGetTaskRequestDTO,
    EvidenceAssessmentDTO,
    EvidenceClaimLinkDTO,
    EvidenceDTO,
    ExecutionEventDTO,
    GetEvidenceRequestDTO,
    GetExecutionRequestDTO,
    GetLatestAssessmentsRequestDTO,
    GetLatestPreflightRequestDTO,
    ListByQuotaScopeRequestDTO,
    ListEvidenceRequestDTO,
    PreflightRecordDTO,
    PublishBatchRequestDTO,
    PublishEventRequestDTO,
    PutManifestRequestDTO,
    RecordManualCaseRequestDTO,
    TaskQueryRequestDTO,
    TransitionExecutionRequestDTO,
)
from crypto_trust_agent.application.ports import (  # noqa: E402
    ArtifactRepository,
    Clock,
    EventPublisher,
    EvidenceRepository,
    ExecutionRepository,
    TaskRepository,
)
from crypto_trust_agent.infrastructure.fakes import (  # noqa: E402
    FakeArtifactRepository,
    FakeClock,
    FakeEventPublisher,
    FakeEvidenceRepository,
    FakeExecutionRepository,
    FakePlatformStore,
    FakeTaskRepository,
)


HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64


def deadline(
    operation_id: str,
    *,
    budget_ms: int = 5_000,
    deadline_at_utc: str = "2026-08-01T03:00:00Z",
    sent_at_utc: str = "2026-08-01T02:00:00Z",
) -> DeadlineDTO:
    return DeadlineDTO(
        schema_version="1.0.0",
        operation_id=operation_id,
        deadline_at_utc=deadline_at_utc,
        budget_ms=budget_ms,
        sent_at_utc=sent_at_utc,
        safety_margin_ms=100,
    )


def task_request(operation_id: str, task_id: str = "TASK-001") -> CreateOrGetTaskRequestDTO:
    return CreateOrGetTaskRequestDTO(
        operation_id=operation_id,
        trusted_user_scope="subject-1",
        principal_subject_hash="hmac-pseudonym-001",
        request_fingerprint=HASH_A,
        idempotency_window_started_at="2026-08-01T02:00:00Z",
        proposed_task={
            "task_id": task_id,
            "question": "Analyse BTC",
            "assets_requested_order": ("BTC",),
            "assets_canonical": ("BTC",),
            "timeframe": {"start": "2026-07-18T00:00:00Z", "end": "2026-08-01T00:00:00Z"},
            "question_type": "market_status",
            "formal_run_intent": True,
            "sourcing_plan": {"plan_id": "PLAN-S-001", "ruleset_version": "planner-1.0.0", "canonical_hash": HASH_B},
            "analysis_plan": {"plan_id": "PLAN-A-001", "ruleset_version": "planner-1.0.0", "canonical_hash": HASH_C},
            "created_at": "2026-08-01T02:00:00Z",
        },
        deadline=deadline(operation_id),
    )


def preflight_request(operation_id: str = "OP-PF-001") -> AppendPreflightResultRequestDTO:
    return AppendPreflightResultRequestDTO(
        operation_id=operation_id,
        expected_task_version=1,
        record=PreflightRecordDTO(
            preflight_id="PF-001",
            task_id="TASK-001",
            task_version=1,
            input_lock_hash=HASH_B,
            dependency_snapshot_hash=HASH_C,
            checked_at="2026-08-01T02:00:00Z",
            expires_at="2026-08-01T02:01:00Z",
            ready=True,
            checks=({"name": "official_dataset", "required": True, "status": "healthy", "safe_reason_code": None},),
            dependency_snapshot={"items": ({"name": "official_dataset", "capability_version": "dataset-1.0.0", "status": "healthy", "checked_at": "2026-08-01T02:00:00Z"},)},
        ),
        deadline=deadline(operation_id),
    )


def acquire_request(operation_id: str, execution_id: str) -> AcquireExecutionRequestDTO:
    return AcquireExecutionRequestDTO(
        operation_id=operation_id,
        trusted_user_scope="subject-1",
        request_fingerprint=HASH_A,
        task_id="TASK-001",
        expected_task_version=2,
        input_lock_hash=HASH_B,
        dependency_snapshot_hash=HASH_C,
        execution_id=execution_id,
        preflight_id="PF-001",
        attempt_kind="user_initial",
        started_at="2026-08-01T02:00:30Z",
        absolute_deadline_at="2026-08-01T02:15:30Z",
        deadline=deadline(operation_id),
    )


def evidence(evidence_id: str = "EVID-001") -> EvidenceDTO:
    return EvidenceDTO(
        evidence_id=evidence_id,
        task_id="TASK-001",
        execution_id="EXEC-001",
        raw_record_id="RAW-001",
        source_name="Example",
        source_type="news",
        source_url="https://example.com/article",
        published_at=None,
        fetched_at="2026-08-01T02:05:00Z",
        content_reference={"kind": "quote", "value": "bounded quote", "offset": {"start": 0, "end": 13}, "unit": "unicode_scalar"},
        raw_locator="urn:cryptotrust:raw:RAW-001",
        raw_content_hash=HASH_A,
        clean_content_hash=HASH_B,
        query_provenance={"collector": "official_news", "query": "BTC news", "parameters": {"asset": "BTC"}, "plan_job_id": "JOB-001"},
        validation_status="active",
        created_at="2026-08-01T02:06:00Z",
    )


def assessment(sequence: int, assessment_id: str) -> EvidenceAssessmentDTO:
    return EvidenceAssessmentDTO(
        assessment_id=assessment_id,
        task_id="TASK-001",
        evidence_id="EVID-001",
        assessment_sequence=sequence,
        assessment_version="1.0.0",
        ruleset_version="trust-1.0.0",
        source_trust="0.8",
        relevance="1",
        freshness="0.9",
        independence="0.7",
        independence_group="official_news:example",
        consistency="0.75",
        overall_confidence="0.82",
        contradiction_severity="none",
        computed_at="2026-08-01T02:08:00Z",
        limitations=(),
    )


class PortAndClockContractTests(unittest.TestCase):
    def test_fake_adapters_implement_application_owned_ports(self) -> None:
        clock = FakeClock("2026-08-01T02:00:00Z", monotonic_ms=100)
        store = FakePlatformStore()
        adapters = (
            (FakeTaskRepository(store, clock), TaskRepository),
            (FakeExecutionRepository(store, clock), ExecutionRepository),
            (FakeEvidenceRepository(clock), EvidenceRepository),
            (FakeArtifactRepository(clock), ArtifactRepository),
            (FakeEventPublisher(clock), EventPublisher),
            (clock, Clock),
        )
        for adapter, port in adapters:
            with self.subTest(port=port.__name__):
                self.assertIsInstance(adapter, port)
        self.assertFalse(hasattr(adapters[0][0], "lock_for_execution"))

    def test_clock_has_independent_controllable_wall_and_monotonic_time(self) -> None:
        clock = FakeClock("2026-08-01T02:00:00Z", monotonic_ms=100)
        wall = clock.now_utc(ClockReadRequestDTO("OP-CLOCK-01"))
        mono = clock.monotonic_ms(ClockReadRequestDTO("OP-CLOCK-02"))
        clock.advance(wall_seconds=2, monotonic_ms=7)
        self.assertEqual("2026-08-01T02:00:02Z", str(clock.now_utc(ClockReadRequestDTO("OP-CLOCK-03")).utc))
        self.assertEqual(107, clock.monotonic_ms(ClockReadRequestDTO("OP-CLOCK-04")).monotonic_ms)
        self.assertEqual(mono.runtime_id, clock.runtime_id)

    def test_dtos_are_frozen_and_request_wire_contains_exact_deadline(self) -> None:
        dto = ClockReadRequestDTO("OP-CLOCK-FROZEN")
        with self.assertRaises(FrozenInstanceError):
            dto.operation_id = "OP-OTHER"  # type: ignore[misc]
        request = task_request("OP-WIRE-001")
        wire = request.to_wire()
        self.assertEqual("OP-WIRE-001", wire["deadline"]["operation_id"])
        self.assertNotIn("monotonic_ms", repr(wire))
        with self.assertRaises(ValueError):
            CreateOrGetTaskRequestDTO(
                operation_id="OP-WIRE-BAD",
                trusted_user_scope="subject-1",
                principal_subject_hash="hmac-pseudonym-001",
                request_fingerprint=HASH_A,
                idempotency_window_started_at="2026-08-01T02:00:00Z",
                proposed_task=task_request("OP-WIRE-SEED").proposed_task,
                deadline=deadline("OP-WIRE-OTHER"),
            )
        with self.assertRaises(ValueError):
            ArtifactPutRequestDTO(
                "OP-ART-PAIR",
                "TASK-001",
                "EXEC-001",
                "final_report",
                "html",
                "text/html",
                "1.0.0",
                HASH_A,
                2,
                "2026-08-01T02:00:00Z",
                "e30=",
                deadline("OP-ART-PAIR"),
            )


class TaskAndExecutionFakeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock("2026-08-01T02:00:00Z")
        self.store = FakePlatformStore()
        self.tasks = FakeTaskRepository(self.store, self.clock)
        self.executions = FakeExecutionRepository(self.store, self.clock)

    def test_create_or_get_is_atomic_and_reuses_for_24_hours(self) -> None:
        requests = [task_request(f"OP-TASK-{index:03}", f"TASK-{index:03}") for index in range(40)]
        with ThreadPoolExecutor(max_workers=16) as executor:
            results = list(executor.map(self.tasks.create_or_get, requests))
        self.assertEqual(1, sum(result.outcome == "created" for result in results))
        self.assertEqual(1, len({result.task.task_id for result in results}))
        self.clock.advance(wall_seconds=86_400, monotonic_ms=86_400_000)
        next_request = replace(
            task_request("OP-TASK-NEXT", "TASK-NEXT"),
            deadline=deadline(
                "OP-TASK-NEXT",
                deadline_at_utc="2026-08-02T03:00:00Z",
                sent_at_utc="2026-08-02T02:00:00Z",
            ),
        )
        replacement = self.tasks.create_or_get(next_request)
        self.assertEqual("created", replacement.outcome)
        self.assertEqual("TASK-NEXT", replacement.task.task_id)

    def test_atomic_acquire_has_one_winner_and_consumes_pass_and_quota_once(self) -> None:
        self.tasks.create_or_get(task_request("OP-TASK-CREATE"))
        self.tasks.append_preflight_result(preflight_request())
        requests = [acquire_request(f"OP-EXEC-{index:03}", f"EXEC-{index:03}") for index in range(100)]
        with ThreadPoolExecutor(max_workers=24) as executor:
            results = list(executor.map(self.executions.acquire_quota_and_create, requests))
        successes = [result for result in results if not hasattr(result, "error")]
        self.assertEqual(1, len(successes))
        quota = self.executions.list_by_quota_scope(ListByQuotaScopeRequestDTO("OP-QUOTA-1", "subject-1", HASH_A, deadline("OP-QUOTA-1")))
        self.assertTrue(quota.initial_used)
        self.assertEqual(1, len(quota.executions))
        task = self.tasks.get(TaskQueryRequestDTO("OP-TASK-GET", "subject-1", "TASK-001", deadline("OP-TASK-GET")))
        self.assertEqual(successes[0].execution_id, task.locked_execution_id)
        preflight = self.tasks.get_latest_preflight(GetLatestPreflightRequestDTO("OP-PF-GET", "TASK-001", 1, HASH_B, deadline("OP-PF-GET")))
        self.assertEqual(successes[0].execution_id, preflight.consumed_by_execution_id)

    def test_expired_or_stale_pass_does_not_consume_quota(self) -> None:
        self.tasks.create_or_get(task_request("OP-TASK-CREATE"))
        self.tasks.append_preflight_result(preflight_request())
        self.clock.advance(wall_seconds=60, monotonic_ms=60_000)
        result = self.executions.acquire_quota_and_create(acquire_request("OP-EXPIRED", "EXEC-EXPIRED"))
        self.assertEqual("preflight_expired", result.error.code)
        self.assertFalse(self.executions.list_by_quota_scope(ListByQuotaScopeRequestDTO("OP-QUOTA-2", "subject-1", HASH_A, deadline("OP-QUOTA-2"))).initial_used)

    def test_preflight_slot_is_tenant_bound_and_locked_task_cannot_reopen(self) -> None:
        self.tasks.create_or_get(task_request("OP-TASK-CREATE"))
        attacker = ConsumePreflightSlotRequestDTO("OP-PF-ATTACK", "attacker-scope", "TASK-001", deadline("OP-PF-ATTACK"))
        self.assertEqual("task_not_found", self.tasks.consume_preflight_slot(attacker).error.code)
        for index in range(3):
            owner = ConsumePreflightSlotRequestDTO(f"OP-PF-OWNER-{index}", "subject-1", "TASK-001", deadline(f"OP-PF-OWNER-{index}"))
            self.assertTrue(self.tasks.consume_preflight_slot(owner).allowed)
        self.tasks.append_preflight_result(preflight_request())
        self.executions.acquire_quota_and_create(acquire_request("OP-EXEC-LOCK", "EXEC-LOCK"))
        locked = self.tasks.get(TaskQueryRequestDTO("OP-TASK-LOCKED", "subject-1", "TASK-001", deadline("OP-TASK-LOCKED")))
        second_pass = replace(preflight_request("OP-PF-REOPEN"), expected_task_version=locked.version, record=replace(preflight_request().record, preflight_id="PF-REOPEN", task_version=locked.version))
        self.assertEqual("task_version_conflict", self.tasks.append_preflight_result(second_pass).error.code)

    def test_create_replay_rejects_scope_or_fingerprint_mismatch(self) -> None:
        original = task_request("OP-TASK-REPLAY")
        self.tasks.create_or_get(original)
        mismatched = replace(original, trusted_user_scope="attacker-scope")
        self.assertEqual("fingerprint_index_conflict", self.tasks.create_or_get(mismatched).error.code)

    def test_expired_deadline_is_rejected_before_mutation(self) -> None:
        self.clock.set_utc("2026-08-01T04:00:00Z")
        result = self.tasks.create_or_get(task_request("OP-EXPIRED-DEADLINE"))
        self.assertEqual("deadline_exceeded", result.error.code)
        self.assertEqual({}, self.store.tasks)

    def test_admin_rerun_requires_persisted_matching_terminal_failure(self) -> None:
        self.tasks.create_or_get(task_request("OP-TASK-INITIAL"))
        self.tasks.append_preflight_result(preflight_request("OP-PF-INITIAL"))
        self.executions.acquire_quota_and_create(acquire_request("OP-EXEC-INITIAL", "EXEC-001"))
        self.clock.advance(wall_seconds=86_400, monotonic_ms=86_400_000)
        second_task_request = replace(
            task_request("OP-TASK-SECOND", "TASK-002"),
            deadline=deadline("OP-TASK-SECOND", deadline_at_utc="2026-08-02T03:00:00Z", sent_at_utc="2026-08-02T02:00:00Z"),
        )
        self.tasks.create_or_get(second_task_request)
        second_record = PreflightRecordDTO(
            "PF-002",
            "TASK-002",
            1,
            HASH_B,
            HASH_C,
            "2026-08-02T02:00:00Z",
            "2026-08-02T02:01:00Z",
            True,
            ({"name": "official_dataset", "required": True, "status": "healthy", "safe_reason_code": None},),
            {"items": ({"name": "official_dataset", "capability_version": "dataset-1.0.0", "status": "healthy", "checked_at": "2026-08-02T02:00:00Z"},)},
        )
        second_deadline = deadline("OP-PF-SECOND", deadline_at_utc="2026-08-02T03:00:00Z", sent_at_utc="2026-08-02T02:00:00Z")
        self.tasks.append_preflight_result(AppendPreflightResultRequestDTO("OP-PF-SECOND", 1, second_record, second_deadline))
        rerun = AcquireExecutionRequestDTO(
            operation_id="OP-EXEC-RERUN",
            trusted_user_scope="subject-1",
            request_fingerprint=HASH_A,
            task_id="TASK-002",
            expected_task_version=2,
            input_lock_hash=HASH_B,
            dependency_snapshot_hash=HASH_C,
            execution_id="EXEC-002",
            preflight_id="PF-002",
            attempt_kind="admin_technical_rerun",
            started_at="2026-08-02T02:00:30Z",
            absolute_deadline_at="2026-08-02T02:15:30Z",
            deadline=deadline("OP-EXEC-RERUN", deadline_at_utc="2026-08-02T03:00:00Z", sent_at_utc="2026-08-02T02:00:00Z"),
            admin_authorization=AdminAuthorizationDTO("AUTH-001", "admin-pseudonym-001", "2026-08-02T02:00:20Z", "technical rerun"),
            original_execution_id="EXEC-001",
            technical_failure_code="provider_timeout",
        )
        result = self.executions.acquire_quota_and_create(rerun)
        self.assertEqual("invalid_technical_failure_code", result.error.code)
        self.assertFalse(self.executions.list_by_quota_scope(ListByQuotaScopeRequestDTO("OP-QUOTA-RERUN", "subject-1", HASH_A, deadline("OP-QUOTA-RERUN", deadline_at_utc="2026-08-02T03:00:00Z", sent_at_utc="2026-08-02T02:00:00Z"))).admin_rerun_used)

    def test_acquire_rejects_cross_scope_and_wrong_fingerprint(self) -> None:
        self.tasks.create_or_get(task_request("OP-TASK-CREATE"))
        self.tasks.append_preflight_result(preflight_request())
        wrong_fingerprint = replace(acquire_request("OP-WRONG-FP", "EXEC-WRONG"), request_fingerprint=HASH_B)
        result = self.executions.acquire_quota_and_create(wrong_fingerprint)
        self.assertEqual("preflight_stale", result.error.code)
        self.assertFalse(self.executions.list_by_quota_scope(ListByQuotaScopeRequestDTO("OP-QUOTA-WRONG", "subject-1", HASH_B, deadline("OP-QUOTA-WRONG"))).initial_used)
        wrong_scope = replace(acquire_request("OP-WRONG-SCOPE", "EXEC-WRONG-SCOPE"), trusted_user_scope="attacker-scope")
        result = self.executions.acquire_quota_and_create(wrong_scope)
        self.assertEqual("preflight_stale", result.error.code)

    def test_transition_is_conditional_and_operation_replay_is_idempotent(self) -> None:
        self.tasks.create_or_get(task_request("OP-TASK-CREATE"))
        self.tasks.append_preflight_result(preflight_request())
        created = self.executions.acquire_quota_and_create(acquire_request("OP-EXEC-CREATE", "EXEC-001"))
        command = TransitionExecutionRequestDTO("OP-TRANSITION", "EXEC-001", 1, "created", "collecting", "2026-08-01T02:00:31Z", deadline=deadline("OP-TRANSITION"))
        first = self.executions.transition(command)
        replay = self.executions.transition(command)
        mismatched_replay = self.executions.transition(TransitionExecutionRequestDTO("OP-TRANSITION", "EXEC-001", 2, "collecting", "extracting", "2026-08-01T02:00:32Z", deadline("OP-TRANSITION")))
        conflict = self.executions.transition(TransitionExecutionRequestDTO("OP-TRANSITION-2", "EXEC-001", 1, "created", "collecting", "2026-08-01T02:00:31Z", deadline=deadline("OP-TRANSITION-2")))
        self.assertEqual(created.version + 1, first.version)
        self.assertEqual(first, replay)
        self.assertEqual("execution_version_conflict", mismatched_replay.error.code)
        self.assertEqual("execution_version_conflict", conflict.error.code)


class EvidenceArtifactAndEventFakeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock("2026-08-01T02:10:00Z")

    def test_evidence_and_assessments_are_append_only_and_latest_is_max_sequence(self) -> None:
        repository = FakeEvidenceRepository(self.clock)
        item = evidence()
        self.assertEqual(item, repository.append_evidence(AppendEvidenceRequestDTO("OP-EVID-1", "TASK-001", item, deadline("OP-EVID-1"))))
        self.assertEqual(item, repository.append_evidence(AppendEvidenceRequestDTO("OP-EVID-2", "TASK-001", item, deadline("OP-EVID-2"))))
        first = assessment(1, "ASSESS-001")
        second = assessment(2, "ASSESS-002")
        repository.append_assessments(AppendAssessmentsRequestDTO("OP-ASSESS-1", "TASK-001", (first,), deadline("OP-ASSESS-1")))
        repository.append_assessments(AppendAssessmentsRequestDTO("OP-ASSESS-2", "TASK-001", (second,), deadline("OP-ASSESS-2")))
        page = repository.list_for_task(ListEvidenceRequestDTO("OP-EVID-LIST", "TASK-001", deadline("OP-EVID-LIST"), limit=50))
        latest = repository.get_latest_assessments(GetLatestAssessmentsRequestDTO("OP-ASSESS-LATEST", "TASK-001", ("EVID-001",), page.snapshot_token, deadline("OP-ASSESS-LATEST")))
        self.assertEqual((second,), latest.items)
        conflict = repository.append_assessments(AppendAssessmentsRequestDTO("OP-ASSESS-3", "TASK-001", (assessment(2, "ASSESS-003"),), deadline("OP-ASSESS-3")))
        self.assertEqual("assessment_sequence_conflict", conflict.error.code)

    def test_snapshot_freezes_assessment_revision_and_rejects_forged_cursor(self) -> None:
        repository = FakeEvidenceRepository(self.clock)
        first_evidence = evidence()
        second_evidence = replace(evidence("EVID-002"), raw_record_id="RAW-002")
        repository.append_evidence(AppendEvidenceRequestDTO("OP-EVID-SNAP-1", "TASK-001", first_evidence, deadline("OP-EVID-SNAP-1")))
        repository.append_evidence(AppendEvidenceRequestDTO("OP-EVID-SNAP-2", "TASK-001", second_evidence, deadline("OP-EVID-SNAP-2")))
        repository.append_assessments(AppendAssessmentsRequestDTO("OP-ASSESS-SNAP-1", "TASK-001", (assessment(1, "ASSESS-SNAP-1"),), deadline("OP-ASSESS-SNAP-1")))
        page = repository.list_for_task(ListEvidenceRequestDTO("OP-LIST-SNAP", "TASK-001", deadline("OP-LIST-SNAP"), limit=1))
        repository.append_assessments(AppendAssessmentsRequestDTO("OP-ASSESS-SNAP-2", "TASK-001", (assessment(2, "ASSESS-SNAP-2"),), deadline("OP-ASSESS-SNAP-2")))
        latest = repository.get_latest_assessments(GetLatestAssessmentsRequestDTO("OP-LATEST-SNAP", "TASK-001", ("EVID-001",), page.snapshot_token, deadline("OP-LATEST-SNAP")))
        self.assertEqual(1, latest.items[0].assessment_sequence)
        forged = repository.list_for_task(ListEvidenceRequestDTO("OP-LIST-FORGED", "TASK-001", deadline("OP-LIST-FORGED"), snapshot_token=page.snapshot_token, cursor=f"{page.snapshot_token}:999", limit=1))
        self.assertEqual("snapshot_expired", forged.error.code)

    def test_artifact_hash_size_and_idempotency_use_decoded_content(self) -> None:
        repository = FakeArtifactRepository(self.clock)
        content = b"{}"
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        request = ArtifactPutRequestDTO(
            operation_id="OP-ART-1",
            task_id="TASK-001",
            execution_id="EXEC-001",
            artifact_type="final_report",
            format="json",
            mime_type="application/json",
            content_schema_version="1.0.0",
            sha256=digest,
            size_bytes=len(content),
            generated_at="2026-08-01T02:09:00Z",
            content_base64=base64.b64encode(content).decode("ascii"),
            deadline=deadline("OP-ART-1"),
        )
        first = repository.put(request)
        replay = repository.put(request)
        self.assertEqual(first, replay)
        bad = repository.put(request.with_operation("OP-ART-BAD", sha256=HASH_A))
        self.assertEqual("artifact_hash_mismatch", bad.error.code)

    def test_large_artifact_uses_contract_valid_signed_locator_delivery(self) -> None:
        repository = FakeArtifactRepository(self.clock)
        content = b"x" * 1_100_000
        encoded = base64.b64encode(content).decode("ascii")
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        descriptor = repository.put(ArtifactPutRequestDTO("OP-ART-LARGE", "TASK-001", "EXEC-001", "final_report", "json", "application/json", "1.0.0", digest, len(content), "2026-08-01T02:09:00Z", encoded, deadline("OP-ART-LARGE")))
        result = repository.get(ArtifactKeyRequestDTO("OP-ART-LARGE-GET", "TASK-001", "EXEC-001", "final_report", "json", "1.0.0", deadline("OP-ART-LARGE-GET")))
        self.assertEqual("signed_locator", result.delivery["kind"])
        self.assertEqual(descriptor.artifact_id, result.descriptor.artifact_id)

    def test_event_rejects_untyped_error_and_secret_bearing_safe_values(self) -> None:
        base = dict(
            event_id="EVT-UNSAFE",
            timestamp="2026-08-01T02:10:00Z",
            task_id="TASK-001",
            execution_id="EXEC-001",
            step="collect_news",
            tool="official_news_collector",
            status="failed",
            duration_ms=10,
            retry_count=0,
            result_summary={},
            deadline_remaining_ms=1000,
            correlation={"operation_id": "OP-EVENT-UNSAFE", "causation_event_id": None},
        )
        with self.assertRaises(ValueError):
            ExecutionEventDTO(sanitized_parameters={}, error={"raw_exception": "Authorization: Bearer TOP-SECRET"}, **base)
        with self.assertRaises(ValueError):
            ExecutionEventDTO(sanitized_parameters={"note": "Bearer TOP-SECRET"}, error=None, **base)
        with self.assertRaises(ValueError):
            ExecutionEventDTO(sanitized_parameters={}, error=None, correlation={}, **{key: value for key, value in base.items() if key != "correlation"})
        with self.assertRaises(ValueError):
            ExecutionEventDTO(sanitized_parameters={}, error=None, status="not-a-status", **{key: value for key, value in base.items() if key != "status"})

    def test_publisher_records_events_and_deduplicates_by_id_and_payload(self) -> None:
        publisher = FakeEventPublisher(self.clock)
        event = ExecutionEventDTO(
            event_id="EVT-001",
            timestamp="2026-08-01T02:10:00Z",
            task_id="TASK-001",
            execution_id="EXEC-001",
            step="collect_news",
            tool="official_news_collector",
            status="completed",
            duration_ms=10,
            retry_count=0,
            sanitized_parameters={"asset": "BTC"},
            result_summary={"records": 1},
            error=None,
            deadline_remaining_ms=1000,
            correlation={"operation_id": "OP-EVENT-1", "causation_event_id": None},
        )
        first = publisher.publish(PublishEventRequestDTO("OP-EVENT-1", event, deadline("OP-EVENT-1")))
        replay = publisher.publish(PublishEventRequestDTO("OP-EVENT-2", event, deadline("OP-EVENT-2")))
        self.assertEqual("published", first.outcome)
        self.assertEqual("existing", replay.outcome)
        self.assertEqual((event,), publisher.recorded_events)
        changed = event.replace(status="failed")
        conflict = publisher.publish(PublishEventRequestDTO("OP-EVENT-3", changed, deadline("OP-EVENT-3")))
        self.assertEqual("event_id_conflict", conflict.error.code)


class FullPortSurfaceSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock("2026-08-01T02:00:00Z")
        self.store = FakePlatformStore()
        self.tasks = FakeTaskRepository(self.store, self.clock)
        self.executions = FakeExecutionRepository(self.store, self.clock)

    def test_all_six_task_and_five_execution_methods(self) -> None:
        create_slot = ConsumeTaskCreateSlotRequestDTO("OP-SLOT-1", "subject-1", "hmac-pseudonym-001", deadline("OP-SLOT-1"))
        self.assertTrue(self.tasks.consume_task_create_slot(create_slot).allowed)
        self.tasks.create_or_get(task_request("OP-TASK-SMOKE"))
        self.assertEqual("TASK-001", self.tasks.get(TaskQueryRequestDTO("OP-TASK-SMOKE-GET", "subject-1", "TASK-001", deadline("OP-TASK-SMOKE-GET"))).task_id)
        preflight_slot = ConsumePreflightSlotRequestDTO("OP-PF-SLOT", "subject-1", "TASK-001", deadline("OP-PF-SLOT"))
        self.assertTrue(self.tasks.consume_preflight_slot(preflight_slot).allowed)
        self.tasks.append_preflight_result(preflight_request("OP-PF-SMOKE"))
        self.assertEqual("PF-001", self.tasks.get_latest_preflight(GetLatestPreflightRequestDTO("OP-PF-SMOKE-GET", "TASK-001", 1, HASH_B, deadline("OP-PF-SMOKE-GET"))).preflight_id)

        created = self.executions.acquire_quota_and_create(acquire_request("OP-EXEC-SMOKE", "EXEC-001"))
        self.assertEqual(created, self.executions.get(GetExecutionRequestDTO("OP-EXEC-GET", "subject-1", "EXEC-001", deadline("OP-EXEC-GET"))))
        collecting = self.executions.transition(TransitionExecutionRequestDTO("OP-EXEC-COLLECT", "EXEC-001", 1, "created", "collecting", "2026-08-01T02:00:31Z", deadline("OP-EXEC-COLLECT")))
        manual = self.executions.transition(TransitionExecutionRequestDTO("OP-EXEC-MANUAL", "EXEC-001", collecting.version, "collecting", "manual_case_required", "2026-08-01T02:00:32Z", deadline("OP-EXEC-MANUAL"), safe_reason_code="admin_review_required"))
        case = self.executions.record_manual_case(RecordManualCaseRequestDTO("OP-CASE", "EXEC-001", manual.version, "admin_review_required", "2026-08-01T02:00:33Z", deadline("OP-CASE")))
        self.assertEqual("open", case.status)
        quota = self.executions.list_by_quota_scope(ListByQuotaScopeRequestDTO("OP-QUOTA-SMOKE", "subject-1", HASH_A, deadline("OP-QUOTA-SMOKE")))
        self.assertTrue(quota.manual_case_required)

    def test_all_six_evidence_methods(self) -> None:
        repository = FakeEvidenceRepository(self.clock)
        item = evidence()
        repository.append_evidence(AppendEvidenceRequestDTO("OP-EVID-SMOKE", "TASK-001", item, deadline("OP-EVID-SMOKE")))
        self.assertEqual(item, repository.get(GetEvidenceRequestDTO("OP-EVID-GET", "TASK-001", "EVID-001", deadline("OP-EVID-GET"))))
        link = EvidenceClaimLinkDTO("LINK-001", "TASK-001", "EVID-001", "CLAIM-001", "supports", "2026-08-01T02:01:00Z")
        links = repository.append_claim_links(AppendClaimLinksRequestDTO("OP-LINK", "TASK-001", (link,), deadline("OP-LINK")))
        self.assertEqual("appended", links.items[0].outcome)
        repository.append_assessments(AppendAssessmentsRequestDTO("OP-ASSESS", "TASK-001", (assessment(1, "ASSESS-001"),), deadline("OP-ASSESS")))
        page = repository.list_for_task(ListEvidenceRequestDTO("OP-LIST", "TASK-001", deadline("OP-LIST")))
        latest = repository.get_latest_assessments(GetLatestAssessmentsRequestDTO("OP-LATEST", "TASK-001", ("EVID-001",), page.snapshot_token, deadline("OP-LATEST")))
        self.assertEqual(1, latest.items[0].assessment_sequence)

    def test_all_five_artifact_and_two_event_methods(self) -> None:
        repository = FakeArtifactRepository(self.clock)
        descriptors = []
        triples = (
            ("final_report", "json", "application/json"),
            ("evidence_list", "json", "application/json"),
            ("execution_log", "jsonl", "application/x-ndjson"),
        )
        content = b"{}"
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        encoded = base64.b64encode(content).decode("ascii")
        for index, (artifact_type, artifact_format, mime_type) in enumerate(triples):
            operation_id = f"OP-ART-SMOKE-{index}"
            descriptor = repository.put(ArtifactPutRequestDTO(operation_id, "TASK-001", "EXEC-001", artifact_type, artifact_format, mime_type, "1.0.0", digest, 2, f"2026-08-01T02:0{index}:00Z", encoded, deadline(operation_id)))
            descriptors.append(descriptor)
        content_result = repository.get(ArtifactKeyRequestDTO("OP-ART-GET", "TASK-001", "EXEC-001", "final_report", "json", "1.0.0", deadline("OP-ART-GET")))
        self.assertEqual(encoded, content_result.delivery["content_base64"])
        self.assertEqual(3, len(repository.list_for_execution(ArtifactExecutionRequestDTO("OP-ART-LIST", "TASK-001", "EXEC-001", deadline("OP-ART-LIST"))).items))
        entries = tuple({"artifact_type": item.artifact_type, "format": item.format, "mime_type": item.mime_type, "content_schema_version": item.content_schema_version, "generated_at": str(item.generated_at), "artifact_id": item.artifact_id, "sha256": item.sha256, "size_bytes": item.size_bytes} for item in descriptors)
        manifest = ArtifactManifestDTO("TASK-001", "EXEC-001", "complete", entries, (), "2026-08-01T02:05:00Z")
        manifest_content = json.dumps(manifest.to_wire(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        manifest_encoded = base64.b64encode(manifest_content).decode("ascii")
        manifest_digest = "sha256:" + hashlib.sha256(manifest_content).hexdigest()
        manifest_descriptor = repository.put_manifest(PutManifestRequestDTO("OP-MANIFEST", manifest, manifest_digest, len(manifest_content), manifest_encoded, deadline("OP-MANIFEST")))
        self.assertEqual("manifest", manifest_descriptor.artifact_type)
        self.assertEqual(manifest, repository.get_manifest(ArtifactExecutionRequestDTO("OP-MANIFEST-GET", "TASK-001", "EXEC-001", deadline("OP-MANIFEST-GET"))))

        publisher = FakeEventPublisher(self.clock)
        first = ExecutionEventDTO("EVT-101", "2026-08-01T02:10:00Z", "TASK-001", "EXEC-001", "collect", "collector", "started", 0, 0, {}, {}, None, 1000, {"operation_id": "OP-BATCH", "causation_event_id": None})
        second = first.replace(event_id="EVT-102", status="completed")
        batch = publisher.publish_batch(PublishBatchRequestDTO("OP-BATCH", (first, second), deadline("OP-BATCH")))
        self.assertEqual(("published", "published"), tuple(item.outcome for item in batch.items))
        self.assertEqual("existing", publisher.publish(PublishEventRequestDTO("OP-PUBLISH-EXISTING", first, deadline("OP-PUBLISH-EXISTING"))).outcome)


class FrozenContractRegistryTests(unittest.TestCase):
    def test_frozen_valid_and_invalid_examples_still_conform(self) -> None:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource

        schema_root = PROJECT_ROOT / "docs" / "architecture" / "schemas"
        common = json.loads((schema_root / "common" / "common.schema.json").read_text(encoding="utf-8"))
        registry = Registry().with_resource(common["$id"], Resource.from_contents(common))
        ports = ("task_repository", "execution_repository", "evidence_repository", "artifact_repository", "event_publisher", "clock")
        valid_count = 0
        invalid_count = 0
        for port in ports:
            contract = json.loads((schema_root / port / "contract.schema.json").read_text(encoding="utf-8"))
            validator = Draft202012Validator(contract, registry=registry)
            valid_examples = json.loads((schema_root / port / "valid-examples.json").read_text(encoding="utf-8"))["examples"]
            invalid_examples = json.loads((schema_root / port / "invalid-examples.json").read_text(encoding="utf-8"))["examples"]
            for example in valid_examples:
                self.assertEqual([], list(validator.iter_errors(example["value"])), example["test_id"])
                valid_count += 1
            for example in invalid_examples:
                self.assertTrue(list(validator.iter_errors(example["value"])), example["test_id"])
                invalid_count += 1
        self.assertEqual(26, valid_count)
        self.assertEqual(26, invalid_count)

    def test_six_ports_expose_all_26_stable_method_ids(self) -> None:
        schema_root = PROJECT_ROOT / "docs" / "architecture" / "schemas"
        ports = ("task_repository", "execution_repository", "evidence_repository", "artifact_repository", "event_publisher", "clock")
        identifiers: set[str] = set()

        def walk(value: object) -> None:
            if isinstance(value, dict):
                identifier = value.get("x-contract-test-id")
                if isinstance(identifier, str):
                    identifiers.add(identifier)
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        for port in ports:
            walk(json.loads((schema_root / port / "contract.schema.json").read_text(encoding="utf-8")))
        self.assertEqual(26, len(identifiers))
        self.assertIn("CT-EXEC-ACQUIRE-01", identifiers)
        self.assertNotIn("CT-TASK-LOCK-EXECUTION-01", identifiers)


if __name__ == "__main__":
    unittest.main()
