from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.market_regime import (
    ExpectedModelDTO,
    FeatureDTO,
    FeatureWindowDTO,
    HealthCheckRequestDTO,
    InferRequestDTO,
    MarketRegimeResultDTO,
    ModelDTO,
    ProbabilityDistributionDTO,
)
from crypto_trust_agent.application.ports.market_regime import MarketRegimeProvider

HASH_A = "sha256:" + "a" * 64


def deadline(
    operation_id: str,
    *,
    at: str = "2026-08-01T02:00:25Z",
    budget_ms: int = 25_000,
) -> DeadlineDTO:
    return DeadlineDTO("1.0.0", operation_id, at, budget_ms, "2026-08-01T02:00:00Z", 100)


def request(operation_id: str = "OP-MR-01") -> InferRequestDTO:
    return InferRequestDTO(
        operation_id,
        "TASK-001",
        "EXEC-001",
        "BTC",
        "2026-08-01T00:00:00Z",
        FeatureWindowDTO("2026-07-03", "2026-08-01"),
        (FeatureDTO("return_14d", "-0.0312", "market-formulas-1.0.0", ("DATASET:BTC-001",)),),
        HASH_A,
        ExpectedModelDTO("market-regime-xgboost", "1.0.0"),
        deadline(operation_id),
    )


class MarketRegimeContractAssertions:
    def test_frozen_method_ids_and_policies_remain_exact(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        schema = json.loads(
            (project_root / "docs" / "architecture" / "schemas" / "market_regime_provider" / "contract.schema.json").read_text(encoding="utf-8")
        )
        infer = schema["$defs"]["InferOperation"]
        health = schema["$defs"]["HealthOperation"]
        self.assertEqual("CT-MARKET-INFER-01", infer["x-contract-test-id"])
        self.assertEqual("CT-MARKET-HEALTH-01", health["x-contract-test-id"])
        self.assertEqual(25_000, infer["x-method-policy"]["timeout_ms"])
        self.assertEqual(3_000, health["x-method-policy"]["timeout_ms"])
        for operation in (infer, health):
            policy = operation["x-method-policy"]
            self.assertEqual("core", policy["retry_owner"])
            self.assertEqual(1, policy["max_attempts"])
            self.assertFalse(policy["hidden_adapter_retries"])

    def make_provider(self):
        raise NotImplementedError

    def configure_infer(self, provider, operation_id: str, response: object) -> None:
        raise NotImplementedError

    def test_runtime_protocol_non_production_and_only_two_methods(self) -> None:
        provider = self.make_provider()
        self.assertIsInstance(provider, MarketRegimeProvider)
        self.assertTrue(provider.non_production)
        self.assertTrue(callable(provider.infer))
        self.assertTrue(callable(provider.health_check))
        self.assertFalse(hasattr(provider, "capabilities"))

    def test_frozen_dtos_have_exact_contract_wire_shape(self) -> None:
        value = request()
        self.assertEqual(
            {
                "schema_version", "operation_id", "task_id", "execution_id", "asset",
                "as_of", "feature_window", "features", "input_feature_hash",
                "expected_model", "deadline",
            },
            set(value.to_wire()),
        )
        with self.assertRaises(FrozenInstanceError):
            value.asset = "ETH"
        result = self.make_provider().infer(value)
        self.assertEqual(
            {
                "schema_version", "operation_id", "asset", "as_of", "model",
                "probabilities", "anomaly_score", "feature_window", "input_feature_hash",
                "computed_at", "quality", "limitations",
            },
            set(result.to_wire()),
        )
        self.assertNotIn("fallback", result.to_wire())

    def test_infer_is_replay_safe_feature_model_bound_and_zero_retry(self) -> None:
        provider = self.make_provider()
        value = request()
        first = provider.infer(value)
        self.assertIs(first, provider.infer(value))
        self.assertEqual(1, provider.invocation_count)
        changed = provider.infer(replace(value, input_feature_hash="sha256:" + "b" * 64))
        self.assertEqual("invalid_feature_schema", changed.error.code)
        self.assertEqual(1, provider.invocation_count)

    def test_timeout_typed_errors_unknown_exception_and_deadline_before_io(self) -> None:
        provider = self.make_provider()
        self.configure_infer(provider, "OP-MR-TIMEOUT", "market_regime_timeout")
        timeout = provider.infer(request("OP-MR-TIMEOUT"))
        self.assertEqual("market_regime_timeout", timeout.error.code)
        self.assertEqual(1, provider.invocation_count)

        self.configure_infer(provider, "OP-MR-UNKNOWN", RuntimeError("secret vendor payload"))
        unknown = provider.infer(request("OP-MR-UNKNOWN"))
        self.assertEqual("unexpected_provider_error", unknown.error.code)
        self.assertNotIn("secret", repr(unknown.to_wire()))
        self.assertEqual(2, provider.invocation_count)

        expired_request = replace(
            request("OP-MR-EXPIRED"),
            deadline=deadline("OP-MR-EXPIRED", at="2026-08-01T02:00:00Z"),
        )
        expired = provider.infer(expired_request)
        self.assertEqual("deadline_exceeded", expired.error.code)
        self.assertEqual(2, provider.invocation_count)

    def test_probability_sum_semantics_and_output_binding(self) -> None:
        with self.assertRaises(ValueError):
            ProbabilityDistributionDTO("0.4", "0.2", "0.39")
        provider = self.make_provider()
        value = request()
        result = provider.infer(value)
        self.assertEqual(value.operation_id, result.operation_id)
        self.assertEqual(value.asset, result.asset)
        self.assertEqual(value.input_feature_hash, result.input_feature_hash)
        total = sum(item.as_decimal() for item in (
            result.probabilities.bullish,
            result.probabilities.bearish,
            result.probabilities.sideways,
        ))
        self.assertLessEqual(abs(total - 1), 0.000001)

    def test_health_is_three_second_side_effect_free_boundary(self) -> None:
        provider = self.make_provider()
        health = provider.health_check(
            HealthCheckRequestDTO(
                "OP-MR-HEALTH",
                deadline("OP-MR-HEALTH", at="2026-08-01T02:00:03Z", budget_ms=3_000),
            )
        )
        self.assertEqual("market_regime_provider", health.provider)
        self.assertEqual("market_regime_inference", health.capability)
        self.assertEqual("healthy", health.status)
        self.assertEqual(0, provider.invocation_count)
