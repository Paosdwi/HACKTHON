from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.evidence_extractor import (  # noqa: E402
    ExtractionResultDTO,
)
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock  # noqa: E402
from crypto_trust_agent.infrastructure.fakes.evidence_extractor_v2 import (  # noqa: E402
    FakeEvidenceExtractorV2,
    LateRepairResult,
    LocatorResolution,
)
from tests.contract.shared_evidence_extractor_v2_assertions import (  # noqa: E402
    EvidenceExtractorV2ContractAssertions,
)


class FakeEvidenceExtractorV2ContractTests(
    EvidenceExtractorV2ContractAssertions,
    unittest.TestCase,
):
    def make_extractor(self) -> FakeEvidenceExtractorV2:
        return FakeEvidenceExtractorV2(FakeClock("2026-08-01T02:00:00Z"))

    def configure_repair(
        self,
        extractor: FakeEvidenceExtractorV2,
        operation_id: str,
        response: object,
    ) -> None:
        extractor.configure_repair(operation_id, response)

    def configure_locator(
        self,
        extractor: FakeEvidenceExtractorV2,
        locator: str,
        *,
        clean_content: str | None = None,
        elapsed_ms: int = 0,
        error: BaseException | None = None,
    ) -> None:
        if error is not None:
            response: object = error
        else:
            if clean_content is None:
                raise AssertionError("clean_content is required for locator success")
            response = LocatorResolution(clean_content, elapsed_ms=elapsed_ms)
        extractor.configure_locator(locator, response)

    def configure_late_repair(
        self,
        extractor: FakeEvidenceExtractorV2,
        operation_id: str,
        result: ExtractionResultDTO,
        *,
        elapsed_ms: int,
    ) -> None:
        extractor.configure_repair(
            operation_id,
            LateRepairResult(result, elapsed_ms=elapsed_ms),
        )

    def provider_invocation_count(self, extractor: FakeEvidenceExtractorV2) -> int:
        return extractor.provider_invocation_count

    def locator_resolution_count(self, extractor: FakeEvidenceExtractorV2) -> int:
        return extractor.locator_resolution_count

    def test_fake_is_explicitly_non_production(self) -> None:
        self.assertTrue(self.make_extractor().non_production)


if __name__ == "__main__":
    unittest.main()
