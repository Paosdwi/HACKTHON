"""Production-safe disabled MarketRegimeProvider boundary.

This adapter performs no model or network I/O.  It makes the intentional
absence of PA72 explicit so Core can apply its versioned deterministic market
fallback without inventing provider probabilities.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock

from crypto_trust_agent.application.dto.common import (
    DeadlineExceededError,
    ErrorResultDTO,
    PortErrorCategory,
    PortErrorDTO,
    build_local_deadline,
)
from crypto_trust_agent.application.dto.market_regime import (
    HealthCheckRequestDTO,
    InferRequestDTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO

CONTRACT_VERSION = "1.0.0"
PROVIDER_VERSION = "disabled-market-regime-adapter-1.0.0"


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class DisabledMarketRegimeProvider:
    """Fail-closed PA72 boundary that deliberately selects Core fallback."""

    contract_version = CONTRACT_VERSION
    provider_version = PROVIDER_VERSION
    service_version = "no-external-market-regime-service-1.0.0"
    max_attempts = 1
    hidden_retries = 0
    non_production = False

    def __init__(
        self,
        *,
        now_utc: Callable[[], datetime] | None = None,
        monotonic_ms: Callable[[], int] | None = None,
        runtime_id: str = "disabled-market-regime-runtime",
    ) -> None:
        self._now = now_utc or (lambda: datetime.now(UTC))
        self._monotonic_ms = monotonic_ms or (lambda: time.monotonic_ns() // 1_000_000)
        self._runtime_id = runtime_id
        self._completed: dict[str, tuple[InferRequestDTO, ErrorResultDTO]] = {}
        self._lock = RLock()
        self.invocation_count = 0

    def _error(self, operation_id: str, code: str) -> ErrorResultDTO:
        if code == "deadline_exceeded":
            category = PortErrorCategory.TIMEOUT
        elif code == "endpoint_unavailable":
            category = PortErrorCategory.UNAVAILABLE
        else:
            category = PortErrorCategory.VALIDATION
        return ErrorResultDTO(
            PortErrorDTO(
                schema_version=CONTRACT_VERSION,
                code=code,
                category=category,
                retryable=False,
                safe_message="Market regime model is disabled; Core fallback is required.",
                provider="market_regime_provider",
                operation_id=operation_id,
                details={},
                occurred_at=_utc_text(self._now()),
            )
        )

    def _deadline_expired(
        self, request: InferRequestDTO | HealthCheckRequestDTO, timeout_ms: int
    ) -> bool:
        try:
            build_local_deadline(
                request.deadline,
                provider_timeout_ms=timeout_ms,
                now_utc=self._now(),
                now_monotonic_ms=self._monotonic_ms(),
                runtime_id=self._runtime_id,
            )
        except DeadlineExceededError:
            return True
        return False

    def infer(self, request: InferRequestDTO) -> ErrorResultDTO:
        """Return an unavailable result without calling a model or fabricating values."""

        with self._lock:
            completed = self._completed.get(request.operation_id)
            if completed is not None:
                if completed[0] == request:
                    return completed[1]
                return self._error(request.operation_id, "invalid_feature_schema")

            code = (
                "deadline_exceeded"
                if self._deadline_expired(request, 25_000)
                else "endpoint_unavailable"
            )
            result = self._error(request.operation_id, code)
            self._completed[request.operation_id] = (request, result)
            return result

    def health_check(
        self, request: HealthCheckRequestDTO
    ) -> ProviderHealthDTO | ErrorResultDTO:
        if self._deadline_expired(request, 3_000):
            return self._error(request.operation_id, "deadline_exceeded")
        return ProviderHealthDTO(
            provider="market_regime_provider",
            capability="market_regime_inference",
            status="degraded",
            checked_at=_utc_text(self._now()),
            latency_ms=0,
            safe_reason_code="market_regime_provider_disabled",
            expires_at=None,
        )


__all__ = (
    "CONTRACT_VERSION",
    "PROVIDER_VERSION",
    "DisabledMarketRegimeProvider",
)
