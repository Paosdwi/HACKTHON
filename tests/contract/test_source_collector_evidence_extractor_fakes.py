from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.infrastructure.fakes.clock import FakeClock  # noqa: E402
from crypto_trust_agent.infrastructure.fakes.evidence_extractor import FakeEvidenceExtractor  # noqa: E402
from crypto_trust_agent.infrastructure.fakes.source_collector import FakeSourceCollector  # noqa: E402
from crypto_trust_agent.application.ports import EvidenceExtractor, SourceCollector  # noqa: E402
from crypto_trust_agent.infrastructure.fakes import (  # noqa: E402
    FakeEvidenceExtractor as ExportedFakeEvidenceExtractor,
    FakeSourceCollector as ExportedFakeSourceCollector,
)
from tests.contract.shared_collector_extractor_assertions import (  # noqa: E402
    EvidenceExtractorContractAssertions,
    SourceCollectorContractAssertions,
)


class FakeSourceCollectorContractTests(SourceCollectorContractAssertions, unittest.TestCase):
    def make_collector(self) -> FakeSourceCollector:
        return FakeSourceCollector(
            FakeClock("2026-08-01T02:00:00Z"),
            provider="fake_collector",
            allowed_hosts=("example.com",),
        )

    def configure_collect(self, collector: FakeSourceCollector, operation_id: str, response: object, **metadata: object) -> None:
        collector.configure(operation_id, response, **metadata)


class FakeEvidenceExtractorContractTests(EvidenceExtractorContractAssertions, unittest.TestCase):
    def make_extractor(self) -> FakeEvidenceExtractor:
        return FakeEvidenceExtractor(FakeClock("2026-08-01T02:00:00Z"))

    def configure_extract(self, extractor: FakeEvidenceExtractor, operation_id: str, response: object) -> None:
        extractor.configure_extract(operation_id, response)

    def configure_repair(self, extractor: FakeEvidenceExtractor, operation_id: str, response: object) -> None:
        extractor.configure_repair(operation_id, response)


class PublicExportContractTests(unittest.TestCase):
    def test_formal_package_exports_resolve_to_public_types(self) -> None:
        self.assertIs(ExportedFakeSourceCollector, FakeSourceCollector)
        self.assertIs(ExportedFakeEvidenceExtractor, FakeEvidenceExtractor)
        self.assertEqual("SourceCollector", SourceCollector.__name__)
        self.assertEqual("EvidenceExtractor", EvidenceExtractor.__name__)


if __name__ == "__main__":
    unittest.main()
