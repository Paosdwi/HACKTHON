from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.domain.fingerprint import (  # noqa: E402
    FINGERPRINT_RULESET_VERSION,
    FingerprintValidationError,
    create_request_fingerprint,
)


class RequestFingerprintTests(unittest.TestCase):
    def test_nfkc_whitespace_assets_and_timezone_have_one_canonical_payload(self) -> None:
        result = create_request_fingerprint(
            question=" \u3000ＢＴＣ\t price\nup?  ",
            assets=("eth", "bTc"),
            timeframe_start="2026-08-01T08:00:00+08:00",
            timeframe_end="2026-08-02T00:00:00Z",
        )
        expected_json = (
            b'{"assets_canonical":["BTC","ETH"],"question":"BTC price up?",'
            b'"timeframe":{"end":"2026-08-02T00:00:00Z",'
            b'"start":"2026-08-01T00:00:00Z"}}'
        )
        self.assertEqual("BTC price up?", result.normalized_question)
        self.assertEqual(("ETH", "BTC"), result.assets_requested_order)
        self.assertEqual(("BTC", "ETH"), result.assets_canonical)
        self.assertEqual(expected_json, result.canonical_json)
        self.assertEqual(
            "sha256:" + hashlib.sha256(expected_json).hexdigest(),
            result.request_fingerprint,
        )
        self.assertEqual(FINGERPRINT_RULESET_VERSION, result.ruleset_version)

    def test_same_canonical_input_is_deterministic_and_order_independent(self) -> None:
        first = create_request_fingerprint(
            question="BTC\u00a0status",
            assets=("BTC", "ETH"),
            timeframe_start="2026-08-01T00:00:00Z",
            timeframe_end="2026-08-02T00:00:00Z",
        )
        second = create_request_fingerprint(
            question="BTC status",
            assets=("eth", "btc"),
            timeframe_start="2026-08-01T00:00:00+00:00",
            timeframe_end="2026-08-02T00:00:00+00:00",
        )
        self.assertEqual(first.request_fingerprint, second.request_fingerprint)
        self.assertNotEqual(first.assets_requested_order, second.assets_requested_order)

    def test_each_canonical_field_changes_the_digest(self) -> None:
        base = create_request_fingerprint(
            question="BTC status?",
            assets=("BTC",),
            timeframe_start="2026-08-01T00:00:00Z",
            timeframe_end="2026-08-02T00:00:00Z",
        )
        changed = create_request_fingerprint(
            question="BTC status!",
            assets=("BTC",),
            timeframe_start="2026-08-01T00:00:00Z",
            timeframe_end="2026-08-02T00:00:00Z",
        )
        self.assertNotEqual(base.request_fingerprint, changed.request_fingerprint)

    def test_duplicate_or_unsupported_assets_are_rejected_not_repaired(self) -> None:
        for assets in (("btc", "BTC"), ("DOGE",), ()):
            with self.subTest(assets=assets):
                with self.assertRaises(FingerprintValidationError):
                    create_request_fingerprint(
                        question="status",
                        assets=assets,
                        timeframe_start="2026-08-01T00:00:00Z",
                        timeframe_end="2026-08-02T00:00:00Z",
                    )

    def test_naive_or_non_minute_time_is_rejected_not_truncated(self) -> None:
        invalid_times = (
            "2026-08-01T00:00:00",
            "2026-08-01T00:00:01Z",
            "2026-08-01T00:00:00.000001Z",
            "not-a-time",
        )
        for invalid in invalid_times:
            with self.subTest(invalid=invalid):
                with self.assertRaises(FingerprintValidationError):
                    create_request_fingerprint(
                        question="status",
                        assets=("BTC",),
                        timeframe_start=invalid,
                        timeframe_end="2026-08-02T00:00:00Z",
                    )


if __name__ == "__main__":
    unittest.main()
