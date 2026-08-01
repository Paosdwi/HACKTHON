"""Core-owned, deterministic, non-production fake adapters."""

from crypto_trust_agent.infrastructure.fakes.artifacts import FakeArtifactRepository
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock
from crypto_trust_agent.infrastructure.fakes.events import FakeEventPublisher
from crypto_trust_agent.infrastructure.fakes.evidence_extractor import FakeEvidenceExtractor
from crypto_trust_agent.infrastructure.fakes.preflight import (
    FakeProviderHealthProbe,
    FakeTaskReadinessProbe,
)
from crypto_trust_agent.infrastructure.fakes.repositories import (
    FakeEvidenceRepository,
    FakeExecutionRepository,
    FakePlatformStore,
    FakeTaskRepository,
)
from crypto_trust_agent.infrastructure.fakes.source_collector import FakeSourceCollector

__all__ = (
    "FakeArtifactRepository",
    "FakeClock",
    "FakeEventPublisher",
    "FakeEvidenceExtractor",
    "FakeEvidenceRepository",
    "FakeExecutionRepository",
    "FakePlatformStore",
    "FakeProviderHealthProbe",
    "FakeSourceCollector",
    "FakeTaskReadinessProbe",
    "FakeTaskRepository",
)
