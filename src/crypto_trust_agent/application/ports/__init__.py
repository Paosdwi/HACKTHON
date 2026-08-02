"""Application-owned Port interfaces."""

from crypto_trust_agent.application.dto.source_collector import CollectorHealthCheckRequestDTO
from crypto_trust_agent.application.ports.evidence_extractor import EvidenceExtractor
from crypto_trust_agent.application.ports.evidence_extractor_v2 import (
    EvidenceExtractorV2,
    negotiate_evidence_extractor_version,
)
from crypto_trust_agent.application.ports.evidence_strategies import (
    ConfidenceCompositionStrategy,
    ContradictionDetectionStrategy,
    DuplicateDetectionStrategy,
    IndependenceGroupingStrategy,
    TrustComponentStrategy,
)
from crypto_trust_agent.application.ports.market_regime import MarketRegimeProvider
from crypto_trust_agent.application.ports.live_market import LiveMarketDataProvider
from crypto_trust_agent.application.ports.preflight import (
    HealthCheckPort,
    HealthCheckRequestDTO,
    ProviderHealthDTO,
    ReasoningHealthCheckRequestDTO,
    TaskReadinessDTO,
    TaskReadinessProbe,
    TaskReadinessRequestDTO,
)
from crypto_trust_agent.application.ports.reasoning import ReasoningProvider
from crypto_trust_agent.application.ports.repositories import (
    ArtifactRepository,
    Clock,
    EventPublisher,
    EvidenceRepository,
    ExecutionRepository,
    TaskRepository,
)
from crypto_trust_agent.application.ports.source_collector import SourceCollector

__all__ = (
    "ArtifactRepository",
    "Clock",
    "CollectorHealthCheckRequestDTO",
    "ConfidenceCompositionStrategy",
    "ContradictionDetectionStrategy",
    "DuplicateDetectionStrategy",
    "EventPublisher",
    "EvidenceExtractor",
    "EvidenceExtractorV2",
    "EvidenceRepository",
    "ExecutionRepository",
    "HealthCheckPort",
    "HealthCheckRequestDTO",
    "IndependenceGroupingStrategy",
    "MarketRegimeProvider",
    "LiveMarketDataProvider",
    "ProviderHealthDTO",
    "ReasoningProvider",
    "ReasoningHealthCheckRequestDTO",
    "SourceCollector",
    "TaskReadinessDTO",
    "TaskReadinessProbe",
    "TaskReadinessRequestDTO",
    "TaskRepository",
    "TrustComponentStrategy",
    "negotiate_evidence_extractor_version",
)
