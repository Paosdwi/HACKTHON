"""AWS hackathon demo composition with live public data and Bedrock Claude."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from crypto_trust_agent.application.orchestration.artifact_assembler import FormalRunArtifactAssembler
from crypto_trust_agent.application.orchestration.formal_run import FormalRunOrchestrator
from crypto_trust_agent.application.publication import ArtifactPublicationService
from crypto_trust_agent.application.use_cases.create_task import CreateTaskUseCase
from crypto_trust_agent.application.use_cases.demo_ui import DemoUseCase
from crypto_trust_agent.application.use_cases.preflight import PreflightUseCase
from crypto_trust_agent.application.use_cases.start_formal_execution import StartFormalExecutionUseCase
from crypto_trust_agent.infrastructure.aws.bedrock_demo import DemoBedrockReasoningClient
from crypto_trust_agent.infrastructure.collectors.binance_live_market import BinanceLiveMarketDataProvider
from crypto_trust_agent.infrastructure.collectors.binance_us_demo import BinanceUsDemoMarketCollector
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
from crypto_trust_agent.infrastructure.reasoning.adapter import BedrockReasoningProvider, ReasoningClient
from crypto_trust_agent.infrastructure.reasoning.demo_executor import AwsDemoFormalRunStepExecutor
from crypto_trust_agent.presentation.api.demo_ui_composition import (
    DEMO_USER_TOKEN,
    LocalAuditRecorder,
    LocalIdentifiers,
    classify_demo_question,
)


_ISSUER = "https://demo.cryptotrust.invalid/cognito"
_AUDIENCE = "crypto-trust-aws-demo"


@dataclass(frozen=True, slots=True)
class AwsDemoComposition:
    use_case: DemoUseCase
    authenticator: CognitoPrincipalBoundary
    clock: FakeClock
    artifact_repository: FakeArtifactRepository
    step_executor: AwsDemoFormalRunStepExecutor


def build_aws_demo_composition(
    *,
    reasoning_client: ReasoningClient | None = None,
    market_provider=None,
    news_collector=None,
) -> AwsDemoComposition:
    """Build the shortest deployable demo; AWS credentials come from the task role."""

    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    clock = FakeClock(now)
    store = FakePlatformStore()
    identifiers = LocalIdentifiers()
    audit = LocalAuditRecorder()
    tasks = FakeTaskRepository(store, clock)
    executions = FakeExecutionRepository(store, clock)
    artifacts = FakeArtifactRepository(clock)
    readiness = FakeTaskReadinessProbe(store, clock)
    probes = (
        FakeProviderHealthProbe(clock, "public_news", "rss_demo_v1"),
        FakeProviderHealthProbe(clock, "claude", "bedrock_converse_demo_v1"),
        FakeProviderHealthProbe(clock, "binance", "daily_ohlcv_v1"),
        FakeProviderHealthProbe(clock, "external_allowlist", "fixed_host_demo_v1"),
    )

    live_client = reasoning_client or DemoBedrockReasoningClient.from_environment()
    model_id = os.getenv("CRYPTOTRUST_BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")
    reasoning = BedrockReasoningProvider(
        live_client,
        provider_name="amazon_bedrock",
        primary_model_version=model_id,
        fallback_model_version=model_id,
    )
    delegate = FakeFormalRunStepExecutor(clock, populate_pipeline=True)
    step_executor = AwsDemoFormalRunStepExecutor(
        delegate,
        clock=clock,
        reasoning_provider=reasoning,
        market_provider=market_provider or BinanceLiveMarketDataProvider.production(),
        news_collector=news_collector,
        market_fallback=BinanceUsDemoMarketCollector(),
    )
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
    use_case = DemoUseCase(
        create_task=CreateTaskUseCase(tasks, clock, classify_demo_question, identifiers, audit),
        preflight=PreflightUseCase(tasks, clock, readiness, *probes, identifiers),
        start_execution=StartFormalExecutionUseCase(tasks, executions, clock, identifiers),
        orchestrator=orchestrator,
        artifact_repository=artifacts,
        clock=clock,
        identifier_factory=identifiers,
        question_classifier=classify_demo_question,
    )

    claims = {
        DEMO_USER_TOKEN: {
            "iss": _ISSUER,
            "aud": _AUDIENCE,
            "exp": 4_102_444_800,
            "sub": "public-hackathon-demo",
            "cognito:groups": ["Users"],
        }
    }
    authenticator = CognitoPrincipalBoundary(
        FakeCognitoTokenVerifier(claims),
        _ISSUER,
        _AUDIENCE,
        HmacPrincipalPseudonymizer("demo-v1", secrets.token_bytes(32)),
        now_provider=lambda: clock.current_utc().as_datetime(),
    )
    return AwsDemoComposition(use_case, authenticator, clock, artifacts, step_executor)


__all__ = ("AwsDemoComposition", "build_aws_demo_composition")
