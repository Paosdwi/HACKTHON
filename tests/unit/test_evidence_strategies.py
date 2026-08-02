from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.evidence_strategies import (
    ConfidenceCompositionRequestDTO,
    ContradictionCandidateDTO,
    ContradictionDetectionRequestDTO,
    DuplicateDetectionRequestDTO,
    EvidenceStrategyItemDTO,
    IndependenceGroupingRequestDTO,
    TrustScoringRequestDTO,
)
from crypto_trust_agent.application.ports.evidence_strategies import (
    ConfidenceCompositionStrategy,
    ContradictionDetectionStrategy,
    DuplicateDetectionStrategy,
    IndependenceGroupingStrategy,
    TrustComponentStrategy,
)
from crypto_trust_agent.infrastructure.fakes.evidence_strategies import (
    FakeConfidenceCompositionStrategy,
    FakeContradictionDetectionStrategy,
    FakeDuplicateDetectionStrategy,
    FakeIndependenceGroupingStrategy,
    FakeTrustComponentStrategy,
)

RULESET = "fake-evidence-strategy-1.0.0"
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64


def item(
    evidence_id: str,
    *,
    url: str,
    content_hash: str,
    stance: str = "supports",
) -> EvidenceStrategyItemDTO:
    return EvidenceStrategyItemDTO(
        evidence_id=evidence_id,
        task_id="TASK-001",
        canonical_url=url,
        content_hash=content_hash,
        title="BTC update",
        event_entities=("BTC",),
        event_time="2026-08-01T00:00:00Z",
        stance=stance,
    )


class EvidenceStrategyFakeUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = (
            item("EVID-B", url="https://example.test/same", content_hash=HASH_A),
            item(
                "EVID-A",
                url="https://example.test/same",
                content_hash=HASH_B,
                stance="contradicts",
            ),
            item("EVID-C", url="https://example.test/other", content_hash=HASH_C),
        )
        self.duplicates = FakeDuplicateDetectionStrategy(RULESET)
        self.independence = FakeIndependenceGroupingStrategy(RULESET)
        self.trust = FakeTrustComponentStrategy(RULESET)
        self.contradictions = FakeContradictionDetectionStrategy(RULESET)
        self.confidence = FakeConfidenceCompositionStrategy(RULESET)

    def test_five_runtime_protocols_and_fakes_are_explicitly_non_production(self) -> None:
        pairs = (
            (self.duplicates, DuplicateDetectionStrategy),
            (self.independence, IndependenceGroupingStrategy),
            (self.trust, TrustComponentStrategy),
            (self.contradictions, ContradictionDetectionStrategy),
            (self.confidence, ConfidenceCompositionStrategy),
        )
        for fake, protocol in pairs:
            with self.subTest(protocol=protocol.__name__):
                self.assertIsInstance(fake, protocol)
                self.assertTrue(fake.non_production)
                self.assertEqual(RULESET, fake.ruleset_version)

    def test_duplicate_groups_are_deterministic_with_stable_fake_tie_break_and_counter_retention(self) -> None:
        request = DuplicateDetectionRequestDTO("TASK-001", RULESET, self.items)
        first = self.duplicates.detect(request)
        second = self.duplicates.detect(request)
        self.assertEqual(first, second)
        duplicate = next(group for group in first.groups if len(group.member_ids) == 2)
        self.assertEqual(("EVID-A", "EVID-B"), duplicate.member_ids)
        self.assertEqual("EVID-A", duplicate.representative_id)
        self.assertEqual(("EVID-A", "EVID-B", "EVID-C"), first.retained_evidence_ids)
        self.assertEqual(("EVID-A",), first.counter_evidence_ids)

    def test_independence_groups_count_each_group_once_without_dropping_members(self) -> None:
        duplicate_result = self.duplicates.detect(
            DuplicateDetectionRequestDTO("TASK-001", RULESET, self.items)
        )
        grouped = self.independence.group(
            IndependenceGroupingRequestDTO(
                "TASK-001", RULESET, self.items, duplicate_result.groups
            )
        )
        duplicate_group = next(group for group in grouped.groups if len(group.member_ids) == 2)
        self.assertEqual(1, duplicate_group.corroboration_count)
        self.assertEqual("EVID-A", duplicate_group.representative_id)
        self.assertEqual(
            {"EVID-A", "EVID-B", "EVID-C"},
            {member for group in grouped.groups for member in group.member_ids},
        )
        self.assertEqual(2, grouped.corroboration_count)

    def test_all_six_contradiction_types_preserve_distinct_bilateral_refs(self) -> None:
        conflict_types = ("numeric", "temporal", "source", "narrative", "signal", "status")
        candidates = tuple(
            ContradictionCandidateDTO(
                f"REF-{kind.upper()}-{side}",
                "TASK-001",
                kind,
                f"KEY-{kind}",
                value,
            )
            for kind in conflict_types
            for side, value in (("LEFT", "alpha"), ("RIGHT", "beta"))
        )
        result = self.contradictions.detect(
            ContradictionDetectionRequestDTO("TASK-001", RULESET, candidates)
        )
        self.assertEqual(set(conflict_types), {item.conflict_type.value for item in result.items})
        self.assertEqual(6, len(result.items))
        for contradiction in result.items:
            self.assertNotEqual(contradiction.left_ref, contradiction.right_ref)
            self.assertEqual(RULESET, contradiction.ruleset_version)

    def test_fake_trust_exposes_five_components_and_confidence_is_decomposable(self) -> None:
        two_items = self.items[:2]
        duplicate_result = self.duplicates.detect(
            DuplicateDetectionRequestDTO("TASK-001", RULESET, two_items)
        )
        grouping = self.independence.group(
            IndependenceGroupingRequestDTO(
                "TASK-001", RULESET, two_items, duplicate_result.groups
            )
        )
        trust = self.trust.score(
            TrustScoringRequestDTO("TASK-001", RULESET, two_items, grouping.groups)
        )
        self.assertEqual(2, len(trust.items))
        representative = next(item for item in trust.items if item.evidence_id == "EVID-A")
        duplicate = next(item for item in trust.items if item.evidence_id == "EVID-B")
        self.assertEqual("1", str(representative.independence))
        self.assertEqual("0", str(duplicate.independence))
        confidence = self.confidence.compose(
            ConfidenceCompositionRequestDTO(
                "TASK-001", RULESET, trust.items, ("CON-001",)
            )
        )
        self.assertEqual(
            {"source_trust", "relevance", "freshness", "independence", "consistency"},
            set(confidence.component_scores),
        )
        self.assertEqual("0.65", str(confidence.score))
        self.assertEqual(("CON-001",), confidence.contradiction_ids)

    def test_ruleset_mismatch_is_rejected_instead_of_silently_substituted(self) -> None:
        with self.assertRaises(ValueError):
            self.duplicates.detect(
                DuplicateDetectionRequestDTO("TASK-001", "other-1.0.0", self.items)
            )


if __name__ == "__main__":
    unittest.main()
