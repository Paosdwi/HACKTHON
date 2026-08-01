"""Reusable SourceCollector/EvidenceExtractor contract assertions.

Core fakes and future Provider adapter harnesses must run these same mixins.  A
provider harness only needs to implement the factory/configuration hooks used by
the behavioral tests; schema policy checks are adapter-independent.
"""
from __future__ import annotations

import json
import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.evidence_extractor import (
    ExtractRequestDTO,
    ExtractedClaimDTO,
    ExtractionProviderDTO,
    ExtractionResultDTO,
    ExtractorHealthCheckRequestDTO,
    InlineContentInputDTO,
    LocatorContentInputDTO,
    RepairRequestDTO,
    UsageDTO,
    ValidationErrorDTO,
)
from crypto_trust_agent.application.dto.source_collector import (
    CapabilitiesRequestDTO,
    CollectionIssueDTO,
    CollectionResultDTO,
    CollectRequestDTO,
    CollectorCapabilitiesDTO,
    CollectorHealthCheckRequestDTO,
    QueryProvenanceDTO,
    RangeDTO,
    RawRecordDTO,
    SecurityResultDTO,
)
from crypto_trust_agent.application.ports.evidence_extractor import EvidenceExtractor
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO
from crypto_trust_agent.application.ports.source_collector import SourceCollector
from crypto_trust_agent.domain.primitives import ContractValidationError

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
NOW = "2026-08-01T02:00:00Z"
LATER = "2026-08-01T02:01:00Z"


def deadline(operation_id: str, *, budget_ms: int = 60_000, at: str = "2026-08-01T03:00:00Z") -> DeadlineDTO:
    return DeadlineDTO("1.0.0", operation_id, at, budget_ms, NOW, 100)


def collect_request(operation_id: str = "OP-COL-001", *, urls: tuple[str, ...] = ("https://example.com/article",), mode: str = "static") -> CollectRequestDTO:
    return CollectRequestDTO(
        operation_id=operation_id,
        task_id="TASK-001",
        execution_id="EXEC-001",
        plan_job_id="JOB-001",
        source_category="news",
        collection_mode=mode,
        requirement="required",
        assets=("BTC",),
        approved_query="BTC regulatory announcement",
        approved_urls=urls,
        reporting_range=RangeDTO("2026-07-18T00:00:00Z", "2026-08-01T00:00:00Z"),
        priority=1,
        security_policy_version="collector-security-1.0.0",
        deadline=deadline(operation_id, budget_ms=30_000),
    )


def raw_record(*, clean_content: str = "bounded source text", event_url: str = "https://example.com/article") -> RawRecordDTO:
    return RawRecordDTO(
        raw_record_id="RAW-001",
        task_id="TASK-001",
        execution_id="EXEC-001",
        plan_job_id="JOB-001",
        source_name="Example",
        source_type="news",
        source_url=event_url,
        canonical_url=event_url,
        http_status=200,
        published_at=None,
        fetched_at=NOW,
        content_hash=HASH_A,
        clean_content_hash=HASH_B,
        raw_locator="urn:cryptotrust:raw:RAW-001",
        media_type="text/html",
        clean_content=clean_content,
        query_provenance=QueryProvenanceDTO("official_news", "BTC regulatory announcement", {"asset": "BTC"}),
        security=SecurityResultDTO("1.0.0", True, True, True, 0),
    )


def collection_result(operation_id: str = "OP-COL-001", *, outcome: str = "success") -> CollectionResultDTO:
    records = (raw_record(),) if outcome == "success" else ()
    issues = () if outcome == "success" else (CollectionIssueDTO("source_unavailable", False, "Source unavailable."),)
    return CollectionResultDTO(operation_id, outcome, records, NOW, LATER, 1_000, 0, issues, ())


def extract_request(operation_id: str = "OP-EXT-001", *, content: Any | None = None) -> ExtractRequestDTO:
    return ExtractRequestDTO(
        operation_id=operation_id,
        task_id="TASK-001",
        execution_id="EXEC-001",
        raw_record_id="RAW-001",
        raw_content_hash=HASH_A,
        content=content or InlineContentInputDTO("bounded source text"),
        assets=("BTC",),
        allowed_event_taxonomy=("regulatory",),
        output_schema_version="1.0.0",
        guardrail_policy_version="extraction-guardrail-1.0.0",
        deadline=deadline(operation_id),
    )


