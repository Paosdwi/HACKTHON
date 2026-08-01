from __future__ import annotations

import inspect
import json
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.evidence_extractor_v2 import (
    REPAIR_AUTHORIZATION_RULESET_VERSION,
    RepairAuthorizationInputDTO,
    RepairRequestV2DTO,
    build_repair_authorization_hash,
    repair_authorization_payload,
)
from crypto_trust_agent.domain.primitives import ContractValidationError

FIXTURE_PATH = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "evidence_extractor_v2"
    / "repair_authorization_hash_golden.json"
)


class RepairAuthorizationHashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_golden_vectors(self) -> None:
        self.assertEqual(
            REPAIR_AUTHORIZATION_RULESET_VERSION,
            self.fixture["authorization_ruleset_version"],
        )
        observed: dict[str, str] = {}
        for vector in self.fixture["vectors"]:
            value = RepairAuthorizationInputDTO(**vector["input"])
            result = build_repair_authorization_hash(value)
            self.assertEqual(vector["expected_hash"], result, vector["name"])
            self.assertRegex(result, r"^sha256:[0-9a-f]{64}$")
            observed[vector["name"]] = result

        self.assertEqual(observed["baseline"], observed["reordered_duplicates"])
        for changed in (
            "different_context_hash",
            "different_clean_content_hash",
            "different_assets",
            "different_taxonomy",
            "different_ruleset_version",
        ):
            self.assertNotEqual(observed["baseline"], observed[changed])

    def test_canonical_payload_has_only_approved_authority_fields(self) -> None:
        vector = self.fixture["vectors"][0]
        value = RepairAuthorizationInputDTO(**vector["input"])
        payload = repair_authorization_payload(value)
        self.assertEqual(
            {
                "authorization_ruleset_version",
                "raw_record_id",
                "raw_content_hash",
                "context_hash",
                "clean_content_hash",
                "assets",
                "allowed_event_taxonomy",
                "output_schema_version",
                "guardrail_policy_version",
            },
            set(payload),
        )
        self.assertEqual(sorted(set(value.assets)), payload["assets"])
        self.assertEqual(
            sorted(set(value.allowed_event_taxonomy)),
            payload["allowed_event_taxonomy"],
        )
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for forbidden in (
            "locator",
            "credential",
            "provider_payload",
            "diagnostic",
            "token",
            "jwt",
            "prompt",
            "pii",
            "full sensitive cleaned content",
        ):
            self.assertNotIn(forbidden, encoded.lower())

    def test_builder_has_no_sensitive_or_nondeterministic_inputs(self) -> None:
        parameters = set(inspect.signature(build_repair_authorization_hash).parameters)
        self.assertEqual({"value"}, parameters)
        request_hash_parameters = set(
            inspect.signature(RepairRequestV2DTO.expected_authorization_hash).parameters
        )
        self.assertEqual({"self"}, request_hash_parameters)
        dto_parameters = set(inspect.signature(RepairAuthorizationInputDTO).parameters)
        payload = repair_authorization_payload(
            RepairAuthorizationInputDTO(**self.fixture["vectors"][0]["input"])
        )
        for forbidden in (
            "content",
            "locator",
            "credential",
            "provider_payload",
            "provider_diagnostics",
            "original_result",
            "validator_errors",
            "deadline",
            "clock",
            "environment",
            "random",
        ):
            self.assertNotIn(forbidden, dto_parameters)
            self.assertNotIn(forbidden, payload)

    def test_authorization_collections_accept_only_list_or_tuple(self) -> None:
        baseline = dict(self.fixture["vectors"][0]["input"])
        invalid_containers = (
            "BTC",
            b"BTC",
            {"BTC": True},
            {"BTC"},
            (item for item in ("BTC",)),
            iter(("BTC",)),
        )
        for field in ("assets", "allowed_event_taxonomy"):
            for container in invalid_containers:
                with self.subTest(field=field, container=type(container).__name__):
                    invalid = baseline | {field: container}
                    with self.assertRaisesRegex(
                        ContractValidationError,
                        f"invalid {field}",
                    ):
                        RepairAuthorizationInputDTO(**invalid)

    def test_authorization_input_is_frozen(self) -> None:
        value = RepairAuthorizationInputDTO(**self.fixture["vectors"][0]["input"])
        with self.assertRaises(FrozenInstanceError):
            value.raw_record_id = "RAW-OTHER"
        with self.assertRaises(AttributeError):
            value.assets.append("ETH")


if __name__ == "__main__":
    unittest.main()
