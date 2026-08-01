"""Strict SageMaker MarketRegimeProvider adapter for frozen contract 1.0.0."""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from threading import RLock
from typing import Protocol

from crypto_trust_agent.application.dto.common import (
    DeadlineExceededError,
    ErrorResultDTO,
    LocalDeadline,
    PortErrorCategory,
    PortErrorDTO,
    build_local_deadline,
    map_unexpected_exception,
)
from crypto_trust_agent.application.dto.market_regime import (
    HealthCheckRequestDTO,
    InferRequestDTO,
    MarketRegimeResultDTO,
    ModelDTO,
    ProbabilityDistributionDTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO
from crypto_trust_agent.domain.primitives import (
    CanonicalDecimal,
    ContractValidationError,
)

CONTRACT_VERSION = "1.0.0"
PROVIDER_VERSION = "sagemaker-market-regime-adapter-1.0.0"
DEFAULT_MODEL_NAME = "market-regime-xgboost"
DEFAULT_MODEL_VERSION = "v1"
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VERSION = re.compile(r"^[a-z0-9_-]+-[0-9]+\.[0-9]+\.[0-9]+$")
_INVOCATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ROOT_FIELDS = frozenset({
    "schema_version", "operation_id", "asset", "as_of", "model",
    "probabilities", "anomaly_score", "feature_window", "input_feature_hash",
    "computed_at", "quality", "limitations",
})


@dataclass(frozen=True, slots=True)
class FeatureSchema:
    name: str
    calculation_version: str

    def __post_init__(self) -> None:
        if _NAME.fullmatch(self.name) is None:
            raise ValueError("invalid configured feature name")
        if _VERSION.fullmatch(self.calculation_version) is None:
            raise ValueError("invalid configured calculation version")


class SageMakerClient(Protocol):
    non_production: bool
    max_attempts: int
    hidden_retries: int

    def invoke(
        self, *, operation_id: str, body: bytes, timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bytes | str: ...

    def probe(self, *, timeout_ms: int, cancelled: Callable[[], bool]) -> bool: ...


class EventSink(Protocol):
    def emit(self, event: Mapping[str, object]) -> None: ...


class ProviderFailure(RuntimeError):
    """Allowlisted Infrastructure failure without vendor exception content."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__("provider failure")
        self.code = code
        self.retryable = retryable


class StubSageMakerClient:
    """Deterministic offline client; never imports AWS SDKs or reads credentials."""

    non_production = True
    max_attempts = 1
    hidden_retries = 0

    def __init__(self) -> None:
        self.responses: dict[str, object] = {}
        self.calls: list[dict[str, object]] = []
        self.probe_result: object = True

    def configure(self, operation_id: str, response: object) -> None:
        self.responses[operation_id] = response

    def invoke(
        self, *, operation_id: str, body: bytes, timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bytes | str:
        if cancelled():
            raise ProviderFailure("market_regime_timeout", retryable=True)
        self.calls.append({"operation_id": operation_id, "body": body, "timeout_ms": timeout_ms})
        configured = self.responses.get(operation_id)
        if isinstance(configured, BaseException):
            raise configured
        if isinstance(configured, str) and configured in {
            "market_regime_timeout", "endpoint_unavailable", "provider_rate_limited",
        }:
            raise ProviderFailure(configured, retryable=True)
        if configured is not None:
            if isinstance(configured, (bytes, str)):
                return configured
            raise TypeError("stub response must be JSON bytes or text")
        request = json.loads(body.decode("utf-8"))
        response = {
            "schema_version": CONTRACT_VERSION,
            "operation_id": request["operation_id"],
            "asset": request["asset"],
            "as_of": request["as_of"],
            "model": {"name": request["expected_model"]["name"], "version": DEFAULT_MODEL_VERSION,
                      "invocation_id": f"INV-{operation_id[3:]}"},
            "probabilities": {"bullish": "0.4", "bearish": "0.2", "sideways": "0.4"},
            "anomaly_score": "0.1", "feature_window": request["feature_window"],
            "input_feature_hash": request["input_feature_hash"],
            "computed_at": "2026-08-01T02:00:00Z", "quality": "valid", "limitations": [],
        }
        return json.dumps(response, separators=(",", ":")).encode("utf-8")

    def probe(self, *, timeout_ms: int, cancelled: Callable[[], bool]) -> bool:
        del timeout_ms
        if cancelled():
            raise ProviderFailure("market_regime_timeout", retryable=True)
        if isinstance(self.probe_result, BaseException):
            raise self.probe_result
        return self.probe_result is True


class NullEventSink:
    def emit(self, event: Mapping[str, object]) -> None:
        del event


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _category(code: str) -> PortErrorCategory:
    if code in {"market_regime_timeout", "deadline_exceeded"}:
        return PortErrorCategory.TIMEOUT
    if code == "endpoint_unavailable":
        return PortErrorCategory.UNAVAILABLE
    if code == "provider_rate_limited":
        return PortErrorCategory.RATE_LIMITED
    if code in {"invalid_probability_distribution", "invalid_provider_output"}:
        return PortErrorCategory.INVALID_PROVIDER_OUTPUT
    if code == "unexpected_provider_error":
        return PortErrorCategory.UNEXPECTED
    return PortErrorCategory.VALIDATION


def _canonical_provider_decimal(value: object) -> str:
    """Normalize a provider JSON number parsed as Decimal, or validate a string."""
    if isinstance(value, str):
        return str(CanonicalDecimal(value))
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ContractValidationError("provider decimal must be a JSON number or canonical string")
    if value == 0:
        text = "0"
    else:
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
    return str(CanonicalDecimal(text))


def _parse_provider_json(raw: bytes | str) -> Mapping[str, object]:
    if isinstance(raw, bytes):
        text = raw.decode("utf-8")
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError("provider output must be JSON bytes or text")
    value = json.loads(
        text,
        parse_float=Decimal,
        parse_int=Decimal,
        parse_constant=lambda constant: (_ for _ in ()).throw(
            ValueError(f"invalid numeric constant {constant}")
        ),
    )
    if not isinstance(value, Mapping):
        raise TypeError("provider output root must be an object")
    return value


class SageMakerMarketRegimeProvider:
    """One-attempt adapter; Core owns retries, fallback, and AnalysisResult creation."""

    contract_version = CONTRACT_VERSION
    provider_version = PROVIDER_VERSION
    service_version = "sagemaker-runtime-boundary-1.0.0"
    max_attempts = 1
    hidden_retries = 0

    def __init__(
        self,
        client: SageMakerClient,
        *,
        feature_schema: Sequence[FeatureSchema] | None = None,
        model_name: str = DEFAULT_MODEL_NAME,
        model_version: str = DEFAULT_MODEL_VERSION,
        safe_limitations: Sequence[str] = (),
        now_utc: Callable[[], datetime] | None = None,
        monotonic_ms: Callable[[], int] | None = None,
        runtime_id: str = "sagemaker-market-regime-runtime",
        cancelled: Callable[[], bool] | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        if client is None:
            raise ValueError("SageMaker client must be explicitly injected")
        self._client = client
        if self._client.max_attempts != 1 or self._client.hidden_retries != 0:
            raise ValueError("SageMaker client must use one attempt and zero hidden retries")
        configured = tuple(feature_schema or (FeatureSchema("return_14d", "market-formulas-1.0.0"),))
        if not configured or len({item.name for item in configured}) != len(configured):
            raise ValueError("configured feature schema must be nonempty and unique")
        if not model_name or not model_version:
            raise ValueError("configured model identity is required")
        self._feature_schema = configured
        self._model_name = model_name
        self._model_version = model_version
        self._safe_limitations = frozenset(safe_limitations)
        self._now = now_utc or (lambda: datetime.now(UTC))
        self._monotonic_ms = monotonic_ms or (lambda: time.monotonic_ns() // 1_000_000)
        self._runtime_id = runtime_id
        self._cancelled = cancelled or (lambda: False)
        self._events = event_sink or NullEventSink()
        self._completed: dict[str, tuple[InferRequestDTO, MarketRegimeResultDTO | ErrorResultDTO]] = {}
        self._lock = RLock()
        self.non_production = bool(getattr(self._client, "non_production", False))
        self.invocation_count = 0

    def _error(self, operation_id: str, code: str, *, retryable: bool = False) -> ErrorResultDTO:
        return ErrorResultDTO(PortErrorDTO(
            schema_version=CONTRACT_VERSION,
            code=code,
            category=_category(code),
            retryable=retryable,
            safe_message="Market regime provider request failed.",
            provider="sagemaker_market_regime",
            operation_id=operation_id,
            details={},
            occurred_at=_utc_text(self._now()),
        ))

    def _deadline(
        self,
        request: InferRequestDTO | HealthCheckRequestDTO,
        timeout_ms: int,
    ) -> LocalDeadline:
        return build_local_deadline(
            request.deadline,
            provider_timeout_ms=timeout_ms,
            now_utc=self._now(),
            now_monotonic_ms=self._monotonic_ms(),
            runtime_id=self._runtime_id,
        )

    def _validate_request(self, request: InferRequestDTO) -> str | None:
        actual_names = tuple(item.name for item in request.features)
        expected_names = tuple(item.name for item in self._feature_schema)
        if len(actual_names) != len(set(actual_names)):
            return "feature_alignment_error"
        if set(actual_names) != set(expected_names):
            return "invalid_feature_schema"
        if actual_names != expected_names:
            return "feature_alignment_error"
        for feature, configured in zip(request.features, self._feature_schema, strict=True):
            if feature.calculation_version != configured.calculation_version:
                return "invalid_feature_schema"
            # DTO construction already proves canonical Decimal and nonempty source refs.
            if str(feature.value) != _canonical_provider_decimal(str(feature.value)):
                return "invalid_feature_schema"
        if request.feature_window.end != request.as_of.as_datetime().date():
            return "feature_alignment_error"
        if request.expected_model.name != self._model_name:
            return "model_version_mismatch"
        return None

    @staticmethod
    def _payload(request: InferRequestDTO) -> bytes:
        # Core's opaque input_feature_hash is forwarded and bound, never recomputed here.
        return json.dumps(
            request.to_wire(), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")

    def _map_response(
        self, request: InferRequestDTO, raw: bytes | str
    ) -> MarketRegimeResultDTO:
        value = _parse_provider_json(raw)
        if set(value) != _ROOT_FIELDS or value["schema_version"] != CONTRACT_VERSION:
            raise ValueError("provider root shape is invalid")
        model = value["model"]
        probabilities = value["probabilities"]
        window = value["feature_window"]
        limitations = value["limitations"]
        if not isinstance(model, Mapping) or set(model) != {"name", "version", "invocation_id"}:
            raise ValueError("provider model shape is invalid")
        if not all(isinstance(model[field], str) for field in ("name", "version", "invocation_id")):
            raise ValueError("provider model values are invalid")
        if _INVOCATION.fullmatch(model["invocation_id"]) is None:
            raise ValueError("provider invocation identity is unsafe")
        if model["name"] != request.expected_model.name or model["version"] != self._model_version:
            raise ProviderFailure("model_version_mismatch")
        if not isinstance(probabilities, Mapping) or set(probabilities) != {"bullish", "bearish", "sideways"}:
            raise ProviderFailure("invalid_probability_distribution")
        try:
            distribution = ProbabilityDistributionDTO(
                _canonical_provider_decimal(probabilities["bullish"]),
                _canonical_provider_decimal(probabilities["bearish"]),
                _canonical_provider_decimal(probabilities["sideways"]),
            )
        except (ContractValidationError, TypeError) as error:
            raise ProviderFailure("invalid_probability_distribution") from error
        if not isinstance(window, Mapping) or set(window) != {"start", "end"}:
            raise ValueError("provider feature window is invalid")
        if not isinstance(limitations, list) or not all(isinstance(item, str) for item in limitations):
            raise ValueError("provider limitations are invalid")
        if not set(limitations).issubset(self._safe_limitations):
            raise ValueError("provider limitations are not locally allowlisted")
        result = MarketRegimeResultDTO(
            operation_id=value["operation_id"],
            asset=value["asset"],
            as_of=value["as_of"],
            model=ModelDTO(model["name"], model["version"], model["invocation_id"]),
            probabilities=distribution,
            anomaly_score=_canonical_provider_decimal(value["anomaly_score"]),
            feature_window=type(request.feature_window)(window["start"], window["end"]),
            input_feature_hash=value["input_feature_hash"],
            computed_at=value["computed_at"],
            quality=value["quality"],
            limitations=tuple(limitations),
            schema_version=value["schema_version"],
        )
        if (
            result.operation_id != request.operation_id
            or result.asset != request.asset
            or result.as_of != request.as_of
            or result.feature_window != request.feature_window
            or result.input_feature_hash != request.input_feature_hash
            or result.model.name != request.expected_model.name
            or result.model.version != self._model_version
        ):
            raise ValueError("provider result is not bound to request")
        return result

    def _emit(self, operation_id: str, status: str, started_ms: int, code: str | None) -> None:
        event = {
            "schema_version": CONTRACT_VERSION,
            "step": "infer_market_regime",
            "adapter": "sagemaker_market_regime",
            "status": status,
            "duration_ms": max(0, self._monotonic_ms() - started_ms),
            "retry_count": 0,
            "operation_id": operation_id,
            "safe_parameters": {
                "contract_version": CONTRACT_VERSION,
                "provider_version": PROVIDER_VERSION,
                "model_name": self._model_name,
                "model_version": self._model_version,
            },
            "result_summary": {"error_code": code},
        }
        try:
            self._events.emit(event)
        except Exception:  # noqa: BLE001 - observability must not alter provider outcome
            return

    def infer(self, request: InferRequestDTO) -> MarketRegimeResultDTO | ErrorResultDTO:
        with self._lock:
            completed = self._completed.get(request.operation_id)
            if completed is not None:
                if completed[0] == request:
                    return completed[1]
                return self._error(request.operation_id, "invalid_feature_schema")
            started_ms = self._monotonic_ms()
            validation_error = self._validate_request(request)
            if validation_error is not None:
                return self._error(request.operation_id, validation_error)
            try:
                local_deadline = self._deadline(request, 25_000)
                if self._cancelled():
                    raise ProviderFailure("market_regime_timeout", retryable=True)
                self.invocation_count += 1
                raw = self._client.invoke(
                    operation_id=request.operation_id,
                    body=self._payload(request),
                    timeout_ms=local_deadline.effective_timeout_ms,
                    cancelled=self._cancelled,
                )
                if self._cancelled() or self._monotonic_ms() >= local_deadline.deadline_monotonic_ms:
                    raise ProviderFailure("market_regime_timeout", retryable=True)
                result: MarketRegimeResultDTO | ErrorResultDTO = self._map_response(request, raw)
            except DeadlineExceededError:
                result = self._error(request.operation_id, "deadline_exceeded")
            except TimeoutError:
                result = self._error(request.operation_id, "market_regime_timeout", retryable=True)
            except ProviderFailure as failure:
                allowed = {
                    "model_version_mismatch", "invalid_probability_distribution",
                    "market_regime_timeout", "endpoint_unavailable", "provider_rate_limited",
                }
                code = failure.code if failure.code in allowed else "unexpected_provider_error"
                retryable = failure.retryable if failure.code in allowed else False
                result = self._error(request.operation_id, code, retryable=retryable)
            except (ContractValidationError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                result = self._error(request.operation_id, "invalid_provider_output")
            except Exception as exception:  # noqa: BLE001 - contract requires safe unknown mapping
                result = map_unexpected_exception(
                    exception,
                    provider="sagemaker_market_regime",
                    operation_id=request.operation_id,
                    occurred_at=self._now(),
                ).as_result()
            self._completed[request.operation_id] = (request, result)
            outcome_code = result.error.code if isinstance(result, ErrorResultDTO) else None
            self._emit(
                request.operation_id,
                "failed" if outcome_code else "succeeded",
                started_ms,
                outcome_code,
            )
            return result

    def health_check(
        self, request: HealthCheckRequestDTO
    ) -> ProviderHealthDTO | ErrorResultDTO:
        started_ms = self._monotonic_ms()
        try:
            local_deadline = self._deadline(request, 3_000)
            if self._cancelled():
                raise ProviderFailure("market_regime_timeout", retryable=True)
            healthy = self._client.probe(
                timeout_ms=local_deadline.effective_timeout_ms,
                cancelled=self._cancelled,
            )
            if self._cancelled() or self._monotonic_ms() >= local_deadline.deadline_monotonic_ms:
                raise ProviderFailure("market_regime_timeout", retryable=True)
            if not healthy:
                return self._error(request.operation_id, "endpoint_unavailable", retryable=True)
            return ProviderHealthDTO(
                provider="market_regime_provider",
                capability="market_regime_inference",
                status="healthy",
                checked_at=_utc_text(self._now()),
                latency_ms=min(30_000, max(0, self._monotonic_ms() - started_ms)),
                safe_reason_code=None,
                expires_at=None,
            )
        except DeadlineExceededError:
            return self._error(request.operation_id, "deadline_exceeded")
        except TimeoutError:
            return self._error(request.operation_id, "market_regime_timeout", retryable=True)
        except ProviderFailure as failure:
            allowed = {"market_regime_timeout", "endpoint_unavailable", "deadline_exceeded"}
            code = failure.code if failure.code in allowed else "unexpected_provider_error"
            retryable = failure.retryable if failure.code in allowed else False
            return self._error(request.operation_id, code, retryable=retryable)
        except Exception as exception:  # noqa: BLE001 - contract requires safe unknown mapping
            return map_unexpected_exception(
                exception,
                provider="sagemaker_market_regime",
                operation_id=request.operation_id,
                occurred_at=self._now(),
            ).as_result()


__all__ = (
    "CONTRACT_VERSION",
    "DEFAULT_MODEL_NAME",
    "DEFAULT_MODEL_VERSION",
    "PROVIDER_VERSION",
    "FeatureSchema",
    "NullEventSink",
    "ProviderFailure",
    "SageMakerClient",
    "SageMakerMarketRegimeProvider",
    "StubSageMakerClient",
)
