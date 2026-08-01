"""Application-owned Port interfaces."""

from crypto_trust_agent.application.dto.source_collector import CollectorHealthCheckRequestDTO
from crypto_trust_agent.application.ports.evidence_extractor import EvidenceExtractor
from crypto_trust_agent.application.ports.preflight import (
    HealthCheckPort,
    HealthCheckRequestDTO,
    ProviderHealthDTO,
    ReasoningHealthCheckRequestDTO,
    TaskReadinessDTO,
    TaskReadinessProbe,
    TaskReadinessRequestDTO,
)
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
    "EventPublisher",
    "EvidenceExtractor",
    "EvidenceRepository",
    "ExecutionRepository",
    "HealthCheckPort",
    "HealthCheckRequestDTO",
    "ProviderHealthDTO",
    "ReasoningHealthCheckRequestDTO",
    "SourceCollector",
    "TaskReadinessDTO",
    "TaskReadinessProbe",
    "TaskReadinessRequestDTO",
    "TaskRepository",
)
