"""SageMaker-backed MarketRegimeProvider infrastructure adapter."""

from crypto_trust_agent.infrastructure.market_regime.adapter import (
    CONTRACT_VERSION,
    PROVIDER_VERSION,
    FeatureSchema,
    ProviderFailure,
    SageMakerMarketRegimeProvider,
    StubSageMakerClient,
)

__all__ = (
    "CONTRACT_VERSION",
    "PROVIDER_VERSION",
    "FeatureSchema",
    "ProviderFailure",
    "SageMakerMarketRegimeProvider",
    "StubSageMakerClient",
)
