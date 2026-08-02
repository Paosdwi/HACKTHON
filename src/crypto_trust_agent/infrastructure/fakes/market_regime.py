"""Deterministic non-production MarketRegimeProvider fake."""

from __future__ import annotations

from threading import RLock

from crypto_trust_agent.application.dto.common import (
    DeadlineExceededError,
    ErrorResultDTO,
    PortErrorCategory,
    PortErrorDTO,
    build_local_deadline,
    map_unexpected_exception,
)
from crypto_trust_agent.application.dto.market_regime import (
    HEALTH_ERROR_CODES,
    INFER_ERROR_CODES,
    HealthCheckRequestDTO,
    InferRequestDTO,
    MarketRegimeResultDTO,
    ModelDTO,
    ProbabilityDistributionDTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock


def _category(code: str) -> PortErrorCategory:
    if code in {"market_regime_timeout", "deadline_exceeded"}:
        return PortErrorCategory.TIMEOUT
    if code == "provider_rate_limited":
        return PortErrorCategory.RATE_LIMITED
    if code == "endpoint_unavailable":
        return PortErrorCategory.UNAVAILABLE
    if code in {"invalid_probability_distribution", "invalid_provider_output"}:
        return PortErrorCategory.INVALID_PROVIDER_OUTPUT
    return PortErrorCategory.VALIDATION


class FakeMarketRegimeProvider:
    """Scenario fake with one attempt, no hidden retry, network, AWS, or fallback."""

    non_production = True

    def __init__(
        self,
        clock: FakeClock,
        *,
        model_name: str = "market-regime-xgboost",
        model_version: str = "1.0.0",
    ) -> None:
        self._clock = clock
        self._model_name = model_name
        self._model_version = model_version
        self._scenarios: dict[str, object] = {}
        self._completed: dict[str, tuple[InferRequestDTO, MarketRegimeResultDTO | ErrorResultDTO]] = {}
        self._lock = RLock()
        self.invocation_count = 0

    def configure_infer(self, operation_id: str, response: object) -> None:
        with self._lock:
            self._scenarios[operation_id] = response

    def _error(self, operation_id: str, code: str) -> ErrorResultDTO:
        return ErrorResultDTO(PortErrorDTO(
            "1.0.0",
            code,
            _category(code),
            False,
            "Market regime request failed.",
            "market_regime_provider",
            operation_id,
            {},
            self._clock.current_utc(),
        ))

    def _deadline_error(self, request: object, timeout_ms: int) -> ErrorResultDTO | None:
        try:
            build_local_deadline(
                request.deadline,
                provider_timeout_ms=timeout_ms,
                now_utc=self._clock.current_utc().as_datetime(),
                now_monotonic_ms=self._clock.current_monotonic_ms(),
                runtime_id=self._clock.runtime_id,
            )
        except DeadlineExceededError:
            return self._error(request.operation_id, "deadline_exceeded")
        return None

    def _default(self, request: InferRequestDTO) -> MarketRegimeResultDTO:
        return MarketRegimeResultDTO(
            request.operation_id,
            request.asset,
            request.as_of,
            ModelDTO(self._model_name, self._model_version, f"INV-{request.operation_id[3:]}"),
            ProbabilityDistributionDTO("0.4", "0.2", "0.4"),
            "0.1",
            request.feature_window,
            request.input_feature_hash,
            self._clock.current_utc(),
            "valid",
            (),
        )

    @staticmethod
    def _valid_semantics(request: InferRequestDTO, result: MarketRegimeResultDTO) -> bool:
        return (
            result.operation_id == request.operation_id
            and result.asset == request.asset
            and result.as_of == request.as_of
            and result.feature_window == request.feature_window
            and result.input_feature_hash == request.input_feature_hash
            and result.model.name == request.expected_model.name
        )

    def infer(self, request: InferRequestDTO) -> MarketRegimeResultDTO | ErrorResultDTO:
        with self._lock:
            completed = self._completed.get(request.operation_id)
            if completed is not None:
                if completed[0] == request:
                    return completed[1]
                return self._error(request.operation_id, "invalid_feature_schema")
            expired = self._deadline_error(request, 25_000)
            if expired is not None:
                return expired
            self.invocation_count += 1
            configured = self._scenarios.get(request.operation_id)
            try:
                if isinstance(configured, BaseException):
                    raise configured
                if configured is None:
                    result: MarketRegimeResultDTO | ErrorResultDTO = self._default(request)
                elif isinstance(configured, str):
                    result = self._error(
                        request.operation_id,
                        configured if configured in INFER_ERROR_CODES else "invalid_provider_output",
                    )
                elif isinstance(configured, ErrorResultDTO):
                    result = configured if configured.error.code in INFER_ERROR_CODES else self._error(request.operation_id, "invalid_provider_output")
                elif isinstance(configured, MarketRegimeResultDTO):
                    result = configured
                else:
                    result = self._error(request.operation_id, "invalid_provider_output")
                if isinstance(result, MarketRegimeResultDTO) and not self._valid_semantics(request, result):
                    result = self._error(request.operation_id, "invalid_provider_output")
            except BaseException as exception:
                result = map_unexpected_exception(
                    exception,
                    provider="market_regime_provider",
                    operation_id=request.operation_id,
                    occurred_at=self._clock.current_utc().as_datetime(),
                ).as_result()
            self._completed[request.operation_id] = (request, result)
            return result

    def health_check(self, request: HealthCheckRequestDTO) -> ProviderHealthDTO | ErrorResultDTO:
        expired = self._deadline_error(request, 3_000)
        if expired is not None:
            return expired
        return ProviderHealthDTO(
            "market_regime_provider",
            "market_regime_inference",
            "healthy",
            self._clock.current_utc(),
            0,
            None,
            None,
        )


__all__ = ("FakeMarketRegimeProvider",)
