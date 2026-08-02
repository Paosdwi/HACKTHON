from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "src" / "crypto_trust_agent" / "infrastructure"
import types
provider_package = types.ModuleType("pa70_infrastructure")
provider_package.__path__ = [str(INFRA)]
sys.modules.setdefault("pa70_infrastructure", provider_package)

from pa70_infrastructure.collectors.adapter import SecureSourceCollector
from test_pa70_collector import FakeFetcher, ImmediateLimiter, Robots, request, response, PUBLIC_IP

SCHEMAS = ROOT / "docs" / "architecture" / "schemas"
COMMON_PATH = SCHEMAS / "common" / "common.schema.json"
CONTRACT_PATH = SCHEMAS / "source_collector" / "contract.schema.json"
VALID_PATH = SCHEMAS / "source_collector" / "valid-examples.json"
INVALID_PATH = SCHEMAS / "source_collector" / "invalid-examples.json"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


class SourceCollectorContractHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.common = load(COMMON_PATH)
        cls.contract = load(CONTRACT_PATH)
        registry = Registry().with_resources([
            (cls.common["$id"], Resource.from_contents(cls.common)),
            (cls.contract["$id"], Resource.from_contents(cls.contract)),
        ])
        cls.validator = Draft202012Validator(cls.contract, registry=registry)

    def test_frozen_contract_identity_and_method_policies(self):
        self.assertEqual("1.0.0", self.contract["x-contract-version"])
        expected = {
            "CollectOperation": ("CT-COLLECT-COLLECT-01", 1, False),
            "HealthOperation": ("CT-COLLECT-HEALTH-01", 1, False),
            "CapabilitiesOperation": ("CT-COLLECT-CAPABILITIES-01", 1, False),
        }
        for name, (contract_id, attempts, hidden) in expected.items():
            definition = self.contract["$defs"][name]
            policy = definition["x-method-policy"]
            self.assertEqual(contract_id, definition["x-contract-test-id"])
            self.assertEqual(attempts, policy["max_attempts"])
            self.assertEqual(hidden, policy["hidden_adapter_retries"])
            for field in ("retry_owner", "idempotency", "concurrency", "error_codes"):
                self.assertIn(field, policy)
        collect = self.contract["$defs"]["CollectOperation"]["x-method-policy"]
        self.assertEqual({"static": 15000, "playwright": 30000}, collect["timeout_by_mode_ms"])
        self.assertEqual({"connect": 3000, "read": 10000}, collect["transport_guard_timeout_ms"])

    def test_core_owned_valid_examples_are_accepted(self):
        examples = load(VALID_PATH)["examples"]
        self.assertEqual(3, len(examples))
        for example in examples:
            with self.subTest(test_id=example["test_id"]):
                self.validator.validate(example["value"])

    def test_core_owned_invalid_examples_are_rejected(self):
        examples = load(INVALID_PATH)["examples"]
        self.assertEqual(3, len(examples))
        for example in examples:
            with self.subTest(test_id=example["test_id"]):
                self.assertTrue(list(self.validator.iter_errors(example["value"])))

    def test_adapter_success_validates_against_shared_collect_contract(self):
        collector = SecureSourceCollector(
            {"example.com"}, "1.0.0", static_fetcher=FakeFetcher([response()]),
            resolver=lambda host, port: (PUBLIC_IP,), robots_policy=Robots(),
            limiter=ImmediateLimiter(), now_utc=lambda: datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc),
        )
        value = request()
        operation = {"method": "collect", "request": value, "response": collector.collect(value)}
        errors = list(self.validator.iter_errors(operation))
        self.assertEqual([], errors, [error.message for error in errors])

    def test_adapter_error_validates_and_unknown_is_method_specific(self):
        collector = SecureSourceCollector(
            {"example.com"}, "1.0.0", static_fetcher=FakeFetcher(failure=RuntimeError("vendor secret")),
            resolver=lambda host, port: (PUBLIC_IP,), robots_policy=Robots(),
            limiter=ImmediateLimiter(), now_utc=lambda: datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc),
        )
        value = request()
        operation = {"method": "collect", "request": value, "response": collector.collect(value)}
        self.validator.validate(operation)
        self.assertEqual("unexpected_provider_error", operation["response"]["issues"][0]["code"])

    def test_capabilities_output_validates_against_shared_contract(self):
        collector = SecureSourceCollector(
            {"example.com"}, "1.0.0", static_fetcher=FakeFetcher([]),
            resolver=lambda host, port: (PUBLIC_IP,), robots_policy=Robots(),
            limiter=ImmediateLimiter(), now_utc=lambda: datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc),
        )
        value = {
            "schema_version": "1.0.0", "operation_id": "OP-CAP-01", "provider": "source_collector",
            "deadline": request("OP-CAP-01")["deadline"],
        }
        operation = {"method": "capabilities", "request": value, "response": collector.capabilities(value)}
        self.validator.validate(operation)

    def test_schema_rejects_invalid_security_and_lineage_shapes(self):
        collector = SecureSourceCollector(
            {"example.com"}, "1.0.0", static_fetcher=FakeFetcher([response()]),
            resolver=lambda host, port: (PUBLIC_IP,), robots_policy=Robots(),
            limiter=ImmediateLimiter(), now_utc=lambda: datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc),
        )
        value = request()
        result = collector.collect(value)
        invalid = {"method": "collect", "request": value, "response": result}
        del invalid["response"]["records"][0]["raw_locator"]
        invalid["response"]["records"][0]["security"]["dns_ip_validated"] = False
        self.assertTrue(list(self.validator.iter_errors(invalid)))


if __name__ == "__main__":
    unittest.main()
