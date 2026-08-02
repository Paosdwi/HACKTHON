from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.market_regime import (
    ExpectedModelDTO,
    FeatureDTO,
    FeatureWindowDTO,
    InferRequestDTO,
)
from crypto_trust_agent.application.ports.market_regime import MarketRegimeProvider
from crypto_trust_agent.application.ports.preflight import HealthCheckRequestDTO
from crypto_trust_agent.application.use_cases.infer_market_regime import (
    InferMarketRegimeCommand,
    InferMarketRegimeUseCase,
)
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock
from crypto_trust_agent.infrastructure.market_regime.disabled import (
    DisabledMarketRegimeProvider,
)

NOW = "2026-08-01T02:00:00Z"
HASH_A = "sha256:" + "a" * 64


def deadline(operation_id: str, *, at: str = "2026-08-01T02:00:25Z") -> DeadlineDTO:
    return DeadlineDTO(
        "1.0.0", operation_id, at, 25_000, NOW, 100
    )


def request(operation_id: str = "OP-MR-DISABLED") -> InferRequestDTO:
    return InferRequestDTO(
        operation_id,
        "TASK-001",
        "EXEC-001",
        "BTC",
        "2026-08-01T00:00:00Z",
        FeatureWindowDTO("2026-07-03", "2026-08-01"),
        (
            FeatureDTO(
                "return_14d",
                "-0.0312",
                "market-formulas-1.0.0",
                ("DATASET:BTC",),
            ),
        ),
        HASH_A,
        ExpectedModelDTO("market-regime-xgboost", "1.0.0"),
        deadline(operation_id),
    )


class DisabledMarketRegimeProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock(NOW, monotonic_ms=100_000)
        self.provider = DisabledMarketRegimeProvider(
            now_utc=lambda: self.clock.current_utc().as_datetime(),
            monotonic_ms=self.clock.current_monotonic_ms,
            runtime_id=self.clock.runtime_id,
        )

    def test_is_port_and_returns_no_synthetic_model_probabilities(self) -> None:
        self.assertIsInstance(self.provider, MarketRegimeProvider)

        result = self.provider.infer(request())

        self.assertIsInstance(result, ErrorResultDTO)
        self.assertEqual("endpoint_unavailable", result.error.code)
        self.assertFalse(result.error.retryable)
        self.assertFalse(hasattr(result, "probabilities"))
        self.assertEqual(0, self.provider.invocation_count)
        self.assertIs(result, self.provider.infer(request()))

        conflict = self.provider.infer(
            replace(request(), features=(replace(request().features[0], value="-0.04"),))
        )
        self.assertEqual("invalid_feature_schema", conflict.error.code)
        self.assertEqual("validation", conflict.error.category.value)

    def test_core_turns_disabled_boundary_into_versioned_deterministic_fallback(self) -> None:
        value = request("OP-MR-DISABLED-FALLBACK")

        result = InferMarketRegimeUseCase(self.provider, self.clock).execute(
            InferMarketRegimeCommand(
                "AN-PA72-DISABLED",
                value,
                ("DATASET:BTC",),
                "bearish",
                "market-formulas-1.0.0",
            )
        )

        self.assertEqual("deterministic_fallback", result.producer.kind.value)
        self.assertEqual("fallback", result.quality.status)
        self.assertEqual(
            ("market_regime_provider_absent", "endpoint_unavailable"),
            result.quality.limitations,
        )
        self.assertEqual("0", str(result.values["bullish_probability"]))
        self.assertEqual("1", str(result.values["bearish_probability"]))
        self.assertEqual("0", str(result.values["sideways_probability"]))
        self.assertEqual(0, self.provider.invocation_count)

    def test_health_is_degraded_but_not_blocking_and_exposes_safe_reason(self) -> None:
        operation_id = "OP-MR-DISABLED-HEALTH"

        result = self.provider.health_check(
            HealthCheckRequestDTO(operation_id, deadline(operation_id))
        )

        self.assertEqual("degraded", result.status)
        self.assertEqual("market_regime_provider", result.provider)
        self.assertEqual("market_regime_provider_disabled", result.safe_reason_code)
        self.assertEqual(0, self.provider.invocation_count)

    def test_expired_deadline_fails_without_model_io(self) -> None:
        operation_id = "OP-MR-DISABLED-EXPIRED"
        value = request(operation_id)
        value = InferRequestDTO(
            value.operation_id,
            value.task_id,
            value.execution_id,
            value.asset,
            value.as_of,
            value.feature_window,
            value.features,
            value.input_feature_hash,
            value.expected_model,
            deadline(operation_id, at=NOW),
        )

        result = self.provider.infer(value)

        self.assertEqual("deadline_exceeded", result.error.code)
        self.assertEqual(0, self.provider.invocation_count)


if __name__ == "__main__":
    unittest.main()
