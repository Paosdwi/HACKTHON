from __future__ import annotations

import json
import sys
import types
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Bind this Provider harness to the checked-out Core namespace package.
core_package = types.ModuleType("crypto_trust_agent")
core_package.__path__ = [str(SRC / "crypto_trust_agent")]
sys.modules["crypto_trust_agent"] = core_package

from crypto_trust_agent.application.dto.common import ErrorResultDTO
from crypto_trust_agent.application.dto.market_regime import (
    FeatureDTO,
    FeatureWindowDTO,
    HealthCheckRequestDTO,
)
from crypto_trust_agent.application.ports.market_regime import MarketRegimeProvider
from crypto_trust_agent.infrastructure.market_regime.adapter import (
    FeatureSchema,
    ProviderFailure,
    SageMakerMarketRegimeProvider,
    StubSageMakerClient,
)
from tests.contract.shared_market_regime_assertions import (
    MarketRegimeContractAssertions,
    deadline,
    request,
)

NOW = datetime(2026, 8, 1, 2, 0, 0, tzinfo=UTC)


class RecordingEvents:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def emit(self, event: object) -> None:
        if not isinstance(event, dict):
            raise TypeError("event must be a dictionary")
        self.items.append(dict(event))


def make_adapter(
    *, client: StubSageMakerClient, cancelled=lambda: False,
    events: RecordingEvents | None = None, monotonic=lambda: 100_000,
    feature_schema: tuple[FeatureSchema, ...] | None = None,
) -> SageMakerMarketRegimeProvider:
    return SageMakerMarketRegimeProvider(
        client, feature_schema=feature_schema, now_utc=lambda: NOW,
        monotonic_ms=monotonic, runtime_id="pa72-test-runtime",
        cancelled=cancelled, event_sink=events,
    )


class ProviderSharedContractTests(MarketRegimeContractAssertions, unittest.TestCase):
    """Runs unmodified Core MarketRegimeContractAssertions, including both IDs."""

    def setUp(self) -> None:
        self.clients: dict[int, StubSageMakerClient] = {}

    def make_provider(self) -> SageMakerMarketRegimeProvider:
        client = StubSageMakerClient()
        provider = make_adapter(client=client)
        self.clients[id(provider)] = client
        return provider

    def configure_infer(
        self, provider: SageMakerMarketRegimeProvider, operation_id: str, response: object
    ) -> None:
        self.clients[id(provider)].configure(operation_id, response)