def extraction_result(*, outcome: str = "valid", event_type: str = "regulatory", assets: tuple[str, ...] = ("BTC",), invocation: str = "INV-001") -> ExtractionResultDTO:
    claim = ExtractedClaimDTO("XCL-001", "A bounded claim", "bounded source text", assets, event_type, "neutral", "high")
    errors = () if outcome == "valid" else (ValidationErrorDTO("/claims", "required_claim_missing", "At least one claim is required"),)
    return ExtractionResultDTO(
        outcome=outcome,
        raw_record_id="RAW-001",
        provider=ExtractionProviderDTO("fake_extractor", "fake-1.0.0", invocation),
        claims=(claim,) if outcome == "valid" else (),
        validation_errors=errors,
        usage=UsageDTO(100, 20),
        started_at=NOW,
        finished_at=LATER,
    )


def repair_request(operation_id: str = "OP-REP-001", *, context_hash: str = HASH_B) -> RepairRequestDTO:
    original = extraction_result(outcome="invalid")
    return RepairRequestDTO(
        operation_id=operation_id,
        task_id="TASK-001",
        execution_id="EXEC-001",
        raw_record_id="RAW-001",
        raw_content_hash=HASH_A,
        context_hash=context_hash,
        original_result=original,
        validator_errors=original.validation_errors,
        output_schema_version="1.0.0",
        guardrail_policy_version="extraction-guardrail-1.0.0",
        deadline=deadline(operation_id, budget_ms=20_000),
    )


def health_request(operation_id: str = "OP-EXT-HEALTH") -> ExtractorHealthCheckRequestDTO:
    return ExtractorHealthCheckRequestDTO(operation_id, deadline(operation_id, budget_ms=3_000))


def assert_schema_examples(test: unittest.TestCase, port: str) -> None:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    root = PROJECT_ROOT / "docs" / "architecture" / "schemas"
    common = json.loads((root / "common" / "common.schema.json").read_text(encoding="utf-8"))
    contract = json.loads((root / port / "contract.schema.json").read_text(encoding="utf-8"))
    registry = Registry().with_resource(common["$id"], Resource.from_contents(common))
    validator = Draft202012Validator(contract, registry=registry)
    for kind, expected_valid in (("valid-examples.json", True), ("invalid-examples.json", False)):
        examples = json.loads((root / port / kind).read_text(encoding="utf-8"))["examples"]
        for example in examples:
            errors = list(validator.iter_errors(example["value"]))
            test.assertEqual(expected_valid, not errors, example["test_id"])


def assert_operation_wire_valid(test: unittest.TestCase, port: str, method: str, request: object, response: object) -> None:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    root = PROJECT_ROOT / "docs" / "architecture" / "schemas"
    common = json.loads((root / "common" / "common.schema.json").read_text(encoding="utf-8"))
    contract = json.loads((root / port / "contract.schema.json").read_text(encoding="utf-8"))
    registry = Registry().with_resource(common["$id"], Resource.from_contents(common))
    value = {"method": method, "request": request.to_wire(), "response": response.to_wire()}
    test.assertEqual([], list(Draft202012Validator(contract, registry=registry).iter_errors(value)))


def assert_method_contracts(test: unittest.TestCase, port: str, expected: dict[str, dict[str, Any]]) -> None:
    contract = json.loads((PROJECT_ROOT / "docs" / "architecture" / "schemas" / port / "contract.schema.json").read_text(encoding="utf-8"))
    found: dict[str, dict[str, Any]] = {}
    for definition in contract["$defs"].values():
        identifier = definition.get("x-contract-test-id") if isinstance(definition, dict) else None
        if identifier:
            found[identifier] = definition["x-method-policy"]
    test.assertEqual(set(expected), set(found))
    for identifier, checks in expected.items():
        policy = found[identifier]
        for key, value in checks.items():
            test.assertEqual(value, policy[key], f"{identifier}:{key}")
        test.assertEqual(1, policy["max_attempts"])
        test.assertFalse(policy["hidden_adapter_retries"])
        test.assertIn("idempotency", policy)
        test.assertIn("concurrency", policy)
        test.assertTrue(policy["error_codes"])


