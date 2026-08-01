from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.planning import (  # noqa: E402
    PLANNER_RULESET_VERSION,
    QuestionType,
    RequirementLevel,
    build_plan,
    source_requirement_matrix,
)
from crypto_trust_agent.domain.fingerprint import create_request_fingerprint  # noqa: E402


class DeterministicPlannerTests(unittest.TestCase):
    def fingerprint(self, assets: tuple[str, ...]):
        return create_request_fingerprint(
            question="Compare market evidence",
            assets=assets,
            timeframe_start="2026-07-01T00:00:00Z",
            timeframe_end="2026-08-01T00:00:00Z",
        )

    def test_source_requirement_matrix_matches_approved_spec(self) -> None:
        self.assertEqual(
            RequirementLevel.REQUIRED_IF_AVAILABLE,
            source_requirement_matrix(QuestionType.HYPOTHESIS_VALIDATION)["on_chain"],
        )
        for question_type in QuestionType:
            matrix = source_requirement_matrix(question_type)
            self.assertEqual(RequirementLevel.REQUIRED, matrix["market"])
            self.assertEqual(RequirementLevel.REQUIRED, matrix["news"])
            self.assertEqual(RequirementLevel.REQUIRED, matrix["official"])

    def test_all_three_question_types_produce_versioned_fixed_plans(self) -> None:
        for question_type in QuestionType:
            with self.subTest(question_type=question_type):
                plan = build_plan(
                    question_type=question_type,
                    fingerprint=self.fingerprint(("BTC",)),
                    clock_snapshot="2026-08-01T00:00:00Z",
                )
                self.assertEqual(PLANNER_RULESET_VERSION, plan.ruleset_version)
                self.assertTrue(plan.answer_dimensions)
                self.assertTrue(plan.analysis_steps)
                self.assertEqual(6, len(plan.sourcing_jobs))
                market_job = next(job for job in plan.sourcing_jobs if job.category == "market")
                self.assertEqual(RequirementLevel.REQUIRED, market_job.requirement)
                self.assertTrue(market_job.query.startswith("official_dataset:"))

    def test_same_input_ruleset_and_clock_is_byte_equivalent(self) -> None:
        fingerprint = self.fingerprint(("BTC",))
        first = build_plan(
            question_type=QuestionType.MARKET_STATUS,
            fingerprint=fingerprint,
            clock_snapshot="2026-08-01T00:00:00Z",
        )
        second = build_plan(
            question_type=QuestionType.MARKET_STATUS,
            fingerprint=fingerprint,
            clock_snapshot="2026-08-01T00:00:00Z",
        )
        self.assertEqual(first.canonical_json, second.canonical_json)
        self.assertEqual(first.canonical_hash, second.canonical_hash)

    def test_asset_comparison_preserves_requested_order_and_first_asset_priority(self) -> None:
        plan = build_plan(
            question_type=QuestionType.ASSET_COMPARISON,
            fingerprint=self.fingerprint(("ETH", "BTC")),
            clock_snapshot="2026-08-01T00:00:00Z",
        )
        self.assertEqual(("ETH", "BTC"), plan.assets_requested_order)
        eth_priorities = [job.priority for job in plan.sourcing_jobs if job.asset == "ETH"]
        btc_priorities = [job.priority for job in plan.sourcing_jobs if job.asset == "BTC"]
        self.assertLess(max(eth_priorities), min(btc_priorities))


if __name__ == "__main__":
    unittest.main()
