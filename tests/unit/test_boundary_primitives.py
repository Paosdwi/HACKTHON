from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.domain.primitives import (  # noqa: E402
    CanonicalDecimal,
    ContractValidationError,
    SchemaVersion,
    UtcInstant,
)


class SchemaVersionTests(unittest.TestCase):
    def test_only_frozen_v1_version_is_accepted(self) -> None:
        self.assertEqual("1.0.0", str(SchemaVersion("1.0.0")))
        for invalid in ("1.0", "1.0.1", "v1.0.0", "2.0.0"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ContractValidationError):
                    SchemaVersion(invalid)


class UtcInstantTests(unittest.TestCase):
    def test_requires_rfc3339_utc_z_and_round_trips(self) -> None:
        value = UtcInstant("2026-08-01T02:15:00.123456Z")
        self.assertEqual("2026-08-01T02:15:00.123456Z", str(value))
        self.assertEqual(
            datetime(2026, 8, 1, 2, 15, 0, 123456, tzinfo=UTC),
            value.as_datetime(),
        )

    def test_rejects_non_utc_or_malformed_timestamp(self) -> None:
        for invalid in (
            "2026-08-01T02:15:00+00:00",
            "2026-08-01T10:15:00+08:00",
            "2026-08-01 02:15:00Z",
            "not-a-timestamp",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ContractValidationError):
                    UtcInstant(invalid)


class CanonicalDecimalTests(unittest.TestCase):
    def test_valid_canonical_values_parse_without_binary_float(self) -> None:
        for raw in (
            "0",
            "1",
            "-1",
            "0.42",
            "1000.000000000000000001",
            "9" * 38,
            "0." + "0" * 17 + "1",
        ):
            with self.subTest(raw=raw):
                value = CanonicalDecimal(raw)
                self.assertEqual(raw, str(value))
                self.assertEqual(Decimal(raw), value.as_decimal())

    def test_noncanonical_or_out_of_bounds_values_are_rejected(self) -> None:
        invalid_values = (
            "01",
            "1.0",
            "+1",
            "1e3",
            "-0",
            ".5",
            "9" * 39,
            "0." + "0" * 18 + "1",
            1,
            0.5,
        )
        for raw in invalid_values:
            with self.subTest(raw=raw):
                with self.assertRaises(ContractValidationError):
                    CanonicalDecimal(raw)  # type: ignore[arg-type]

    def test_domain_ranges_are_explicit(self) -> None:
        CanonicalDecimal("0").require_nonnegative()
        CanonicalDecimal("1").require_probability()
        CanonicalDecimal("0.000001").require_probability()
        with self.assertRaises(ContractValidationError):
            CanonicalDecimal("-0.1").require_nonnegative()
        with self.assertRaises(ContractValidationError):
            CanonicalDecimal("1.000001").require_probability()


if __name__ == "__main__":
    unittest.main()
