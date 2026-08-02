"""Local-only composition root for the Demo UI.

This is the sole Demo module that wires concrete fake adapters.  It performs no
network I/O and is not a production deployment configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from crypto_trust_agent.application.orchestration.artifact_assembler import (
    FormalRunArtifactAssembler,
)
from crypto_trust_agent.application.orchestration.formal_run import FormalRunOrchestrator
from crypto_trust_agent.application.planning import QuestionType
from crypto_trust_agent.application.publication import ArtifactPublicationService
from crypto_trust_agent.application.use_cases.create_task import CreateTaskUseCase
from crypto_trust_agent.application.use_cases.demo_ui import DemoUseCase
from crypto_trust_agent.application.use_cases.preflight import PreflightUseCase
from crypto_trust_agent.application.use_cases.start_formal_execution import (
    StartFormalExecutionUseCase,
)
from crypto_trust_agent.infrastructure.fakes import (
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
from crypto_trust_agent.infrastructure.identity import (
    CognitoPrincipalBoundary,
    FakeCognitoTokenVerifier,
    HmacPrincipalPseudonymizer,
)

DEMO_USER_TOKEN = "local-demo-user"
DEMO_OTHER_USER_TOKEN = "local-demo-other-user"
DEMO_COOKIE_NAME = "crypto_trust_demo_session"
_DEMO_ISSUER = "https://local.demo.invalid/cognito"
_DEMO_AUDIENCE = "crypto-trust-local-demo"
_DEMO_HMAC_KEY = b"local-demo-pseudonym-key-32-bytes!"


class LocalIdentifiers:
    """Thread-safe deterministic identifiers for one local app instance."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._value = 0

    def __call__(self, prefix: str) -> str:
        with self._lock:
            self._value += 1
            return f"{prefix}{self._value:08X}"


class LocalAuditRecorder:
    """Bounded local audit sink containing only TaskCreationAuditRecord values."""

    def __init__(self, limit: int = 128) -> None:
        self._limit = limit
        self._lock = Lock()
        self._records: list[object] = []

    def __call__(self, record: object) -> None:
        with self._lock:
            self._records.append(record)
            del self._records[:-self._limit]

    @property
    def records(self) -> tuple[object, ...]:
        with self._lock:
            return tuple(self._records)


@dataclass(frozen=True, slots=True)
class LocalDemoComposition:
    use_case: DemoUseCase
    authenticator: CognitoPrincipalBoundary
    clock: FakeClock
    store: FakePlatformStore
    artifact_repository: FakeArtifactRepository
    task_repository: FakeTaskRepository
    execution_repository: FakeExecutionRepository
    task_readiness: FakeTaskReadinessProbe
    provider_probes: tuple[FakeProviderHealthProbe, ...]
    step_executor: FakeFormalRunStepExecutor
    event_publisher: FakeEventPublisher
    audit_recorder: LocalAuditRecorder


def classify_demo_question(question: str, assets: tuple[str, ...]) -> QuestionType:
    """Deterministic local classifier; it never calls a model or provider."""

    if len(assets) >= 2:
        return QuestionType.ASSET_COMPARISON
    normalized = question.casefold()
    if normalized.startswith("validate") or "hypothesis" in normalized:
        return QuestionType.HYPOTHESIS_VALIDATION
    return QuestionType.MARKET_STATUS


def build_local_demo_composition(
    *,
    dataset_status: str = "healthy",
    dataset_reason_code: str | None = None,
) -> LocalDemoComposition:
    """Build one isolated, deterministic, no-network Demo system."""

    clock = FakeClock("2026-08-01T02:00:00Z")
    store = FakePlatformStore()
    identifiers = LocalIdentifiers()
    audit = LocalAuditRecorder()
    tasks = FakeTaskRepository(store, clock)
    executions = FakeExecutionRepository(store, clock)
    artifacts = FakeArtifactRepository(clock)
    readiness = FakeTaskReadinessProbe(
        store,
        clock,
        dataset_status=dataset_status,
        dataset_reason_code=dataset_reason_code,
    )
    probes = (
        FakeProviderHealthProbe(clock, "nova", "extraction_v1"),
        FakeProviderHealthProbe(clock, "opus", "reasoning_v1"),
        FakeProviderHealthProbe(clock, "sagemaker", "market_regime_v1"),
        FakeProviderHealthProbe(clock, "external_allowlist", "collector_policy_v1"),
    )
    step_executor = FakeFormalRunStepExecutor(clock, populate_pipeline=True)
    events = FakeEventPublisher(clock)
    publication = ArtifactPublicationService(artifacts, clock)
    orchestrator = FormalRunOrchestrator(
        clock,
        step_executor,
        events,
        executions,
        artifact_assembler=FormalRunArtifactAssembler(),
        artifact_publication=publication,
        artifact_repository=artifacts,
    )
    create_task = CreateTaskUseCase(
        tasks,
        clock,
        classify_demo_question,
        identifiers,
        audit,
    )
    preflight = PreflightUseCase(
        tasks,
        clock,
        readiness,
        *probes,
        identifiers,
    )
    start_execution = StartFormalExecutionUseCase(
        tasks,
        executions,
        clock,
        identifiers,
    )
    use_case = DemoUseCase(
        create_task=create_task,
        preflight=preflight,
        start_execution=start_execution,
        orchestrator=orchestrator,
        artifact_repository=artifacts,
        clock=clock,
        identifier_factory=identifiers,
        question_classifier=classify_demo_question,
    )

    expires_at = 4_102_444_800
    common = {
        "iss": _DEMO_ISSUER,
        "aud": _DEMO_AUDIENCE,
        "exp": expires_at,
        "cognito:groups": ["Users"],
    }
    authenticator = CognitoPrincipalBoundary(
        FakeCognitoTokenVerifier(
            {
                DEMO_USER_TOKEN: {**common, "sub": "local-demo-user-subject"},
                DEMO_OTHER_USER_TOKEN: {
                    **common,
                    "sub": "local-demo-other-user-subject",
                },
            }
        ),
        _DEMO_ISSUER,
        _DEMO_AUDIENCE,
        HmacPrincipalPseudonymizer("local-v1", _DEMO_HMAC_KEY),
        now_provider=lambda: clock.current_utc().as_datetime(),
    )
    return LocalDemoComposition(
        use_case=use_case,
        authenticator=authenticator,
        clock=clock,
        store=store,
        artifact_repository=artifacts,
        task_repository=tasks,
        execution_repository=executions,
        task_readiness=readiness,
        provider_probes=probes,
        step_executor=step_executor,
        event_publisher=events,
        audit_recorder=audit,
    )


__all__ = (
    "DEMO_COOKIE_NAME",
    "DEMO_OTHER_USER_TOKEN",
    "DEMO_USER_TOKEN",
    "LocalDemoComposition",
    "build_local_demo_composition",
    "classify_demo_question",
)
