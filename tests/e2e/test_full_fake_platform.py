"""T80 full local fake-platform E2E acceptance suite.

The suite intentionally combines the T63 HTTP spine with real Core use cases,
Core orchestration/publication, and scenario-driven local fakes.  It performs no
AWS, model, browser, or external network I/O.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from threading import Barrier, BrokenBarrierError, Lock
from urllib.parse import parse_qs, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO  # noqa: E402
from crypto_trust_agent.application.dto.market_regime import (  # noqa: E402
    ExpectedModelDTO,
    FeatureDTO,
    FeatureWindowDTO,
    InferRequestDTO,
)
from crypto_trust_agent.application.dto.reasoning import (  # noqa: E402
    ConclusionDTO,
    ConfidenceComponentsDTO,
    FactDTO,
    InferenceDTO,
    ProviderDTO,
    ReasoningResultDTO,
)
from crypto_trust_agent.application.dto.repositories import (  # noqa: E402
    ArtifactExecutionRequestDTO,
    ArtifactKeyRequestDTO,
    ClockReadRequestDTO,
    TransitionExecutionRequestDTO,
)
from crypto_trust_agent.application.orchestration.artifact_assembler import (  # noqa: E402
    FormalRunArtifactAssembler,
)
from crypto_trust_agent.application.orchestration.formal_run import (  # noqa: E402
    DEFAULT_FORMAL_RUN_BUDGET_POLICY,
    FORMAL_RUN_HARD_DEADLINE_MS,
    FormalRunCommand,
    FormalRunOrchestrator,
    FormalRunStep,
    FormalRunStepRequest,
    FormalRunStepResult,
    StepOutcome,
    TerminalOutcome,
)
from crypto_trust_agent.application.planning import (  # noqa: E402
    QuestionType,
    RequirementLevel,
    build_plan,
    source_requirement_matrix,
)
from crypto_trust_agent.application.publication import ArtifactPublicationService  # noqa: E402
from crypto_trust_agent.application.reasoning import (  # noqa: E402
    EvidenceRankingInput,
    ReasoningSequenceRunner,
    ReasoningTokenCounter,
    StructuredReasoningContextBuilder,
)
from crypto_trust_agent.application.use_cases.create_task import (  # noqa: E402
    CreateTaskCommand,
    CreateTaskRateLimited,
    CreateTaskUseCase,
)
from crypto_trust_agent.application.use_cases.infer_market_regime import (  # noqa: E402
    InferMarketRegimeCommand,
    InferMarketRegimeUseCase,
)
from crypto_trust_agent.application.use_cases.preflight import (  # noqa: E402
    PreflightCommand,
    PreflightRateLimited,
    PreflightUseCase,
)
from crypto_trust_agent.application.use_cases.start_formal_execution import (  # noqa: E402
    StartFormalExecutionAuthorizationError,
    StartFormalExecutionCommand,
    StartFormalExecutionUseCase,
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
from crypto_trust_agent.domain.fingerprint import create_request_fingerprint  # noqa: E402
from crypto_trust_agent.domain.primitives import CanonicalDecimal  # noqa: E402
from crypto_trust_agent.domain.trust import Contradiction  # noqa: E402
from crypto_trust_agent.infrastructure.fakes import (  # noqa: E402
    FakeArtifactRepository,
    FakeClock,
    FakeEventPublisher,
    FakeExecutionRepository,
    FakeFormalRunStepExecutor,
    FakePlatformStore,
    FakeProviderHealthProbe,
    FakeTaskReadinessProbe,
    FakeTaskRepository,
)
from crypto_trust_agent.infrastructure.fakes.evidence_extractor_v2 import (  # noqa: E402
    FakeEvidenceExtractorV2,
)
from crypto_trust_agent.infrastructure.fakes.market_regime import (  # noqa: E402
    FakeMarketRegimeProvider,
)
from crypto_trust_agent.infrastructure.fakes.reasoning import FakeReasoningProvider  # noqa: E402
from crypto_trust_agent.infrastructure.identity import (  # noqa: E402
    CognitoPrincipalBoundary,
    FakeCognitoTokenVerifier,
    HmacPrincipalPseudonymizer,
)
from crypto_trust_agent.presentation.api.demo_ui_api import (  # noqa: E402
    create_local_demo_fastapi_app,
)
from crypto_trust_agent.presentation.api.demo_ui_composition import (  # noqa: E402
    DEMO_OTHER_USER_TOKEN,
    DEMO_USER_TOKEN,
    classify_demo_question,
)
from tests.contract.shared_evidence_extractor_v2_assertions import (  # noqa: E402
    extract_request,
    invalid_original_result,
    repair_request,
)
from tests.contract.shared_reasoning_assertions import (  # noqa: E402
    deadline as reasoning_deadline,
    generate_request,
    invalid_result,
)

TIMEFRAME_START = "2026-07-18T00:00:00Z"
TIMEFRAME_END = "2026-08-01T00:00:00Z"
PSEUDONYM_PREFIX = "hmac-sha256:t80-v1:"
FULL_BUNDLE = {
    ("final_report", "json"),
    ("markdown_report", "markdown"),
    ("html_report", "html"),
    ("evidence_list", "json"),
    ("csv_evidence", "csv"),
    ("execution_log", "jsonl"),
    ("manifest", "json"),
}
MINIMUM_BUNDLE = {
    ("final_report", "json"),
    ("evidence_list", "json"),
    ("execution_log", "jsonl"),
    ("manifest", "json"),
}


def auth(token: str = DEMO_USER_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def payload(question: str, assets: tuple[str, ...] = ("BTC",)) -> dict[str, object]:
    return {
        "question": question,
        "assets": list(assets),
        "timeframe_start": TIMEFRAME_START,
        "timeframe_end": TIMEFRAME_END,
    }


def params(result: dict[str, object]) -> dict[str, str]:
    parsed = parse_qs(urlsplit(str(result["status_url"])).query)
    return {
        "task_id": parsed["task_id"][0],
        "execution_id": parsed["execution_id"][0],
    }


def repository_deadline(clock: FakeClock, operation_id: str) -> DeadlineDTO:
    now = clock.current_utc()
    return DeadlineDTO(
        "1.0.0",
        operation_id,
        (now.as_datetime() + timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
        10_000,
        now,
        100,
    )


class DeterministicIDs:
    def __init__(self) -> None:
        self._value = 0
        self._lock = Lock()

    def __call__(self, prefix: str) -> str:
        with self._lock:
            self._value += 1
            return f"{prefix}{self._value:08X}"


class RecordingArtifactRepository:
    """Transparent recorder around the Core-owned artifact fake."""

    non_production = True

    def __init__(self, delegate: FakeArtifactRepository) -> None:
        self.delegate = delegate
        self.writes: list[tuple[str, str, str]] = []

    def put(self, request):
        result = self.delegate.put(request)
        if not isinstance(result, ErrorResultDTO):
            self.writes.append(("put", request.artifact_type, request.format))
        return result

    def get(self, request):
        return self.delegate.get(request)

    def list_for_execution(self, request):
        return self.delegate.list_for_execution(request)

    def put_manifest(self, request):
        result = self.delegate.put_manifest(request)
        if not isinstance(result, ErrorResultDTO):
            self.writes.append(("put_manifest", "manifest", "json"))
        return result

    def get_manifest(self, request):
        return self.delegate.get_manifest(request)


class ConcurrencyProbeExecutor:
    """Observe job concurrency while delegating every result to the real fake."""

    non_production = True

    def __init__(self, delegate: FakeFormalRunStepExecutor) -> None:
        self.delegate = delegate
        self._barrier = Barrier(2)
        self._lock = Lock()
        self._collection_entries = 0
        self._active = 0
        self.max_active = 0

    def execute(self, request: FormalRunStepRequest) -> FormalRunStepResult:
        synchronize = False
        if request.step is FormalRunStep.COLLECTION:
            with self._lock:
                self._collection_entries += 1
                synchronize = self._collection_entries <= 2
                self._active += 1
                self.max_active = max(self.max_active, self._active)
            if synchronize:
                try:
                    self._barrier.wait(timeout=2)
                except BrokenBarrierError:
                    pass
        try:
            return self.delegate.execute(request)
        finally:
            if request.step is FormalRunStep.COLLECTION:
                with self._lock:
                    self._active -= 1


class CoreFixture:
    """Shared no-network composition for non-HTTP T80 scenarios."""

    def __init__(self, *, monotonic_ms: int = 0, concurrency_probe: bool = False) -> None:
        self.clock = FakeClock("2026-08-01T02:00:00Z", monotonic_ms=monotonic_ms)
        self.store = FakePlatformStore()
        self.ids = DeterministicIDs()
        self.tasks = FakeTaskRepository(self.store, self.clock)
        self.executions = FakeExecutionRepository(self.store, self.clock)
        self.readiness = FakeTaskReadinessProbe(self.store, self.clock)
        self.probes = (
            FakeProviderHealthProbe(self.clock, "nova", "extraction_v1"),
            FakeProviderHealthProbe(self.clock, "opus", "reasoning_v1"),
            FakeProviderHealthProbe(self.clock, "sagemaker", "market_regime_v1"),
            FakeProviderHealthProbe(self.clock, "external_allowlist", "collector_policy_v1"),
        )
        self.artifact_delegate = FakeArtifactRepository(self.clock)
        self.artifacts = RecordingArtifactRepository(self.artifact_delegate)
        self.events = FakeEventPublisher(self.clock)
        self.step_executor = FakeFormalRunStepExecutor(self.clock, populate_pipeline=True)
        if concurrency_probe:
            self.executor = ConcurrencyProbeExecutor(self.step_executor)
        else:
            self.executor = self.step_executor
        self.publication = ArtifactPublicationService(self.artifacts, self.clock)
        self.orchestrator = FormalRunOrchestrator(
            self.clock,
            self.executor,
            self.events,
            self.executions,
            artifact_assembler=FormalRunArtifactAssembler(),
            artifact_publication=self.publication,
            artifact_repository=self.artifacts,
        )
        self.create = CreateTaskUseCase(
            self.tasks,
            self.clock,
            classify_demo_question,
            self.ids,
            lambda _record: None,
        )
        self.preflight = PreflightUseCase(
            self.tasks,
            self.clock,
            self.readiness,
            *self.probes,
            self.ids,
        )
        self.start = StartFormalExecutionUseCase(
            self.tasks,
            self.executions,
            self.clock,
            self.ids,
        )

    @staticmethod
    def pseudonym(subject: str) -> str:
        return PSEUDONYM_PREFIX + hashlib.sha256(subject.encode("utf-8")).hexdigest()

    def create_command(
        self,
        subject: str,
        question: str,
        assets: tuple[str, ...] = ("BTC",),
        *,
        timeframe_start: str = TIMEFRAME_START,
        timeframe_end: str = TIMEFRAME_END,
    ) -> CreateTaskCommand:
        return CreateTaskCommand(
            subject,
            self.pseudonym(subject),
            question,
            assets,
            timeframe_start,
            timeframe_end,
            True,
        )

    def create_plan(self, question: str, assets: tuple[str, ...]):
        fingerprint = create_request_fingerprint(
            question=question,
            assets=assets,
            timeframe_start=TIMEFRAME_START,
            timeframe_end=TIMEFRAME_END,
        )
        question_type = classify_demo_question(
            fingerprint.normalized_question,
            fingerprint.assets_requested_order,
        )
        return build_plan(
            question_type=question_type,
            fingerprint=fingerprint,
            clock_snapshot=str(self.clock.current_utc()),
        )

    def create_preflight_start(
        self,
        *,
        subject: str = "t80-user",
        question: str = "請分析 BTC 目前的市場狀況。",
        assets: tuple[str, ...] = ("BTC",),
        is_admin: bool = False,
        original_execution_id: str | None = None,
        technical_failure_code: str | None = None,
    ):
        plan = self.create_plan(question, assets)
        created = self.create.execute(self.create_command(subject, question, assets))
        preflight = self.preflight.execute(PreflightCommand(subject, created.task_id))
        self.assert_ready(preflight)
        started = self.start.execute(StartFormalExecutionCommand(
            trusted_user_scope=subject,
            principal_pseudonym=self.pseudonym(subject),
            is_admin=is_admin,
            task_id=created.task_id,
            preflight_id=preflight.record.preflight_id,
            input_lock_hash=preflight.record.input_lock_hash,
            original_execution_id=original_execution_id,
            technical_failure_code=technical_failure_code,
        ))
        return created, preflight, started, plan

    @staticmethod
    def assert_ready(preflight) -> None:
        if not preflight.ready:
            raise AssertionError(preflight.to_safe_dict())

    def execute_formal(self, started, plan, question: str, operation_id: str):
        if started.execution is None:
            raise AssertionError("execution was not created")
        return self.orchestrator.execute(FormalRunCommand(
            operation_id,
            started.execution,
            plan,
            question=question,
        ))

    def fail_execution(self, execution_id: str, code: str) -> None:
        current = self.store.executions[execution_id]
        first_operation = f"OP-T80-COLLECT-{execution_id}"
        collecting = self.executions.transition(TransitionExecutionRequestDTO(
            first_operation,
            execution_id,
            current.version,
            "created",
            "collecting",
            self.clock.current_utc(),
            repository_deadline(self.clock, first_operation),
        ))
        if isinstance(collecting, ErrorResultDTO):
            raise AssertionError(collecting.error.code)
        fail_operation = f"OP-T80-FAIL-{execution_id}"
        failed = self.executions.transition(TransitionExecutionRequestDTO(
            fail_operation,
            execution_id,
            collecting.version,
            "collecting",
            "failed",
            self.clock.current_utc(),
            repository_deadline(self.clock, fail_operation),
            safe_reason_code=code,
        ))
        if isinstance(failed, ErrorResultDTO):
            raise AssertionError(failed.error.code)


def verified_principals(clock: FakeClock):
    issuer = "https://t80.local.invalid/cognito"
    audience = "crypto-trust-t80"
    common = {"iss": issuer, "aud": audience, "exp": 4_102_444_800}
    boundary = CognitoPrincipalBoundary(
        FakeCognitoTokenVerifier({
            "t80-local-user": {
                **common,
                "sub": "t80-user",
                "cognito:groups": ["Users"],
            },
            "t80-local-admin": {
                **common,
                "sub": "t80-user",
                "cognito:groups": ["Users", "CryptoTrustAdmins"],
            },
        }),
        issuer,
        audience,
        HmacPrincipalPseudonymizer(
            "t80-v1",
            b"t80-local-pseudonym-key-32-bytes",
        ),
        now_provider=lambda: clock.current_utc().as_datetime(),
    )
    return (
        boundary.authenticate("Bearer t80-local-user"),
        boundary.authenticate("Bearer t80-local-admin"),
    )


class FullWorkflowMatrixE2ETests(unittest.TestCase):
    def run_http(self, question: str, assets: tuple[str, ...]):
        app = create_local_demo_fastapi_app()
        client = TestClient(app)
        response = client.post(
            "/demo/submit",
            json=payload(question, assets),
            headers=auth(),
        )
        self.assertEqual(201, response.status_code, response.text)
        return app.state.demo_composition, client, response.json()

    def test_three_question_types_use_real_planner_and_publish_traceable_full_bundles(self) -> None:
        cases = (
            (
                QuestionType.MARKET_STATUS,
                "請分析 BTC 目前的市場狀況，列出關鍵證據與限制。",
                ("BTC",),
            ),
            (
                QuestionType.HYPOTHESIS_VALIDATION,
                "請驗證 BTC hypothesis 是否受現有證據支持。",
                ("BTC",),
            ),
            (
                QuestionType.ASSET_COMPARISON,
                "請比較 ETH 與 BTC 的市場位置與風險。",
                ("ETH", "BTC"),
            ),
        )
        for expected_type, question, assets in cases:
            with self.subTest(question_type=expected_type.value):
                composition, _client, result = self.run_http(question, assets)
                self.assertTrue(result["preflight_ready"])
                self.assertEqual("completed", result["state"])
                self.assertEqual("success", result["terminal_outcome"])
                self.assertEqual("complete", result["publication_outcome"])

                fingerprint = create_request_fingerprint(
                    question=question,
                    assets=assets,
                    timeframe_start=TIMEFRAME_START,
                    timeframe_end=TIMEFRAME_END,
                )
                plan = build_plan(
                    question_type=expected_type,
                    fingerprint=fingerprint,
                    clock_snapshot="2026-08-01T02:00:00Z",
                )
                self.assertEqual(assets, plan.assets_requested_order)
                self.assertEqual(
                    dict(source_requirement_matrix(expected_type)),
                    dict(plan.source_requirements),
                )
                self.assertEqual(
                    {"market", "news", "official", "on_chain", "social", "macro"},
                    {job.category for job in plan.sourcing_jobs},
                )
                actual_jobs = {
                    request.planned_job_ids[0]
                    for request in composition.step_executor.requests
                    if request.step is FormalRunStep.COLLECTION
                    and len(request.planned_job_ids) == 1
                }
                self.assertEqual({job.job_id for job in plan.sourcing_jobs}, actual_jobs)

                principal = composition.authenticator.authenticate(
                    auth()["Authorization"]
                )
                listed = composition.use_case.list_artifacts(
                    principal=principal,
                    task_id=str(result["task_id"]),
                    execution_id=str(result["execution_id"]),
                )
                self.assertEqual(
                    FULL_BUNDLE,
                    {(item.artifact_type, item.format) for item in listed.items},
                )
                report = composition.use_case.get_artifact_document(
                    principal=principal,
                    task_id=str(result["task_id"]),
                    execution_id=str(result["execution_id"]),
                    artifact_type="final_report",
                ).payload
                evidence = composition.use_case.get_artifact_document(
                    principal=principal,
                    task_id=str(result["task_id"]),
                    execution_id=str(result["execution_id"]),
                    artifact_type="evidence_list",
                ).payload
                facts = tuple(report["facts"])
                self.assertTrue(facts)
                self.assertTrue(tuple(report["conclusions"]))
                self.assertTrue(
                    all(
                        tuple(item["evidence_refs"]) or tuple(item["analysis_refs"])
                        for item in facts
                    )
                )
                evidence_ids = {item["evidence_id"] for item in evidence["items"]}
                for fact in facts:
                    self.assertTrue(set(fact["evidence_refs"]) <= evidence_ids)
                for item in evidence["items"]:
                    self.assertTrue(item["source"])
                    self.assertTrue(item["source_locator"])
                    self.assertTrue(item["fetched_at"])
                    self.assertRegex(item["content_hash"], r"^sha256:[0-9a-f]{64}$")
                    self.assertTrue(item["content_reference"])
                    self.assertTrue(item["lineage"])
                    self.assertTrue(item["assessment_id"])
                    self.assertTrue(item["assessment_version"])

    def test_idempotent_replay_is_stable_and_different_question_changes_report_hash(self) -> None:
        question = "請分析 BTC 目前的市場狀況。"
        first_app = create_local_demo_fastapi_app()
        first_client = TestClient(first_app)
        first_response = first_client.post(
            "/demo/submit", json=payload(question), headers=auth()
        )
        self.assertEqual(201, first_response.status_code)
        first = first_response.json()
        composition = first_app.state.demo_composition
        calls_before = composition.step_executor.calls
        second = first_client.post(
            "/demo/submit", json=payload(question), headers=auth()
        ).json()
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(first["execution_id"], second["execution_id"])
        self.assertEqual(calls_before, composition.step_executor.calls)
        self.assertEqual(1, len(composition.store.tasks))
        self.assertEqual(1, len(composition.store.executions))

        def hashes(run_question: str) -> dict[tuple[str, str], str]:
            app = create_local_demo_fastapi_app()
            client = TestClient(app)
            result = client.post(
                "/demo/submit", json=payload(run_question), headers=auth()
            ).json()
            c = app.state.demo_composition
            principal = c.authenticator.authenticate(auth()["Authorization"])
            listed = c.use_case.list_artifacts(
                principal=principal,
                task_id=result["task_id"],
                execution_id=result["execution_id"],
            )
            return {
                (item.artifact_type, item.format): item.sha256
                for item in listed.items
            }

        first_hashes = hashes(question)
        self.assertEqual(first_hashes, hashes(question))
        changed = hashes("請分析 BTC 的不同市場問題與限制。")
        self.assertNotEqual(
            first_hashes[("final_report", "json")],
            changed[("final_report", "json")],
        )


class IdentityRateQuotaE2ETests(unittest.TestCase):
    def test_24_hour_reuse_boundary_and_tenant_or_input_isolation(self) -> None:
        fixture = CoreFixture()
        question = "請分析 BTC 目前市場狀況。"
        command = fixture.create_command("tenant-a", question)
        created = fixture.create.execute(command)
        reused = fixture.create.execute(command)
        expected_fingerprint = create_request_fingerprint(
            question=question,
            assets=("BTC",),
            timeframe_start=TIMEFRAME_START,
            timeframe_end=TIMEFRAME_END,
        ).request_fingerprint
        self.assertEqual("created", created.outcome)
        self.assertEqual("reused", reused.outcome)
        self.assertEqual(created.task_id, reused.task_id)
        self.assertEqual(
            expected_fingerprint,
            fixture.store.tasks[created.task_id].request_fingerprint,
        )

        fixture.clock.advance(wall_seconds=24 * 60 * 60 + 1)
        after_boundary = fixture.create.execute(command)
        self.assertEqual("created", after_boundary.outcome)
        self.assertNotEqual(created.task_id, after_boundary.task_id)

        other_tenant = fixture.create.execute(
            fixture.create_command("tenant-b", question)
        )
        changed_question = fixture.create.execute(
            fixture.create_command("tenant-c", "請分析 BTC 的另一個問題。")
        )
        changed_assets = fixture.create.execute(
            fixture.create_command("tenant-d", "請分析 ETH 市場狀況。", ("ETH",))
        )
        changed_timeframe = fixture.create.execute(
            fixture.create_command(
                "tenant-e",
                question,
                timeframe_start="2026-07-17T00:00:00Z",
            )
        )
        self.assertEqual(
            5,
            len({
                after_boundary.task_id,
                other_tenant.task_id,
                changed_question.task_id,
                changed_assets.task_id,
                changed_timeframe.task_id,
            }),
        )

    def test_create_and_preflight_rate_limits_short_circuit_before_probes(self) -> None:
        fixture = CoreFixture()
        command = fixture.create_command("rate-user", "請分析 BTC 市場狀況。")
        for index in range(10):
            result = fixture.create.execute(command)
            self.assertEqual("created" if index == 0 else "reused", result.outcome)
        with self.assertRaises(CreateTaskRateLimited):
            fixture.create.execute(command)
        self.assertEqual(1, len(fixture.store.tasks))

        other = CoreFixture()
        task = other.create.execute(
            other.create_command("preflight-user", "請分析 BTC 市場狀況。")
        )
        for _ in range(3):
            self.assertTrue(
                other.preflight.execute(
                    PreflightCommand("preflight-user", task.task_id)
                ).ready
            )
        before = (
            other.readiness.probe_count,
            *(probe.probe_count for probe in other.probes),
        )
        with self.assertRaises(PreflightRateLimited):
            other.preflight.execute(
                PreflightCommand("preflight-user", task.task_id)
            )
        self.assertEqual(
            before,
            (
                other.readiness.probe_count,
                *(probe.probe_count for probe in other.probes),
            ),
        )

    def test_cross_task_quota_verified_admin_rerun_and_no_third_attempt(self) -> None:
        fixture = CoreFixture()
        user, admin = verified_principals(fixture.clock)
        self.assertFalse(user.is_admin)
        self.assertTrue(admin.is_admin)
        question = "請分析 BTC 市場狀況。"

        _created, _preflight, first, _plan = fixture.create_preflight_start(
            subject=user.subject,
            question=question,
            is_admin=user.is_admin,
        )
        first_execution = first.execution
        self.assertIsNotNone(first_execution)
        fixture.fail_execution(first_execution.execution_id, "provider_timeout")

        fixture.clock.advance(wall_seconds=24 * 60 * 60 + 1)
        second_task = fixture.create.execute(
            fixture.create_command(user.subject, question)
        )
        second_preflight = fixture.preflight.execute(
            PreflightCommand(user.subject, second_task.task_id)
        )
        with self.assertRaises(StartFormalExecutionAuthorizationError):
            fixture.start.execute(StartFormalExecutionCommand(
                user.subject,
                user.pseudonym,
                user.is_admin,
                second_task.task_id,
                second_preflight.record.preflight_id,
                second_preflight.record.input_lock_hash,
                original_execution_id=first_execution.execution_id,
                technical_failure_code="provider_timeout",
            ))

        rerun = fixture.start.execute(StartFormalExecutionCommand(
            admin.subject,
            admin.pseudonym,
            admin.is_admin,
            second_task.task_id,
            second_preflight.record.preflight_id,
            second_preflight.record.input_lock_hash,
            original_execution_id=first_execution.execution_id,
            technical_failure_code="provider_timeout",
        ))
        self.assertEqual(2, rerun.execution.attempt_number)
        self.assertEqual(
            2,
            len(fixture.store.quota[(
                user.subject,
                fixture.store.tasks[first_execution.task_id].request_fingerprint,
            )]),
        )
        fixture.fail_execution(rerun.execution.execution_id, "provider_timeout")

        fixture.clock.advance(wall_seconds=24 * 60 * 60 + 1)
        third_task = fixture.create.execute(
            fixture.create_command(user.subject, question)
        )
        third_preflight = fixture.preflight.execute(
            PreflightCommand(user.subject, third_task.task_id)
        )
        third = fixture.start.execute(StartFormalExecutionCommand(
            admin.subject,
            admin.pseudonym,
            admin.is_admin,
            third_task.task_id,
            third_preflight.record.preflight_id,
            third_preflight.record.input_lock_hash,
            original_execution_id=first_execution.execution_id,
            technical_failure_code="provider_timeout",
        ))
        self.assertEqual("manual_case_opened", third.outcome)
        self.assertIsNone(third.execution)
        self.assertNotIn(third_task.task_id, fixture.store.execution_scope.values())


class DegradationAndDeadlineE2ETests(unittest.TestCase):
    def run_fault(self, *, required: bool):
        app = create_local_demo_fastapi_app()
        composition = app.state.demo_composition
        limitations = (
            "缺少必要來源 official（required_source_missing）。"
            if required
            else "選用來源 social 無法使用（optional_source_failed）。"
        )
        composition.step_executor.configure_report_limitations(limitations)
        composition.step_executor.configure(
            FormalRunStep.COLLECTION,
            outcome=StepOutcome.DEGRADED,
            safe_reason_code="source_coverage_degraded",
            required_source_failures=("official",) if required else (),
            optional_source_failures=() if required else ("social",),
        )
        client = TestClient(app)
        response = client.post(
            "/demo/submit",
            json=payload("請分析 BTC 市場狀況與來源限制。"),
            headers=auth(),
        )
        self.assertEqual(201, response.status_code, response.text)
        return composition, client, response.json(), limitations

    def test_optional_and_required_source_failures_remain_visible_in_report_and_status(self) -> None:
        optional, optional_client, optional_result, optional_limitation = self.run_fault(
            required=False
        )
        self.assertEqual("success", optional_result["terminal_outcome"])
        self.assertEqual("complete", optional_result["publication_outcome"])
        optional_status = optional_client.get(
            optional_result["status_url"], headers=auth()
        )
        self.assertIn("optional_source_failed", optional_status.text)
        optional_report = optional_client.get(
            "/demo/report", params=params(optional_result), headers=auth()
        )
        self.assertIn(optional_limitation, optional_report.text)
        self.assertTrue(optional_result["execution_id"])
        self.assertTrue(optional.step_executor.calls)

        required, required_client, required_result, required_limitation = self.run_fault(
            required=True
        )
        self.assertEqual("partial", required_result["terminal_outcome"])
        self.assertNotEqual("success", required_result["terminal_outcome"])
        required_status = required_client.get(
            required_result["status_url"], headers=auth()
        )
        self.assertIn("required_source_missing", required_status.text)
        self.assertIn("部分完成（partial）", required_status.text)
        required_report = required_client.get(
            "/demo/report", params=params(required_result), headers=auth()
        )
        self.assertIn(required_limitation, required_report.text)

    def test_no_verified_evidence_stops_reasoning_and_publication(self) -> None:
        app = create_local_demo_fastapi_app()
        composition = app.state.demo_composition
        composition.step_executor.configure(
            FormalRunStep.EXTRACTION,
            outcome=StepOutcome.FAILURE,
            safe_reason_code="no_verified_evidence",
            evidence_count=0,
            quarantined_count=6,
        )
        client = TestClient(app)
        response = client.post(
            "/demo/submit",
            json=payload("請分析 BTC，但將無法驗證的證據隔離。"),
            headers=auth(),
        )
        self.assertEqual(201, response.status_code, response.text)
        result = response.json()
        self.assertEqual("failed", result["state"])
        self.assertEqual("failed", result["terminal_outcome"])
        self.assertIsNone(result["publication_outcome"])
        self.assertNotIn(FormalRunStep.REASONING_BOUNDARY, composition.step_executor.calls)
        self.assertNotIn(FormalRunStep.ARTIFACT_PLACEHOLDER, composition.step_executor.calls)
        principal = composition.authenticator.authenticate(auth()["Authorization"])
        listed = composition.use_case.list_artifacts(
            principal=principal,
            task_id=result["task_id"],
            execution_id=result["execution_id"],
        )
        self.assertEqual((), listed.items)

    def test_900_second_deadline_optional_cutoff_and_timeout_stop_new_boundaries(self) -> None:
        near = CoreFixture(monotonic_ms=500_000)
        question = "請分析 BTC 市場狀況。"
        _task, _preflight, started, plan = near.create_preflight_start(
            question=question
        )
        self.assertEqual(
            900,
            (
                started.execution.absolute_deadline_at.as_datetime()
                - started.execution.started_at.as_datetime()
            ).total_seconds(),
        )
        near.clock.advance(wall_seconds=890, monotonic_ms=890_000)
        result = near.execute_formal(
            started, plan, question, "OP-T80-NEAR-DEADLINE"
        )
        self.assertEqual(TerminalOutcome.SUCCESS, result.terminal_outcome)
        self.assertIn("optional_source_failed", result.degraded_reason_codes)
        called_jobs = {
            request.planned_job_ids[0]
            for request in near.step_executor.requests
            if request.step is FormalRunStep.COLLECTION
            and request.planned_job_ids
        }
        optional_jobs = {
            job.job_id
            for job in plan.sourcing_jobs
            if job.requirement is RequirementLevel.OPTIONAL
        }
        self.assertTrue(optional_jobs)
        self.assertTrue(optional_jobs.isdisjoint(called_jobs))
        self.assertEqual("complete", result.publication_outcome)

        expired = CoreFixture(monotonic_ms=9_000_000)
        _task, _preflight, expired_start, expired_plan = expired.create_preflight_start(
            question=question
        )
        expired.clock.advance(wall_seconds=900, monotonic_ms=900_000)
        expired_result = expired.execute_formal(
            expired_start,
            expired_plan,
            question,
            "OP-T80-EXPIRED",
        )
        self.assertEqual(TerminalOutcome.TIMED_OUT, expired_result.terminal_outcome)
        self.assertEqual((), expired.step_executor.calls)
        self.assertIsNone(expired_result.publication_outcome)
        self.assertEqual([], expired.artifacts.writes)

        timed = CoreFixture()
        timed.step_executor.configure(
            FormalRunStep.INITIALIZATION,
            outcome=StepOutcome.SUCCESS,
            duration_ms=30_000,
        )
        _task, _preflight, timed_start, timed_plan = timed.create_preflight_start(
            question=question
        )
        timed_result = timed.execute_formal(
            timed_start, timed_plan, question, "OP-T80-STAGE-TIMEOUT"
        )
        self.assertEqual(TerminalOutcome.TIMED_OUT, timed_result.terminal_outcome)
        self.assertEqual((FormalRunStep.INITIALIZATION,), timed.step_executor.calls)
        self.assertEqual([], timed.artifacts.writes)
        self.assertEqual(FORMAL_RUN_HARD_DEADLINE_MS, 900_000)
        self.assertNotIn(
            "monotonic",
            timed.step_executor.requests[0].deadline.to_wire(),
        )

    def test_two_asset_order_and_bounded_parallel_jobs_preserve_first_priority(self) -> None:
        fixture = CoreFixture(concurrency_probe=True)
        question = "請比較 ETH 與 BTC。"
        assets = ("ETH", "BTC")
        _task, _preflight, started, plan = fixture.create_preflight_start(
            question=question,
            assets=assets,
        )
        result = fixture.execute_formal(
            started, plan, question, "OP-T80-DUAL-ASSET"
        )
        self.assertEqual(TerminalOutcome.SUCCESS, result.terminal_outcome)
        self.assertEqual(assets, plan.assets_requested_order)
        self.assertEqual(
            tuple(range(6)),
            tuple(job.priority for job in plan.sourcing_jobs if job.asset == "ETH"),
        )
        self.assertEqual(
            tuple(range(6, 12)),
            tuple(job.priority for job in plan.sourcing_jobs if job.asset == "BTC"),
        )
        self.assertGreaterEqual(fixture.executor.max_active, 2)
        self.assertLessEqual(fixture.executor.max_active, 8)
        called = {
            request.planned_job_ids[0]
            for request in fixture.step_executor.requests
            if request.step is FormalRunStep.COLLECTION
        }
        self.assertEqual({job.job_id for job in plan.sourcing_jobs}, called)


class ProviderBoundaryAndCitationE2ETests(unittest.TestCase):
    def test_extractor_v2_valid_invalid_authorized_repair_and_failure_quarantine(self) -> None:
        clock = FakeClock("2026-08-01T02:00:00Z")
        extractor = FakeEvidenceExtractorV2(clock)
        valid = extractor.extract(extract_request("OP-T80-EXT-VALID"))
        self.assertEqual("valid", valid.outcome)

        invalid_operation = "OP-T80-EXT-INVALID"
        extractor.configure_extract(invalid_operation, invalid_original_result())
        invalid = extractor.extract(extract_request(invalid_operation))
        self.assertEqual("invalid", invalid.outcome)

        repair = repair_request("OP-T80-EXT-REPAIR")
        repaired = extractor.repair(repair)
        self.assertEqual("valid", repaired.outcome)
        self.assertEqual(1, extractor.provider_invocation_count)
        self.assertEqual(repaired, extractor.repair(repair))
        self.assertEqual(1, extractor.provider_invocation_count)

        failing = FakeEvidenceExtractorV2(clock)
        failure_request = repair_request("OP-T80-EXT-REPAIR-FAIL")
        failing.configure_repair(
            failure_request.operation_id,
            "extractor_timeout",
        )
        failure = failing.repair(failure_request)
        self.assertIsInstance(failure, ErrorResultDTO)
        self.assertEqual("extractor_timeout", failure.error.code)
        self.assertEqual(1, failing.provider_invocation_count)
        self.assertNotIn("Nova", repr(failure.to_wire()))

    def test_market_model_timeout_and_unavailable_use_core_decimal_fallback(self) -> None:
        for code in ("market_regime_timeout", "endpoint_unavailable"):
            with self.subTest(code=code):
                clock = FakeClock("2026-08-01T02:00:00Z")
                provider = FakeMarketRegimeProvider(clock)
                operation_id = f"OP-T80-MARKET-{code.upper()}"
                provider.configure_infer(operation_id, code)
                request = InferRequestDTO(
                    operation_id,
                    "TASK-T80",
                    "EXEC-T80",
                    "BTC",
                    "2026-08-01T00:00:00Z",
                    FeatureWindowDTO("2026-07-03", "2026-08-01"),
                    (
                        FeatureDTO(
                            "return_14d",
                            "-0.0312",
                            "market-formulas-1.0.0",
                            ("DATASET:BTC",),
                        ),
                    ),
                    "sha256:" + "a" * 64,
                    ExpectedModelDTO("market-regime-xgboost", "1.0.0"),
                    DeadlineDTO(
                        "1.0.0",
                        operation_id,
                        "2026-08-01T02:00:25Z",
                        25_000,
                        "2026-08-01T02:00:00Z",
                        100,
                    ),
                )
                analysis = InferMarketRegimeUseCase(provider, clock).execute(
                    InferMarketRegimeCommand(
                        f"AN-{code.upper()}",
                        request,
                        ("DATASET:BTC",),
                        "bullish",
                        "market-formulas-1.0.0",
                    )
                )
                self.assertEqual(1, provider.invocation_count)
                self.assertEqual("deterministic_fallback", analysis.producer.kind.value)
                self.assertEqual("fallback", analysis.quality.status)
                self.assertIn(code, analysis.quality.limitations)
                self.assertTrue(
                    all(
                        isinstance(value, CanonicalDecimal)
                        for value in analysis.values.values()
                    )
                )
                self.assertEqual("1", str(analysis.values["bullish_probability"]))
                self.assertEqual("0", str(analysis.values["bearish_probability"]))
                self.assertEqual("0", str(analysis.values["sideways_probability"]))
                self.assertEqual("0", str(analysis.values["anomaly_score"]))
                self.assertFalse(
                    any(isinstance(value, float) for value in analysis.values.values())
                )

    def test_reasoning_primary_repair_fallback_invalid_and_hallucination_fail_closed(self) -> None:
        def runner(clock: FakeClock, provider: FakeReasoningProvider):
            counters = (
                ReasoningTokenCounter(
                    "primary", "fake-v1", "fake-tokenizer-1.0.0", len
                ),
                ReasoningTokenCounter(
                    "fallback", "fake-v1", "fake-tokenizer-1.0.0", len
                ),
            )
            return ReasoningSequenceRunner(provider, clock, token_counters=counters)

        primary = generate_request("OP-T80-RSN-PRIMARY")
        fallback = generate_request(
            "OP-T80-RSN-FALLBACK", model_role="fallback"
        )
        # Frozen slots DTOs are replaced without changing their contract fields.
        from dataclasses import replace
        fallback = replace(
            fallback,
            context=primary.context,
            context_hash=primary.context_hash,
        )
        repair_deadline = reasoning_deadline(
            "OP-T80-RSN-REPAIR", at="2026-08-01T02:01:00Z"
        )

        valid_clock = FakeClock("2026-08-01T02:00:00Z")
        valid_provider = FakeReasoningProvider(valid_clock)
        valid = runner(valid_clock, valid_provider).run(
            primary,
            "OP-T80-RSN-REPAIR",
            repair_deadline,
            fallback,
        )
        self.assertTrue(valid.publishable)
        self.assertEqual(("primary_generate",), valid.attempts)

        repair_clock = FakeClock("2026-08-01T02:00:00Z")
        repair_provider = FakeReasoningProvider(repair_clock)
        repair_provider.configure_generate(primary.operation_id, invalid_result())
        repaired = runner(repair_clock, repair_provider).run(
            primary,
            "OP-T80-RSN-REPAIR",
            repair_deadline,
            fallback,
        )
        self.assertTrue(repaired.publishable)
        self.assertEqual(("primary_generate", "primary_repair"), repaired.attempts)
        self.assertEqual(2, repair_provider.invocation_count)

        fallback_clock = FakeClock("2026-08-01T02:00:00Z")
        fallback_provider = FakeReasoningProvider(fallback_clock)
        fallback_provider.configure_generate(primary.operation_id, invalid_result())
        fallback_provider.configure_repair(
            "OP-T80-RSN-REPAIR", "reasoning_timeout"
        )
        degraded = runner(fallback_clock, fallback_provider).run(
            primary,
            "OP-T80-RSN-REPAIR",
            repair_deadline,
            fallback,
        )
        self.assertTrue(degraded.publishable)
        self.assertEqual(
            ("primary_generate", "primary_repair", "fallback_generate"),
            degraded.attempts,
        )

        invalid_clock = FakeClock("2026-08-01T02:00:00Z")
        invalid_provider = FakeReasoningProvider(invalid_clock)
        invalid_provider.configure_generate(primary.operation_id, invalid_result())
        invalid_provider.configure_repair(
            "OP-T80-RSN-REPAIR", "reasoning_timeout"
        )
        invalid_provider.configure_generate(
            fallback.operation_id,
            invalid_result(role="fallback"),
        )
        invalid_outcome = runner(invalid_clock, invalid_provider).run(
            primary,
            "OP-T80-RSN-REPAIR",
            repair_deadline,
            fallback,
        )
        self.assertFalse(invalid_outcome.publishable)
        self.assertEqual("invalid", invalid_outcome.result.outcome)

        hallucination_clock = FakeClock("2026-08-01T02:00:00Z")
        hallucination_provider = FakeReasoningProvider(hallucination_clock)
        now = hallucination_clock.current_utc()
        hallucinated = ReasoningResultDTO(
            "valid",
            ProviderDTO(
                "fake_reasoning_model", "fake-v1", "primary", "INV-T80-HALL"
            ),
            (
                FactDTO(
                    "FACT-T80-HALL",
                    "Hallucinated citation.",
                    ("EVID-NOT-IN-CONTEXT",),
                    (),
                ),
            ),
            (
                InferenceDTO(
                    "INFER-T80-HALL",
                    "Invalid inference.",
                    ("FACT-T80-HALL",),
                    "0.5",
                ),
            ),
            (
                ConclusionDTO(
                    "CONCL-T80-HALL",
                    "Invalid conclusion.",
                    ("FACT-T80-HALL",),
                    ("INFER-T80-HALL",),
                    "0.5",
                ),
            ),
            (),
            (),
            ConfidenceComponentsDTO("0.5", "0.5", "0.5", "0.5"),
            (),
            now,
            now,
        )
        hallucination_provider.configure_generate(
            primary.operation_id, hallucinated
        )
        hallucination = hallucination_provider.generate(primary)
        self.assertIsInstance(hallucination, ErrorResultDTO)
        self.assertEqual("citation_invalid", hallucination.error.code)

    def test_structured_context_retains_counter_and_rejects_quarantine_or_cross_scope(self) -> None:
        builder = StructuredReasoningContextBuilder(token_counters=(
            ReasoningTokenCounter(
                "primary", "primary-v1", "fake-tokenizer-1.0.0", lambda value: len(value) // 4
            ),
            ReasoningTokenCounter(
                "fallback", "fallback-v1", "fake-tokenizer-1.0.0", lambda value: len(value) // 4
            ),
        ))

        def evidence(index: int, *, status: ValidationStatus = ValidationStatus.ACTIVE, task_id: str = "TASK-T80") -> Evidence:
            text = f"可驗證的證據摘錄 {index}"
            return Evidence(
                f"EVID-T80-{index}",
                task_id,
                "EXEC-T80",
                f"RAW-T80-{index}",
                "T80 Official Source",
                SourceType.OFFICIAL,
                f"https://example.test/t80/{index}",
                None,
                "2026-08-01T01:00:00Z",
                ContentReference(
                    "quote", text, ContentOffset(0, len(text)), "unicode_scalar"
                ),
                f"urn:cryptotrust:t80:{index}",
                "sha256:" + f"{index}" * 64,
                "sha256:" + f"{index + 2}" * 64,
                QueryProvenance(
                    "official_collector",
                    "BTC official",
                    {"asset": "BTC"},
                    f"JOB-T80-{index}",
                ),
                status,
                "2026-08-01T01:01:00Z",
            )

        def assessment(evidence_id: str) -> EvidenceAssessment:
            return EvidenceAssessment(
                f"ASSESS-{evidence_id[5:]}",
                "TASK-T80",
                evidence_id,
                1,
                "1.0.0",
                "non-production-trust-1.0.0",
                "0.8",
                "0.8",
                "0.8",
                "0.7",
                f"GROUP-{evidence_id}",
                "0.8",
                "0.8",
                "low",
                "2026-08-01T02:00:00Z",
                (),
            )

        first, second = evidence(1), evidence(2)
        assessments = (assessment(first.evidence_id), assessment(second.evidence_id))
        analysis = AnalysisResult(
            "AN-T80-001",
            "TASK-T80",
            "EXEC-T80",
            "market_regime",
            "BTC",
            "2026-08-01T00:00:00Z",
            (first.evidence_id,),
            ("DATASET:BTC",),
            {"neutral_probability": "1"},
            AnalysisQuality("degraded", ("endpoint_unavailable",)),
            AnalysisProducer(
                ProducerKind.DETERMINISTIC_FALLBACK,
                "core-market-fallback",
                "1.0.0",
                "market-fallback-1.0.0",
            ),
            "2026-08-01T02:00:00Z",
        )
        contradiction = Contradiction(
            "CON-T80-001",
            "TASK-T80",
            "signal",
            first.evidence_id,
            second.evidence_id,
            "non-production-1.0.0",
        )
        ranking = {
            item.evidence_id: EvidenceRankingInput(
                item.evidence_id, "required", True
            )
            for item in (first, second)
        }
        context = builder.build(
            task_id="TASK-T80",
            question="BTC 的可驗證市場狀況為何？",
            evidence=(first, second),
            assessments=assessments,
            stances={first.evidence_id: "supports", second.evidence_id: "contradicts"},
            ranking_inputs=ranking,
            analyses=(analysis,),
            contradictions=(contradiction,),
            limitations=("本機 fake 資料限制。",),
        )
        self.assertEqual(
            {"supports", "contradicts"},
            {item.stance for item in context.evidence_refs},
        )
        self.assertEqual(
            (first.evidence_id, second.evidence_id),
            context.contradictions[0].evidence_refs,
        )
        self.assertIn("endpoint_unavailable", context.limitations)
        self.assertLessEqual(len(context.canonical_json()), 524_288)
        self.assertLessEqual(builder.token_count(context), 64_000)

        quarantined = evidence(1, status=ValidationStatus.QUARANTINED)
        with self.assertRaises(LineageViolation):
            builder.build(
                task_id="TASK-T80",
                question="BTC?",
                evidence=(quarantined,),
                assessments=(assessment(quarantined.evidence_id),),
                stances={quarantined.evidence_id: "supports"},
                ranking_inputs={
                    quarantined.evidence_id: EvidenceRankingInput(
                        quarantined.evidence_id, "required", True
                    )
                },
                analyses=(),
                contradictions=(),
                limitations=(),
            )
        with self.assertRaises(LineageViolation):
            builder.build(
                task_id="TASK-T80",
                question="BTC?",
                evidence=(evidence(1, task_id="TASK-OTHER"),),
                assessments=(assessment("EVID-T80-1"),),
                stances={"EVID-T80-1": "supports"},
                ranking_inputs={
                    "EVID-T80-1": EvidenceRankingInput(
                        "EVID-T80-1", "required", True
                    )
                },
                analyses=(),
                contradictions=(),
                limitations=(),
            )


class ArtifactAndChineseDemoE2ETests(unittest.TestCase):
    def test_full_bundle_manifest_last_hash_size_identity_and_redaction(self) -> None:
        fixture = CoreFixture()
        question = "請分析 BTC 市場狀況、信心與限制。"
        task, _preflight, started, plan = fixture.create_preflight_start(
            question=question
        )
        result = fixture.execute_formal(
            started, plan, question, "OP-T80-ARTIFACTS"
        )
        self.assertEqual(TerminalOutcome.SUCCESS, result.terminal_outcome)
        self.assertEqual("complete", result.publication_outcome)
        self.assertEqual(("put_manifest", "manifest", "json"), fixture.artifacts.writes[-1])
        self.assertEqual(7, len(fixture.artifacts.writes))

        list_operation = "OP-T80-ARTIFACT-LIST"
        listed = fixture.artifacts.list_for_execution(ArtifactExecutionRequestDTO(
            list_operation,
            task.task_id,
            started.execution.execution_id,
            repository_deadline(fixture.clock, list_operation),
        ))
        self.assertEqual(FULL_BUNDLE, {
            (item.artifact_type, item.format) for item in listed.items
        })

        all_bytes: dict[tuple[str, str], bytes] = {}
        for descriptor in listed.items:
            pair = (descriptor.artifact_type, descriptor.format)
            if pair == ("manifest", "json"):
                operation = "OP-T80-GET-MANIFEST"
                manifest = fixture.artifacts.get_manifest(
                    ArtifactExecutionRequestDTO(
                        operation,
                        task.task_id,
                        started.execution.execution_id,
                        repository_deadline(fixture.clock, operation),
                    )
                )
                self.assertNotIsInstance(manifest, ErrorResultDTO)
                content = json.dumps(
                    manifest.to_wire(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            else:
                operation = f"OP-T80-GET-{descriptor.artifact_id}"
                stored = fixture.artifacts.get(ArtifactKeyRequestDTO(
                    operation,
                    task.task_id,
                    started.execution.execution_id,
                    descriptor.artifact_type,
                    descriptor.format,
                    descriptor.content_schema_version,
                    repository_deadline(fixture.clock, operation),
                ))
                self.assertNotIsInstance(stored, ErrorResultDTO)
                self.assertEqual("inline", stored.delivery["kind"])
                content = base64.b64decode(
                    stored.delivery["content_base64"], validate=True
                )
            all_bytes[pair] = content
            self.assertEqual(descriptor.size_bytes, len(content))
            self.assertEqual(
                descriptor.sha256,
                "sha256:" + hashlib.sha256(content).hexdigest(),
            )
            self.assertEqual(task.task_id, descriptor.task_id)
            self.assertEqual(started.execution.execution_id, descriptor.execution_id)

        final_report = json.loads(all_bytes[("final_report", "json")])
        self.assertTrue(final_report["market_judgment"])
        self.assertTrue(final_report["facts"])
        self.assertTrue(final_report["confidence_components"])
        self.assertTrue(final_report["limitations"])
        evidence = json.loads(all_bytes[("evidence_list", "json")])
        self.assertTrue(evidence["items"])
        self.assertTrue(
            all(
                item["source"]
                and item["fetched_at"]
                and item["content_reference"]
                and item["lineage"]
                for item in evidence["items"]
            )
        )
        log_lines = [
            json.loads(line)
            for line in all_bytes[("execution_log", "jsonl")].splitlines()
        ]
        self.assertTrue(log_lines)
        self.assertTrue(
            all(item["timestamp"] and item["step"] for item in log_lines)
        )
        serialized = b"\n".join(all_bytes.values()).lower()
        for forbidden in (
            b"authorization:",
            b"bearer ",
            b"secret=",
            b"jwt",
            b'"raw_content":',
            b'"raw_prompt":',
        ):
            self.assertNotIn(forbidden, serialized)

    def test_chinese_demo_pages_downloads_ownership_headers_and_escaping(self) -> None:
        app = create_local_demo_fastapi_app()
        client = TestClient(app)
        question = '請分析 BTC <script>alert("t80")</script> 的市場狀況。'
        response = client.post(
            "/demo/submit", json=payload(question), headers=auth()
        )
        self.assertEqual(201, response.status_code, response.text)
        result = response.json()
        identity = params(result)

        status = client.get(result["status_url"], headers=auth())
        self.assertEqual(200, status.status_code)
        self.assertIn('<html lang="zh-Hant">', status.text)
        self.assertIn("正式分析", status.text)
        self.assertIn("完整（complete）", status.text)
        for header, marker in (
            ("content-security-policy", "default-src 'none'"),
            ("cache-control", "no-store"),
            ("x-content-type-options", "nosniff"),
        ):
            self.assertIn(marker, status.headers[header])

        for action, marker in (
            ("report", "最終分析報告"),
            ("evidence", "證據清單"),
            ("log", "執行紀錄"),
            ("manifest", "成果清冊"),
        ):
            page = client.get(
                f"/demo/{action}", params=identity, headers=auth()
            )
            self.assertEqual(200, page.status_code, page.text)
            self.assertIn(marker, page.text)
            self.assertIn('<html lang="zh-Hant">', page.text)
            self.assertEqual("no-store", page.headers["cache-control"])
            if action == "report":
                self.assertNotIn('<script>alert("t80")</script>', page.text)
                self.assertIn("&lt;script&gt;", page.text)

        for artifact_type, artifact_format in MINIMUM_BUNDLE:
            download = client.get(
                "/demo/download",
                params={
                    **identity,
                    "artifact_type": artifact_type,
                    "format": artifact_format,
                },
                headers=auth(),
            )
            self.assertEqual(200, download.status_code, download.text)
            self.assertTrue(download.content)

        for path in ("status", "report", "evidence", "log", "manifest"):
            denied = client.get(
                f"/demo/{path}",
                params=identity,
                headers=auth(DEMO_OTHER_USER_TOKEN),
            )
            self.assertEqual(404, denied.status_code)
        self.assertEqual(
            422,
            client.post(
                "/demo/submit",
                json={**payload("請分析 BTC。"), "sub": "attacker"},
                headers=auth(),
            ).status_code,
        )
        self.assertEqual(
            422,
            client.get(
                "/demo/status",
                params={**identity, "sub": "attacker"},
                headers=auth(),
            ).status_code,
        )
        self.assertEqual(
            422,
            client.post(
                "/demo/submit",
                json=payload("請分析 BTC。"),
                headers={**auth(), "X-User-Id": "attacker"},
            ).status_code,
        )


if __name__ == "__main__":
    unittest.main()