class ProviderBoundaryTests(unittest.TestCase):
    def test_constructor_requires_explicit_client_and_fails_closed_without_one(self) -> None:
        with self.assertRaises(TypeError):
            SageMakerMarketRegimeProvider()  # type: ignore[call-arg]
        with self.assertRaises(ValueError):
            SageMakerMarketRegimeProvider(None)  # type: ignore[arg-type]

    def test_structural_port_and_declared_versions_and_policy(self) -> None:
        adapter = make_adapter(client=StubSageMakerClient())
        self.assertIsInstance(adapter, MarketRegimeProvider)
        self.assertEqual("1.0.0", adapter.contract_version)
        self.assertEqual("sagemaker-market-regime-adapter-1.0.0", adapter.provider_version)
        self.assertEqual(1, adapter.max_attempts)
        self.assertEqual(0, adapter.hidden_retries)
        self.assertTrue(adapter.non_production)

    def test_configured_feature_schema_order_calculation_alignment_and_model(self) -> None:
        schema = (
            FeatureSchema("return_14d", "market-formulas-1.0.0"),
            FeatureSchema("volume_zscore", "market-formulas-1.0.0"),
        )
        features = (
            FeatureDTO("return_14d", "-0.0312", "market-formulas-1.0.0", ("DATASET:A",)),
            FeatureDTO("volume_zscore", "0.25", "market-formulas-1.0.0", ("DATASET:B",)),
        )
        base = request("OP-MR-SCHEMA")
        valid = replace(base, features=features)
        client = StubSageMakerClient()
        result = make_adapter(client=client, feature_schema=schema).infer(valid)
        self.assertNotIsInstance(result, ErrorResultDTO)
        body = cast(str | bytes | bytearray, client.calls[0]["body"])
        payload = json.loads(body)
        self.assertEqual(["return_14d", "volume_zscore"], [item["name"] for item in payload["features"]])
        self.assertEqual("-0.0312", payload["features"][0]["value"])

        cases = (
            (replace(valid, features=tuple(reversed(features))), "feature_alignment_error"),
            (replace(valid, features=(replace(features[0], calculation_version="formula-2.0.0"), features[1])), "invalid_feature_schema"),
            (replace(valid, feature_window=FeatureWindowDTO("2026-07-02", "2026-07-31")), "feature_alignment_error"),
            (replace(valid, expected_model=replace(valid.expected_model, name="other-model")), "model_version_mismatch"),
        )
        for index, (value, code) in enumerate(cases):
            with self.subTest(code=code):
                another = replace(value, operation_id=f"OP-MR-SCHEMA-BAD-{index}", deadline=deadline(f"OP-MR-SCHEMA-BAD-{index}"))
                local_client = StubSageMakerClient()
                failure = make_adapter(client=local_client, feature_schema=schema).infer(another)
                self.assertEqual(code, failure.error.code)
                self.assertEqual([], local_client.calls)

    def test_hash_identity_is_bound_without_inventing_hash_algorithm(self) -> None:
        client = StubSageMakerClient()
        adapter = make_adapter(client=client)
        original = request("OP-MR-HASH-BIND")
        first = adapter.infer(original)
        self.assertEqual(original.input_feature_hash, first.input_feature_hash)
        self.assertIs(first, adapter.infer(original))
        conflict = adapter.infer(replace(original, features=(replace(original.features[0], value="-0.04"),)))
        self.assertEqual("invalid_feature_schema", conflict.error.code)
        self.assertEqual(1, len(client.calls))

    def test_provider_numeric_json_is_decimal_parsed_and_canonicalized_without_float(self) -> None:
        operation = "OP-MR-DECIMAL"
        value = request(operation)
        raw = json.dumps({
            "schema_version": "1.0.0", "operation_id": operation, "asset": "BTC",
            "as_of": "2026-08-01T00:00:00Z",
            "model": {"name": "market-regime-xgboost", "version": "v1", "invocation_id": "INV-DECIMAL"},
            "probabilities": {"bullish": "0.333333333333333333", "bearish": "0.333333333333333333", "sideways": "0.333333333333333334"},
            "anomaly_score": "0.100000000000000001", "feature_window": {"start": "2026-07-03", "end": "2026-08-01"},
            "input_feature_hash": value.input_feature_hash, "computed_at": "2026-08-01T02:00:00Z",
            "quality": "valid", "limitations": [],
        }, separators=(",", ":"))
        # Replace only decimal strings with JSON numbers; adapter parse_float=Decimal preserves all digits.
        raw = raw.replace('"0.333333333333333333"', '0.333333333333333333')
        raw = raw.replace('"0.333333333333333334"', '0.333333333333333334')
        raw = raw.replace('"0.100000000000000001"', '0.100000000000000001')
        client = StubSageMakerClient()
        client.configure(operation, raw.encode())
        result = make_adapter(client=client).infer(value)
        self.assertEqual("0.333333333333333333", str(result.probabilities.bullish))
        self.assertEqual("0.100000000000000001", str(result.anomaly_score))

    def test_response_binding_model_probability_anomaly_quality_and_limitations(self) -> None:
        base = request("OP-MR-RESPONSE")

        def response(**changes: object) -> str:
            value: dict[str, object] = {
                "schema_version": "1.0.0", "operation_id": base.operation_id, "asset": base.asset,
                "as_of": str(base.as_of), "model": {"name": base.expected_model.name, "version": "v1", "invocation_id": "INV-VALID"},
                "probabilities": {"bullish": "0.4", "bearish": "0.2", "sideways": "0.4"},
                "anomaly_score": "0.1", "feature_window": base.feature_window.to_wire(),
                "input_feature_hash": base.input_feature_hash, "computed_at": "2026-08-01T02:00:00Z",
                "quality": "valid", "limitations": [],
            }
            value.update(changes)
            return json.dumps(value, separators=(",", ":"))

        cases: tuple[tuple[dict[str, object], str], ...] = (
            ({"asset": "ETH"}, "invalid_provider_output"),
            ({"input_feature_hash": "sha256:" + "b" * 64}, "invalid_provider_output"),
            ({"model": {"name": base.expected_model.name, "version": "v2", "invocation_id": "INV-BAD"}}, "model_version_mismatch"),
            ({"probabilities": {"bullish": "0.4", "bearish": "0.2", "sideways": "0.39"}}, "invalid_probability_distribution"),
            ({"anomaly_score": "1.1"}, "invalid_provider_output"),
            ({"quality": "vendor_quality"}, "invalid_provider_output"),
            ({"limitations": ["Authorization: Bearer SECRET"]}, "invalid_provider_output"),
        )
        for index, (changes, expected) in enumerate(cases):
            operation = f"OP-MR-RESP-{index}"
            value = replace(base, operation_id=operation, deadline=deadline(operation))
            client = StubSageMakerClient()
            client.configure(operation, response(**{"operation_id": operation, **changes}))
            result = make_adapter(client=client).infer(value)
            self.assertEqual(expected, result.error.code)
            self.assertNotIn("secret", repr(result.to_wire()).lower())

    def test_timeout_effective_deadline_cancellation_endpoint_and_zero_retry(self) -> None:
        timeout_client = StubSageMakerClient()
        timeout_client.configure("OP-MR-TIME", TimeoutError("vendor secret payload"))
        timeout = make_adapter(client=timeout_client).infer(request("OP-MR-TIME"))
        self.assertEqual("market_regime_timeout", timeout.error.code)
        self.assertEqual(1, len(timeout_client.calls))
        timeout_value = cast(int, timeout_client.calls[0]["timeout_ms"])
        self.assertLessEqual(timeout_value, 25_000)

        cancelled_client = StubSageMakerClient()
        cancelled = make_adapter(client=cancelled_client, cancelled=lambda: True).infer(request("OP-MR-CANCEL"))
        self.assertEqual("market_regime_timeout", cancelled.error.code)
        self.assertEqual([], cancelled_client.calls)

        unavailable_client = StubSageMakerClient()
        unavailable_client.configure("OP-MR-DOWN", ProviderFailure("endpoint_unavailable", retryable=True))
        unavailable = make_adapter(client=unavailable_client).infer(request("OP-MR-DOWN"))
        self.assertEqual("endpoint_unavailable", unavailable.error.code)
        self.assertEqual("unavailable", unavailable.error.category.value)
        self.assertEqual(1, len(unavailable_client.calls))

    def test_post_call_monotonic_deadline_rehearsal_maps_timeout(self) -> None:
        ticks = iter((100_000, 100_000, 126_000, 126_000))
        client = StubSageMakerClient()
        adapter = make_adapter(client=client, monotonic=lambda: next(ticks))
        result = adapter.infer(request("OP-MR-LATENCY"))
        self.assertEqual("market_regime_timeout", result.error.code)
        self.assertEqual(1, len(client.calls))

    def test_unknown_errors_and_events_are_redacted_and_no_fallback_exists(self) -> None:
        events = RecordingEvents()
        client = StubSageMakerClient()
        client.configure("OP-MR-SECRET", RuntimeError("Authorization: Bearer TOP-SECRET endpoint payload"))
        result = make_adapter(client=client, events=events).infer(request("OP-MR-SECRET"))
        self.assertEqual("unexpected_provider_error", result.error.code)
        self.assertEqual("unexpected", result.error.category.value)
        self.assertEqual({}, dict(result.error.details))
        serialized = json.dumps({"result": result.to_wire(), "events": events.items}).lower()
        for forbidden in ("top-secret", "authorization", "bearer", "runtimeerror", "endpoint payload"):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn("fallback", serialized)
        self.assertFalse(hasattr(result, "probabilities"))

    def test_unknown_retryable_provider_failure_infer_is_safe_and_not_retryable(self) -> None:
        operation = "OP-MR-UNKNOWN-PROVIDER-FAILURE"
        events = RecordingEvents()
        client = StubSageMakerClient()
        client.configure(
            operation,
            ProviderFailure("Authorization: Bearer TOP-SECRET vendor code", retryable=True),
        )
        result = make_adapter(client=client, events=events).infer(request(operation))

        self.assertIsInstance(result, ErrorResultDTO)
        self.assertEqual("unexpected_provider_error", result.error.code)
        self.assertEqual("unexpected", result.error.category.value)
        self.assertFalse(result.error.retryable)
        self.assertEqual({}, dict(result.error.details))
        serialized = json.dumps({"result": result.to_wire(), "events": events.items}).lower()
        for forbidden in ("top-secret", "authorization", "bearer", "vendor code"):
            self.assertNotIn(forbidden, serialized)
        self.assertFalse(hasattr(result, "probabilities"))

    def test_health_timeout_unavailable_unknown_and_no_unavailable_status(self) -> None:
        cases = (
            (TimeoutError("secret"), "market_regime_timeout", "timeout"),
            (ProviderFailure("endpoint_unavailable", retryable=True), "endpoint_unavailable", "unavailable"),
            (RuntimeError("Authorization: Bearer SECRET"), "unexpected_provider_error", "unexpected"),
        )
        for index, (failure, code, category) in enumerate(cases):
            client = StubSageMakerClient()
            client.probe_result = failure
            operation = f"OP-MR-HEALTH-{index}"
            result = make_adapter(client=client).health_check(
                HealthCheckRequestDTO(operation, deadline(operation, at="2026-08-01T02:00:03Z", budget_ms=3_000))
            )
            self.assertEqual(code, result.error.code)
            self.assertEqual(category, result.error.category.value)
            self.assertNotIn("secret", repr(result.to_wire()).lower())

        client = StubSageMakerClient()
        client.probe_result = False
        operation = "OP-MR-HEALTH-FALSE"
        result = make_adapter(client=client).health_check(
            HealthCheckRequestDTO(operation, deadline(operation, at="2026-08-01T02:00:03Z", budget_ms=3_000))
        )
        self.assertIsInstance(result, ErrorResultDTO)
        self.assertEqual("endpoint_unavailable", result.error.code)

    def test_unknown_retryable_provider_failure_health_is_safe_and_not_retryable(self) -> None:
        operation = "OP-MR-HEALTH-UNKNOWN-PROVIDER-FAILURE"
        client = StubSageMakerClient()
        client.probe_result = ProviderFailure(
            "Authorization: Bearer TOP-SECRET health vendor code", retryable=True
        )
        result = make_adapter(client=client).health_check(
            HealthCheckRequestDTO(
                operation,
                deadline(operation, at="2026-08-01T02:00:03Z", budget_ms=3_000),
            )
        )

        self.assertIsInstance(result, ErrorResultDTO)
        self.assertEqual("unexpected_provider_error", result.error.code)
        self.assertEqual("unexpected", result.error.category.value)
        self.assertFalse(result.error.retryable)
        self.assertEqual({}, dict(result.error.details))
        serialized = json.dumps(result.to_wire()).lower()
        for forbidden in ("top-secret", "authorization", "bearer", "vendor code"):
            self.assertNotIn(forbidden, serialized)

    def test_deadline_expiry_starts_no_io_and_health_is_at_most_three_seconds(self) -> None:
        expired_client = StubSageMakerClient()
        expired = request("OP-MR-NO-IO")
        expired = replace(expired, deadline=deadline(expired.operation_id, at="2026-08-01T02:00:00Z"))
        result = make_adapter(client=expired_client).infer(expired)
        self.assertEqual("deadline_exceeded", result.error.code)
        self.assertEqual([], expired_client.calls)
        self.assertEqual(0, make_adapter(client=expired_client).invocation_count)

        class TimeoutRecordingClient(StubSageMakerClient):
            def __init__(self) -> None:
                super().__init__()
                self.probe_timeout = 0

            def probe(self, *, timeout_ms: int, cancelled) -> bool:
                self.probe_timeout = timeout_ms
                return super().probe(timeout_ms=timeout_ms, cancelled=cancelled)

        health_client = TimeoutRecordingClient()
        operation = "OP-MR-HEALTH-BOUND"
        health = make_adapter(client=health_client).health_check(
            HealthCheckRequestDTO(operation, deadline(operation, at="2026-08-01T02:00:03Z", budget_ms=3_000))
        )
        self.assertEqual("healthy", health.status)
        self.assertLessEqual(health_client.probe_timeout, 3_000)


if __name__ == "__main__":
    unittest.main()
