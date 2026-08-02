"""Verified Cognito-style principal boundary with injectable token verification."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Callable, Mapping, Protocol

_KEY_VERSION = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


class IdentityAuthenticationError(ValueError):
    """Authentication failed; callers must expose only a generic 401 response."""


class CognitoTokenVerifier(Protocol):
    """Verifies token signature before returning claims; implementations may be injected."""

    def verify(self, token: str) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    subject: str
    pseudonym: str
    is_admin: bool


class HmacPrincipalPseudonymizer:
    def __init__(self, key_version: str, key: bytes) -> None:
        if not _KEY_VERSION.fullmatch(key_version) or not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("invalid pseudonym key configuration")
        self._version = key_version
        self._key = key

    def pseudonymize(self, subject: str) -> str:
        digest = hmac.new(self._key, subject.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"hmac-sha256:{self._version}:{digest}"


class CognitoPrincipalBoundary:
    """Creates principals only after signature, issuer, audience, expiry, and sub checks."""

    def __init__(
        self,
        verifier: CognitoTokenVerifier,
        expected_issuer: str,
        expected_audience: str,
        pseudonymizer: HmacPrincipalPseudonymizer,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        if not expected_issuer.startswith("https://") or not expected_audience:
            raise ValueError("invalid Cognito identity configuration")
        self._verifier = verifier
        self._issuer = expected_issuer
        self._audience = expected_audience
        self._pseudonymizer = pseudonymizer
        self._now = now_provider or (lambda: datetime.now(UTC))

    def authenticate(self, authorization_header: str | None) -> AuthenticatedPrincipal:
        if not isinstance(authorization_header, str) or not authorization_header.startswith("Bearer "):
            raise IdentityAuthenticationError("authentication failed")
        token = authorization_header[7:]
        if not token or token != token.strip():
            raise IdentityAuthenticationError("authentication failed")
        try:
            claims = dict(self._verifier.verify(token))
            self._validate_registered_claims(claims)
            subject = claims["sub"]
            groups_value = claims.get("cognito:groups", ())
            if not isinstance(groups_value, (list, tuple)) or any(not isinstance(item, str) for item in groups_value):
                raise IdentityAuthenticationError("authentication failed")
            return AuthenticatedPrincipal(
                subject=subject,
                pseudonym=self._pseudonymizer.pseudonymize(subject),
                is_admin="CryptoTrustAdmins" in groups_value,
            )
        except IdentityAuthenticationError:
            raise
        except Exception as error:
            raise IdentityAuthenticationError("authentication failed") from error

    def _validate_registered_claims(self, claims: Mapping[str, object]) -> None:
        subject = claims.get("sub")
        issuer = claims.get("iss")
        audience = claims.get("aud")
        expires_at = claims.get("exp")
        if not isinstance(subject, str) or not subject or subject != subject.strip() or len(subject) > 128:
            raise IdentityAuthenticationError("authentication failed")
        if issuer != self._issuer:
            raise IdentityAuthenticationError("authentication failed")
        audience_matches = audience == self._audience or (
            isinstance(audience, (list, tuple))
            and self._audience in audience
            and all(isinstance(item, str) for item in audience)
        )
        if not audience_matches:
            raise IdentityAuthenticationError("authentication failed")
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise RuntimeError("identity clock must be timezone-aware")
        if type(expires_at) is not int or expires_at <= int(now.timestamp()):
            raise IdentityAuthenticationError("authentication failed")


class FakeCognitoTokenVerifier:
    """Local-only verifier fake; accepted tokens stand in for signature verification."""

    non_production = True

    def __init__(
        self,
        accepted_tokens: Mapping[str, Mapping[str, object]],
        *,
        rejected_tokens: tuple[str, ...] = (),
    ) -> None:
        self._accepted = MappingProxyType(
            {token: MappingProxyType(dict(claims)) for token, claims in accepted_tokens.items()}
        )
        self._rejected = frozenset(rejected_tokens)

    def verify(self, token: str) -> Mapping[str, object]:
        if token in self._rejected or token not in self._accepted:
            raise IdentityAuthenticationError("authentication failed")
        return self._accepted[token]


__all__ = (
    "AuthenticatedPrincipal",
    "CognitoPrincipalBoundary",
    "CognitoTokenVerifier",
    "FakeCognitoTokenVerifier",
    "HmacPrincipalPseudonymizer",
    "IdentityAuthenticationError",
)
