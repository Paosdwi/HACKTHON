"""Provider-owned evidence extraction adapters."""

from crypto_trust_agent.infrastructure.extraction.adapter import (
    CONTRACT_VERSION,
    PROVIDER_VERSION,
    NovaClient,
    NovaLiteEvidenceExtractor,
    ProviderFailure,
    StubNovaClient,
)

from crypto_trust_agent.infrastructure.extraction.adapter_v2 import (
    AuthoritativeContentResolver,
    CONTRACT_VERSION_V2,
    DEFAULT_MODEL_VERSION_V2,
    NovaLiteEvidenceExtractorV2,
    PROVIDER_VERSION_V2,
)

__all__ = (
    "AuthoritativeContentResolver",
    "CONTRACT_VERSION",
    "CONTRACT_VERSION_V2",
    "DEFAULT_MODEL_VERSION_V2",
    "PROVIDER_VERSION",
    "PROVIDER_VERSION_V2",
    "NovaClient",
    "NovaLiteEvidenceExtractor",
    "NovaLiteEvidenceExtractorV2",
    "ProviderFailure",
    "StubNovaClient",
)
