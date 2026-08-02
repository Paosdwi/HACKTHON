from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.evidence_strategies import (
    ContradictionCandidateDTO,
    EvidenceStrategyItemDTO,
)
from crypto_trust_agent.application.use_cases.evaluate_evidence_strategies import (
    EvaluateEvidenceStrategiesCommand,
    EvaluateEvidenceStrategiesUseCase,
)
from crypto_trust_agent.infrastructure.fakes.evidence_strategies import (
    FakeConfidenceCompositionStrategy,
    FakeContradictionDetectionStrategy,
    FakeDuplicateDetectionStrategy,
    FakeIndependenceGroupingStrategy,
    FakeTrustComponentStrategy,
)

RULESET = "fake-evidence-strategy-1.0.0"


class EvidenceStrategyPipelineIntegrationTests(unittest.TestCase):
    def test_pipeline_is_reproducible_retains_counter_evidence_and_exposes_components(self) -> None:
        items = (
            EvidenceStrategyItemDTO(
                "EVID-SUPPORT", "TASK-001", "https://example.test/event",
                "sha256:" + "a" * 64, "BTC event", ("BTC",),
                "2026-08-01T00:00:00Z", "supports",
            ),
            EvidenceStrategyItemDTO(
                "EVID-COUNTER", "TASK-001", "https://example.test/event",
                "sha256:" + "b" * 64, "BTC event", ("BTC",),
                "2026-08-01T00:00:00Z", "contradicts",
            ),
        )
        candidates = (
            ContradictionCandidateDTO(
                "EVID-SUPPORT", "TASK-001", "signal", "btc-signal", "positive"
            ),
            ContradictionCandidateDTO(
                "EVID-COUNTER", "TASK-001", "signal", "btc-signal", "negative"
            ),
        )
        use_case = EvaluateEvidenceStrategiesUseCase(
            FakeDuplicateDetectionStrategy(RULESET),
            FakeIndependenceGroupingStrategy(RULESET),
            FakeTrustComponentStrategy(RULESET),
            FakeContradictionDetectionStrategy(RULESET),
            FakeConfidenceCompositionStrategy(RULESET),
        )
        command = EvaluateEvidenceStrategiesCommand(
            "TASK-001", RULESET, items, candidates
        )
        first = use_case.execute(command)
        second = use_case.execute(command)
        self.assertEqual(first, second)
        self.assertEqual(("EVID-COUNTER",), first.duplicates.counter_evidence_ids)
        self.assertEqual(
            ("EVID-COUNTER", "EVID-SUPPORT"),
            first.duplicates.retained_evidence_ids,
        )
        self.assertEqual(1, first.independence.corroboration_count)
        self.assertEqual("signal", first.contradictions.items[0].conflict_type.value)
        self.assertEqual(
            {"source_trust", "relevance", "freshness", "independence", "consistency"},
            set(first.confidence.component_scores),
        )
        self.assertEqual(RULESET, first.confidence.ruleset_version)


if __name__ == "__main__":
    unittest.main()
