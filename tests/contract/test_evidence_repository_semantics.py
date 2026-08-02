from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from crypto_trust_agent.infrastructure.fakes import FakeClock, FakeEvidenceRepository
from tests.contract.evidence_repository_semantics import EvidenceRepositorySemanticAssertions


class FakeEvidenceRepositorySemanticTests(EvidenceRepositorySemanticAssertions, unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock("2026-08-01T02:00:00Z")
        self.repository = FakeEvidenceRepository(self.clock, snapshot_ttl_seconds=1)


class FakeEvidenceRepositoryBackwardCompatibilityTests(unittest.TestCase):
    def test_existing_constructor_keeps_non_expiring_fake_behavior(self) -> None:
        clock = FakeClock("2026-08-01T02:00:00Z")
        repository = FakeEvidenceRepository(clock)
        self.assertTrue(repository.non_production)
        self.assertIsNone(repository.snapshot_ttl_seconds)


if __name__ == "__main__":
    unittest.main()