class SourceCollectorContractAssertions:
    """Shared collector assertions; subclass with unittest.TestCase and hooks."""

    def make_collector(self):
        raise NotImplementedError

    def configure_collect(self, collector, operation_id: str, response: object, **metadata: object) -> None:
        raise NotImplementedError

    def test_collector_schema_examples_and_all_method_policies(self) -> None:
        assert_schema_examples(self, "source_collector")
        assert_method_contracts(self, "source_collector", {
            "CT-COLLECT-COLLECT-01": {"retry_owner": "orchestrator", "timeout_by_mode_ms": {"static": 15000, "playwright": 30000}, "error_codes": ["unsupported_category", "collector_not_configured", "source_not_allowlisted", "dns_ip_rejected", "ssrf_blocked", "robots_disallowed", "payload_too_large", "redirect_limit_exceeded", "source_rate_limited", "fetch_timeout", "invalid_source_schema", "collector_unavailable", "deadline_exceeded", "unexpected_provider_error"]},
            "CT-COLLECT-HEALTH-01": {"retry_owner": "orchestrator", "timeout_ms": 3000, "error_codes": ["collector_not_configured", "fetch_timeout", "collector_unavailable", "deadline_exceeded", "unexpected_provider_error"]},
            "CT-COLLECT-CAPABILITIES-01": {"retry_owner": "orchestrator", "timeout_ms": 3000, "error_codes": ["collector_not_configured", "collector_unavailable", "deadline_exceeded", "unexpected_provider_error"]},
        })

    def test_collector_runtime_protocol_and_non_production_marker(self) -> None:
        adapter = self.make_collector()
        self.assertIsInstance(adapter, SourceCollector)
        self.assertTrue(adapter.non_production)
        request = collect_request("OP-COL-DETERMINISTIC")
        self.assertEqual(adapter.collect(request), self.make_collector().collect(request))

    def test_collector_dtos_are_frozen_deeply_immutable_and_exact_wire(self) -> None:
        request = collect_request()
        with self.assertRaises(FrozenInstanceError):
            request.priority = 2
        with self.assertRaises(AttributeError):
            request.approved_urls.append("https://example.com/other")
        with self.assertRaises(TypeError):
            raw_record().query_provenance.parameters["asset"] = "ETH"
        wire = request.to_wire()
        self.assertEqual({"schema_version", "operation_id", "task_id", "execution_id", "plan_job_id", "source_category", "collection_mode", "requirement", "assets", "approved_query", "approved_urls", "reporting_range", "priority", "security_policy_version", "deadline"}, set(wire))
        self.assertEqual(["BTC"], wire["assets"])
        self.assertEqual("OP-COL-001", wire["deadline"]["operation_id"])
        record_wire = raw_record().to_wire()
        self.assertEqual({"schema_version", "raw_record_id", "task_id", "execution_id", "plan_job_id", "source_name", "source_type", "source_url", "canonical_url", "http_status", "published_at", "fetched_at", "content_hash", "clean_content_hash", "raw_locator", "media_type", "clean_content", "query_provenance", "security"}, set(record_wire))

    def test_collector_success_skipped_failed_and_completed_replay(self) -> None:
        adapter = self.make_collector()
        for index, outcome in enumerate(("success", "skipped", "failed")):
            operation = f"OP-COL-OUT-{index}"
            expected = collection_result(operation, outcome=outcome)
            self.configure_collect(adapter, operation, expected)
            request = collect_request(operation)
            first = adapter.collect(request)
            replay = adapter.collect(request)
            self.assertEqual(expected, first)
            self.assertIs(first, replay)
        new_result = adapter.collect(collect_request("OP-COL-NEW"))
        self.assertEqual("success", new_result.outcome)
        self.assertEqual(4, adapter.io_count)

    def test_collector_complete_raw_record_and_fixed_capabilities_health_side_effect_free(self) -> None:
        adapter = self.make_collector()
        request = collect_request()
        result = adapter.collect(request)
        assert_operation_wire_valid(self, "source_collector", "collect", request, result)
        record = result.records[0]
        self.assertEqual(("TASK-001", "EXEC-001", "JOB-001"), (record.task_id, record.execution_id, record.plan_job_id))
        self.assertEqual((HASH_A, HASH_B), (record.content_hash, record.clean_content_hash))
        self.assertEqual(f"urn:cryptotrust:raw:{record.raw_record_id}", record.raw_locator)
        self.assertEqual(("https://example.com/article", "https://example.com/article", 200), (record.source_url, record.canonical_url, record.http_status))
        self.assertEqual((NOW, "BTC regulatory announcement", "BTC"), (str(record.fetched_at), record.query_provenance.query, record.query_provenance.parameters["asset"]))
        self.assertEqual((True, True, True, 0), (record.security.url_allowed, record.security.dns_ip_validated, record.security.robots_allowed, record.security.redirect_count))
        self.assertTrue(record.security.url_allowed and record.security.dns_ip_validated and record.security.robots_allowed)
        before = adapter.io_count
        cap_op = "OP-COL-CAP"
        cap_request = CapabilitiesRequestDTO(cap_op, "fake_collector", deadline(cap_op, budget_ms=3_000))
        caps = adapter.capabilities(cap_request)
        self.assertEqual((5_242_880, 1_048_576, 3), (caps.max_raw_bytes, caps.max_clean_bytes, caps.max_redirects))
        assert_operation_wire_valid(self, "source_collector", "capabilities", cap_request, caps)
        health_op = "OP-COL-HEALTH"
        health_request_dto = CollectorHealthCheckRequestDTO(health_op, "fake_collector", deadline(health_op, budget_ms=3_000))
        health = adapter.health_check(health_request_dto)
        self.assertIsInstance(health, ProviderHealthDTO)
        assert_operation_wire_valid(self, "source_collector", "health_check", health_request_dto, health)
        self.assertEqual(before, adapter.io_count)

    def test_collector_rejects_unsafe_urls_before_io(self) -> None:
        cases = {
            "https://user:pass@example.com": "source_not_allowlisted",
            "https://unapproved.example/path": "source_not_allowlisted",
            "https://example.com:444/path": "source_not_allowlisted",
            "https://127.0.0.1/path": "dns_ip_rejected",
            "https://10.0.0.1/path": "dns_ip_rejected",
            "https://169.254.1.1/path": "dns_ip_rejected",
            "https://224.0.0.1/path": "dns_ip_rejected",
            "https://192.0.2.1/path": "dns_ip_rejected",
            "https://169.254.169.254/latest/meta-data": "ssrf_blocked",
        }
        for index, (url, code) in enumerate(cases.items()):
            with self.subTest(url=url):
                adapter = self.make_collector()
                result = adapter.collect(collect_request(f"OP-COL-URL-{index}", urls=(url,)))
                self.assertEqual(code, result.error.code)
                self.assertEqual(0, adapter.io_count)
        with self.assertRaises(ContractValidationError):
            collect_request("OP-COL-HTTP", urls=("http://example.com",))
        with self.assertRaises(ContractValidationError):
            collect_request("OP-COL-LONG", urls=("https://example.com/" + "x" * 2049,))

    def test_collector_redirect_payload_timeout_invalid_schema_and_unknown_exception(self) -> None:
        cases = (
            ("OP-COL-REDIRECT", collection_result("OP-COL-REDIRECT"), {"redirect_urls": ("https://example.com/1", "https://example.com/2", "https://example.com/3", "https://example.com/4")}, "redirect_limit_exceeded"),
            ("OP-COL-UNSAFE-REDIRECT", collection_result("OP-COL-UNSAFE-REDIRECT"), {"redirect_urls": ("https://127.0.0.1/x",)}, "dns_ip_rejected"),
            ("OP-COL-RAW", collection_result("OP-COL-RAW"), {"raw_decompressed_bytes": 5_242_881}, "payload_too_large"),
            ("OP-COL-CLEAN", collection_result("OP-COL-CLEAN"), {"cleaned_bytes": 1_048_577}, "payload_too_large"),
            ("OP-COL-TIMEOUT", "fetch_timeout", {}, "fetch_timeout"),
            ("OP-COL-ROBOTS", "robots_disallowed", {}, "robots_disallowed"),
            ("OP-COL-RATE", "source_rate_limited", {}, "source_rate_limited"),
            ("OP-COL-SCHEMA", "invalid_source_schema", {}, "invalid_source_schema"),
            ("OP-COL-EXCEPTION", RuntimeError("Authorization: Bearer secret"), {}, "unexpected_provider_error"),
        )
        for operation, response, metadata, code in cases:
            with self.subTest(operation=operation):
                adapter = self.make_collector()
                self.configure_collect(adapter, operation, response, **metadata)
                result = adapter.collect(collect_request(operation))
                self.assertIsInstance(result, ErrorResultDTO)
                self.assertEqual(code, result.error.code)
                self.assertNotIn("secret", repr(result.to_wire()).lower())

    def test_collector_deadline_before_io_and_operation_identity_conflict(self) -> None:
        adapter = self.make_collector()
        expired = collect_request("OP-COL-EXPIRED")
        expired = replace(expired, deadline=deadline("OP-COL-EXPIRED", budget_ms=1_000, at=NOW))
        self.assertEqual("deadline_exceeded", adapter.collect(expired).error.code)
        self.assertEqual(0, adapter.io_count)
        request = collect_request("OP-COL-IDENTITY")
        adapter.collect(request)
        changed = replace(request, approved_query="different approved query")
        self.assertEqual("invalid_source_schema", adapter.collect(changed).error.code)


