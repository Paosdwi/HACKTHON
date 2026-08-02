"""Focused tests for the public demo reasoning instructions."""

from __future__ import annotations

import unittest

from crypto_trust_agent.infrastructure.reasoning.demo_executor import (
    AwsDemoFormalRunStepExecutor,
)


class DemoExecutorTests(unittest.TestCase):
    def test_reasoning_question_requires_every_selected_asset(self) -> None:
        assets = ("BTC", "ETH", "SOL", "BNB", "XRP")
        question = AwsDemoFormalRunStepExecutor._analysis_question(
            "比較所選幣種的市場訊號。",
            assets,
        )

        self.assertIn("指定分析幣種：BTC、ETH、SOL、BNB、XRP", question)
        self.assertIn("不得省略", question)
        self.assertIn("資料不足", question)
        for asset in assets:
            self.assertIn(asset, question)


if __name__ == "__main__":
    unittest.main()
