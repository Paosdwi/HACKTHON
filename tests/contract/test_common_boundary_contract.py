from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.common import (  # noqa: E402
    DeadlineDTO,
    DeadlineExceededError,
    PortErrorCategory,
    build_local_deadline,
    map_unexpected_exception,
)
from crypto_trust_agent.domain.primitives import ContractValidationError  # noqa: E402


class DeadlineContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dto = DeadlineDTO(
            schema_version="1.0.0",
            operation_id="OP-001",
            deadline_at_utc="2026-08-01T02:15:00Z",
            budget_ms=25_000,
            sent_at_utc="2026-08-01T02:14:34Z",
            safety_margin_ms=1_000,
        )

    def test_wire_shape_is_exact_and_contains_no_monotonic_epoch(self) -> None:
        self.assertEqual(
            {
                "schema_version": "1.0.0",
                "operation_id": "OP-001",
                "deadline_at_utc": "2026-08-01T02:15:00Z",
                "budget_ms": 25_000,
                "sent_at_utc": "2026-08-01T02:14:34Z",
                "safety_margin_ms": 1_000,
            },
            self.dto.to_wire(),
        )
        self.assertFalse(any("monotonic" in key for key in self.dto.to_wire()))

    def test_receiver_uses_minimum_limit_and_its_local_monotonic_clock(self) -> None:
        local = build_local_deadline(
            self.dto,
            provider_timeout_ms=8_000,
            now_utc=datetime(2026, 8, 1, 2, 14, 50, tzinfo=UTC),
            now_monotonic_ms=900_000,
            runtime_id="runtime-receiver-a",
        )
        self.assertEqual(8_000, local.effective_timeout_ms)
        self.assertEqual(908_000, local.deadline_monotonic_ms)
        self.assertEqual("runtime-receiver-a", local.runtime_id)

    def test_utc_remaining_minus_margin_can_be_the_smallest_limit(self) -> None:
        local = build_local_deadline(
            self.dto,
            provider_timeout_ms=30_000,
            now_utc=datetime(2026, 8, 1, 2, 14, 58, 500_000, tzinfo=UTC),
            now_monotonic_ms=1_000,
            runtime_id="runtime-receiver-b",
        )
        self.assertEqual(500, local.effective_timeout_ms)
        self.assertEqual(1_500, local.deadline_monotonic_ms)

    def test_expired_deadline_fails_before_io(self) -> None:
        with self.assertRaises(DeadlineExceededError):
            build_local_deadline(
                self.dto,
                provider_timeout_ms=10_000,
                now_utc=datetime(2026, 8, 1, 2, 15, 0, tzinfo=UTC),
                now_monotonic_ms=1,
                runtime_id="runtime-expired",
            )

    def test_schema_bounds_and_identifiers_are_enforced(self) -> None:
        invalid_overrides = (
            {"schema_version": "2.0.0"},
            {"operation_id": "client supplied"},
            {"budget_ms": 0},
            {"budget_ms": 900_001},
            {"safety_margin_ms": 99},
            {"safety_margin_ms": 5_001},
        )
        base = self.dto.to_wire()
        for override in invalid_overrides:
            with self.subTest(override=override):
                with self.assertRaises(ContractValidationError):
                    DeadlineDTO(**(base | override))


class ErrorEnvelopeContractTests(unittest.TestCase):
    def test_unknown_exception_maps_to_safe_typed_error(self) -> None:
        error = map_unexpected_exception(
            RuntimeError("Authorization: Bearer secret-token"),
            provider="fake_repository",
            operation_id="OP-ERR-001",
            occurred_at=datetime(2026, 8, 1, 2, 0, tzinfo=UTC),
        )
        wire = error.to_wire()
        self.assertEqual("unexpected_provider_error", wire["code"])
        self.assertEqual(PortErrorCategory.UNEXPECTED.value, wire["category"])
        self.assertFalse(wire["retryable"])
        self.assertEqual({}, wire["details"])
        self.assertNotIn("secret-token", repr(wire))
        self.assertNotIn("RuntimeError", repr(wire))

    def test_error_result_has_frozen_status_envelope(self) -> None:
        error = map_unexpected_exception(
            Exception("vendor payload"),
            provider="fake_repository",
            operation_id="OP-ERR-002",
            occurred_at=datetime(2026, 8, 1, 2, 0, tzinfo=UTC),
        )
        self.assertEqual(
            {"status": "error", "error": error.to_wire()},
            error.as_result().to_wire(),
        )


if __name__ == "__main__":
    unittest.main()