class EvidenceExtractorContractAssertions:
    """Shared extractor assertions; subclass with unittest.TestCase and hooks."""

    def make_extractor(self):
        raise NotImplementedError

    def configure_extract(self, extractor, operation_id: str, response: object) -> None:
        raise NotImplementedError

    def configure_repair(self, extractor, operation_id: str, response: object) -> None:
        raise NotImplementedError

    def test_extractor_schema_examples_and_all_method_policies(self) -> None:
        assert_schema_examples(self, "evidence_extractor")
        assert_method_contracts(self, "evidence_extractor", {
            "CT-EXTRACT-EXTRACT-01": {"retry_owner": "core", "timeout_ms": 60000, "error_codes": ["raw_record_not_safe", "input_too_large", "guardrail_rejected", "invalid_extraction_schema", "extractor_rate_limited", "extractor_timeout", "extractor_unavailable", "deadline_exceeded", "unexpected_provider_error"]},
            "CT-EXTRACT-REPAIR-01": {"retry_owner": "core", "timeout_ms": 20000, "error_codes": ["guardrail_rejected", "invalid_extraction_schema", "extractor_rate_limited", "extractor_timeout", "extractor_unavailable", "deadline_exceeded", "unexpected_provider_error"]},
            "CT-EXTRACT-HEALTH-01": {"retry_owner": "core", "timeout_ms": 3000, "error_codes": ["extractor_timeout", "extractor_unavailable", "deadline_exceeded", "unexpected_provider_error"]},
        })

    def test_extractor_runtime_protocol_and_non_production_marker(self) -> None:
        adapter = self.make_extractor()
        self.assertIsInstance(adapter, EvidenceExtractor)
        self.assertTrue(adapter.non_production)
        request = extract_request("OP-EXT-DETERMINISTIC")
        self.assertEqual(adapter.extract(request), self.make_extractor().extract(request))

    def test_extractor_dtos_frozen_exact_and_inline_locator_bounds(self) -> None:
        request = extract_request()
        with self.assertRaises(FrozenInstanceError):
            request.raw_record_id = "RAW-OTHER"
        with self.assertRaises(AttributeError):
            request.assets.append("ETH")
        self.assertEqual({"schema_version", "operation_id", "task_id", "execution_id", "raw_record_id", "raw_content_hash", "content", "assets", "allowed_event_taxonomy", "output_schema_version", "guardrail_policy_version", "deadline"}, set(request.to_wire()))
        self.assertEqual({"kind": "inline", "clean_content": "bounded source text"}, request.content.to_wire())
        locator = LocatorContentInputDTO("urn:cryptotrust:raw:RAW-001")
        self.assertEqual({"kind": "locator", "locator": "urn:cryptotrust:raw:RAW-001"}, locator.to_wire())
        with self.assertRaises(ContractValidationError):
            InlineContentInputDTO("")
        with self.assertRaises(ContractValidationError):
            InlineContentInputDTO("é" * 600_000)
        with self.assertRaises(ContractValidationError):
            LocatorContentInputDTO("x" * 4097)

    def test_extractor_valid_invalid_quarantined_and_replay(self) -> None:
        adapter = self.make_extractor()
        for index, outcome in enumerate(("valid", "invalid", "quarantined")):
            operation = f"OP-EXT-OUT-{index}"
            expected = extraction_result(outcome=outcome, invocation=f"INV-{index}")
            self.configure_extract(adapter, operation, expected)
            request = extract_request(operation)
            first = adapter.extract(request)
            replay = adapter.extract(request)
            self.assertEqual(expected, first)
            self.assertIs(first, replay)
            assert_operation_wire_valid(self, "evidence_extractor", "extract", request, first)
        self.assertEqual(3, adapter.invocation_count)

    def test_extractor_asset_taxonomy_semantics_and_identity(self) -> None:
        for result in (extraction_result(assets=("ETH",)), extraction_result(event_type="unapproved_event")):
            adapter = self.make_extractor()
            operation = "OP-EXT-SEMANTIC"
            self.configure_extract(adapter, operation, result)
            self.assertEqual("invalid_extraction_schema", adapter.extract(extract_request(operation)).error.code)
        adapter = self.make_extractor()
        request = extract_request("OP-EXT-IDENTITY")
        adapter.extract(request)
        changed = replace(request, raw_content_hash=HASH_B)
        self.assertEqual("invalid_extraction_schema", adapter.extract(changed).error.code)

    def test_extractor_timeout_typed_errors_unknown_exception_and_deadline_before_io(self) -> None:
        for operation, response, code in (
            ("OP-EXT-TIMEOUT", "extractor_timeout", "extractor_timeout"),
            ("OP-EXT-SCHEMA", "invalid_extraction_schema", "invalid_extraction_schema"),
            ("OP-EXT-EXCEPTION", RuntimeError("vendor secret token"), "unexpected_provider_error"),
        ):
            adapter = self.make_extractor()
            self.configure_extract(adapter, operation, response)
            result = adapter.extract(extract_request(operation))
            self.assertEqual(code, result.error.code)
            self.assertNotIn("secret", repr(result.to_wire()).lower())
        adapter = self.make_extractor()
        request = extract_request("OP-EXT-EXPIRED")
        request = replace(request, deadline=deadline("OP-EXT-EXPIRED", budget_ms=1_000, at=NOW))
        self.assertEqual("deadline_exceeded", adapter.extract(request).error.code)
        self.assertEqual(0, adapter.invocation_count)

    def test_extractor_repair_once_typed_failure_or_configured_quarantine(self) -> None:
        adapter = self.make_extractor()
        quarantine = extraction_result(outcome="quarantined", invocation="INV-REPAIR")
        self.configure_repair(adapter, "OP-REP-OK", quarantine)
        first_request = repair_request("OP-REP-OK")
        first = adapter.repair(first_request)
        self.assertEqual("quarantined", first.outcome)
        self.assertIs(first, adapter.repair(first_request))
        assert_operation_wire_valid(self, "evidence_extractor", "repair", first_request, first)
        second_request = repair_request("OP-REP-SECOND")
        self.assertEqual("invalid_extraction_schema", adapter.repair(second_request).error.code)

        for operation, configured, code in (
            ("OP-REP-TIMEOUT", "extractor_timeout", "extractor_timeout"),
            ("OP-REP-FAIL", "invalid_extraction_schema", "invalid_extraction_schema"),
        ):
            other = self.make_extractor()
            self.configure_repair(other, operation, configured)
            result = other.repair(repair_request(operation))
            self.assertEqual(code, result.error.code)

    def test_extractor_health_is_three_second_side_effect_free(self) -> None:
        adapter = self.make_extractor()
        before = adapter.invocation_count
        request = health_request()
        result = adapter.health_check(request)
        self.assertIsInstance(result, ProviderHealthDTO)
        assert_operation_wire_valid(self, "evidence_extractor", "health_check", request, result)
        self.assertEqual(before, adapter.invocation_count)
