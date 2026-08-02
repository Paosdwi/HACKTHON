"""Core-owned verified identity boundary; no network adapter is provided."""

from crypto_trust_agent.infrastructure.identity.principal import (
    AuthenticatedPrincipal,
    CognitoPrincipalBoundary,
    CognitoTokenVerifier,
    FakeCognitoTokenVerifier,
    HmacPrincipalPseudonymizer,
    IdentityAuthenticationError,
)

__all__ = (
    "AuthenticatedPrincipal",
    "CognitoPrincipalBoundary",
    "CognitoTokenVerifier",
    "FakeCognitoTokenVerifier",
    "HmacPrincipalPseudonymizer",
    "IdentityAuthenticationError",
)
