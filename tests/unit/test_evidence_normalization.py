from __future__ import annotations

import hashlib
import sys
import unittest
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.evidence_extractor import (
    ExtractedClaimDTO,
    ExtractionProviderDTO,
    ExtractionResultDTO,
    UsageDTO,
)
from crypto_trust_agent.application.dto.source_collector import (
    QueryProvenanceDTO,
    RawRecordDTO,
    SecurityResultDTO,
)
from crypto_trust_agent.application.use_cases.normalize_evidence import (
    EvidenceNormalizationRejected,
    NormalizeEvidenceCommand,
    NormalizeEvidenceUseCase,
    canonicalize_source_url,
)
from crypto_trust_agent.infrastructure.fakes import FakeClock, FakeEvidenceRepository


class SequentialIds:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def __call__(self, prefix: str) -> str:
        value = self.counts.get(prefix, 0) + 1
        self.counts[prefix] = value
        return f"{prefix}{value:04d}"


def raw_record(clean_content: str = "Prefix 🚀 quote here suffix") -> RawRecordDTO:
    digest = "sha256:" + hashlib.sha256(clean_content.encode("utf-8")).hexdigest()
    return RawRecordDTO(
        raw_record_id="RAW-001",
        task_id="TASK-001",
        execution_id="EXEC-001",
        plan_job_id="JOB-001",
        source_name="Official Example",
        source_type="official",
        source_url="https://EXAMPLE.com:443/a/../release?b=2&a=1#fragment",
        canonical_url="https://EXAMPLE.com:443/release?b=2&a=1#fragment",
        http_status=200,
        published_at="2026-08-01T00:00:00Z",
        fetched_at="2026-08-01T01:00:00Z",
        content_hash="sha256:" + "a" * 64,
        clean_content_hash=digest,
        raw_locator="urn:cryptotrust:raw:RAW-001",
        media_type="text/plain",
        clean_content=clean_content,
        query_provenance=QueryProvenanceDTO(
            collector="official_collector",
            query="BTC release",
            parameters={"asset": "BTC"},
        ),
        security=SecurityResultDTO("1.0.0", True, True, True, 0),
    )


def extraction(*, outcome: str = "valid", quote: str = "🚀 quote") -> ExtractionResultDTO:
    claims = () if outcome != "valid" else (
        ExtractedClaimDTO(
            extracted_claim_id="XCL-001",
            text="A verifiable claim",
            quote=quote,
            related_assets=("BTC",),
            event_type="official_update",
            sentiment="neutral",
            relevance="high",
        ),
    )
    return ExtractionResultDTO(
        outcome=outcome,
        raw_record_id="RAW-001",
        provider=ExtractionProviderDTO("fake_extractor", "1.0.0", "invoke-001"),
        claims=claims,
        validation_errors=(),
        usage=UsageDTO(10, 5),
        started_at="2026-08-01T01:00:01Z",
        finished_at="2026-08-01T01:00:02Z",
    )


def command(**changes: object) -> NormalizeEvidenceCommand:
    values: dict[str, object] = {
        "operation_id": "OP-NORMALIZE-001",
        "task_id": "TASK-001",
        "execution_id": "EXEC-001",
        "raw_record_id": "RAW-001",
        "raw_content_hash": "sha256:" + "a" * 64,
        "raw_record": raw_record(),
        "extraction": extraction(),
        "claim_stances": {"XCL-001": "supports"},
    }
    values.update(changes)
    return NormalizeEvidenceCommand(**values)


class EvidenceNormalizationUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock("2026-08-01T01:01:00Z")
        self.repository = FakeEvidenceRepository(self.clock)
        self.ids = SequentialIds()
        self.use_case = NormalizeEvidenceUseCase(self.repository, self.clock, self.ids)

    def test_canonical_url_is_deterministic_and_pure(self) -> None:
        source = "https://EXAMPLE.com:443/a/../release?b=2&a=1#fragment"
        self.assertEqual("https://example.com/release?a=1&b=2", canonicalize_source_url(source))
        self.assertEqual(canonicalize_source_url(source), canonicalize_source_url(source))

    def test_normalizes_quote_with_unicode_scalar_half_open_offset_and_complete_lineage(self) -> None:
        result = self.use_case.execute(command())
        item = result.items[0]
        self.assertEqual("EVID-0001", item.evidence.evidence_id)
        self.assertEqual("LINK-0001", item.link.link_id)
        self.assertEqual("CLAIM-0001", item.link.claim_id)
        self.assertEqual("supports", item.link.stance)
        self.assertEqual("https://example.com/release?a=1&b=2", item.evidence.source_url)
        self.assertEqual("2026-08-01T01:00:00Z", str(item.evidence.fetched_at))
        self.assertEqual("urn:cryptotrust:raw:RAW-001", item.evidence.raw_locator)
        self.assertEqual("sha256:" + "a" * 64, item.evidence.raw_content_hash)
        self.assertEqual(raw_record().clean_content_hash, item.evidence.clean_content_hash)
        self.assertEqual("JOB-001", item.evidence.query_provenance["plan_job_id"])
        self.assertEqual(
            {"start": 7, "end": 14},
            dict(item.evidence.content_reference["offset"]),
        )
        self.assertEqual("unicode_scalar", item.evidence.content_reference["unit"])
        self.assertEqual("1.0.0", item.evidence.schema_version)
        self.assertEqual(item.evidence, self.repository._evidence[item.evidence.evidence_id])

    def test_all_supported_stances_are_preserved(self) -> None:
        for index, stance in enumerate(("supports", "contradicts", "context"), start=1):
            with self.subTest(stance=stance):
                repository = FakeEvidenceRepository(self.clock)
                use_case = NormalizeEvidenceUseCase(repository, self.clock, SequentialIds())
                result = use_case.execute(command(operation_id=f"OP-STANCE-{index}", claim_stances={"XCL-001": stance}))
                self.assertEqual(stance, result.items[0].link.stance)

    def test_same_operation_and_payload_replays_but_changed_payload_conflicts(self) -> None:
        first = self.use_case.execute(command())
        self.assertIs(first, self.use_case.execute(command()))
        with self.assertRaises(EvidenceNormalizationRejected) as raised:
            self.use_case.execute(command(claim_stances={"XCL-001": "context"}))
        self.assertEqual("operation_payload_conflict", raised.exception.code)
        self.assertEqual(1, len(self.repository._evidence))
        self.assertEqual(1, len(self.repository._links))

    def test_rejects_task_execution_raw_and_hash_mismatches(self) -> None:
        cases = (
            (command(task_id="TASK-OTHER"), "task_lineage_mismatch"),
            (command(execution_id="EXEC-OTHER"), "execution_lineage_mismatch"),
            (command(raw_record_id="RAW-OTHER"), "raw_record_lineage_mismatch"),
            (command(extraction=replace(extraction(), raw_record_id="RAW-OTHER")), "raw_record_lineage_mismatch"),
            (command(raw_content_hash="sha256:" + "b" * 64), "raw_content_hash_mismatch"),
            (command(raw_record=replace(raw_record(), clean_content_hash="sha256:" + "b" * 64)), "clean_content_hash_mismatch"),
        )
        for candidate, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(EvidenceNormalizationRejected) as raised:
                    self.use_case.execute(candidate)
                self.assertEqual(code, raised.exception.code)
        self.assertEqual({}, self.repository._evidence)

    def test_invalid_or_quarantined_extraction_never_creates_eligible_evidence(self) -> None:
        for outcome in ("invalid", "quarantined"):
            with self.subTest(outcome=outcome):
                with self.assertRaises(EvidenceNormalizationRejected) as raised:
                    self.use_case.execute(command(operation_id=f"OP-{outcome.upper()}", extraction=extraction(outcome=outcome), claim_stances={}))
                self.assertEqual("extraction_not_eligible", raised.exception.code)
        self.assertEqual({}, self.repository._evidence)
        self.assertEqual({}, self.repository._links)

    def test_quote_must_be_verifiable_in_clean_content_and_ids_are_validated(self) -> None:
        with self.assertRaises(EvidenceNormalizationRejected) as raised:
            self.use_case.execute(command(extraction=extraction(quote="not present")))
        self.assertEqual("quote_not_in_clean_content", raised.exception.code)

        class BadIds:
            def __call__(self, prefix: str) -> str:
                return "invalid"

        use_case = NormalizeEvidenceUseCase(FakeEvidenceRepository(self.clock), self.clock, BadIds())
        with self.assertRaises(EvidenceNormalizationRejected) as invalid_id:
            use_case.execute(command(operation_id="OP-BAD-ID"))
        self.assertEqual("invalid_generated_id", invalid_id.exception.code)


if __name__ == "__main__":
    unittest.main()
