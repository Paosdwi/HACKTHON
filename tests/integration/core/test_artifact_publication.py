from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from crypto_trust_agent.application.dto.repositories import ArtifactExecutionRequestDTO  # noqa: E402
from crypto_trust_agent.application.publication import (  # noqa: E402
    ArtifactPublicationService,
    PublicationRenderers,
)
from crypto_trust_agent.infrastructure.fakes.artifacts import FakeArtifactRepository  # noqa: E402
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock  # noqa: E402
from tests.contract.test_artifact_publication_fake import bundle, deadline  # noqa: E402


class ArtifactPublicationCoreIntegrationTests(unittest.TestCase):
    def test_local_fake_publishes_normal_and_degraded_bundles_without_network(self) -> None:
        normal_clock = FakeClock("2026-08-01T02:10:00Z")
        normal_fake = FakeArtifactRepository(normal_clock)
        normal = ArtifactPublicationService(normal_fake, normal_clock).publish(bundle())
        self.assertEqual("complete", normal.manifest.publication_outcome)
        normal_items = normal_fake.list_for_execution(ArtifactExecutionRequestDTO("OP-NORMAL-LIST", "TASK-001", "EXEC-001", deadline("OP-NORMAL-LIST"))).items
        self.assertEqual(7, len(normal_items))

        def fail(_value):
            raise RuntimeError("renderer unavailable")

        degraded_clock = FakeClock("2026-08-01T02:10:00Z")
        degraded_fake = FakeArtifactRepository(degraded_clock)
        degraded = ArtifactPublicationService(
            degraded_fake,
            degraded_clock,
            renderers=PublicationRenderers(markdown=fail, html=fail, csv=fail),
        ).publish(bundle())
        self.assertEqual("partial", degraded.manifest.publication_outcome)
        degraded_items = degraded_fake.list_for_execution(ArtifactExecutionRequestDTO("OP-DEGRADED-LIST", "TASK-001", "EXEC-001", deadline("OP-DEGRADED-LIST"))).items
        self.assertEqual({"final_report", "evidence_list", "execution_log", "manifest"}, {item.artifact_type for item in degraded_items})


if __name__ == "__main__":
    unittest.main()
