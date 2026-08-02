"""MarketRegimeProvider infrastructure adapters."""

from crypto_trust_agent.infrastructure.market_regime.adapter import (
    CONTRACT_VERSION,
    PROVIDER_VERSION,
    FeatureSchema,
    ProviderFailure,
    SageMakerMarketRegimeProvider,
    StubSageMakerClient,
)
from crypto_trust_agent.infrastructure.market_regime.disabled import (
    DisabledMarketRegimeProvider,
)

__all__ = (
    "CONTRACT_VERSION",
    "DisabledMarketRegimeProvider",
    "PROVIDER_VERSION",
    "FeatureSchema",
    "ProviderFailure",
    "SageMakerMarketRegimeProvider",
    "StubSageMakerClient",
)
