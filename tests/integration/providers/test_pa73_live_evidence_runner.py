"""Offline-only tests for the explicit PA73 live-evidence runner."""
from __future__ import annotations

import builtins
import hashlib
import importlib.util
import json
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
while str(SRC) in sys.path:
    sys.path.remove(str(SRC))
sys.path.insert(0, str(SRC))
core_package = sys.modules.get("crypto_trust_agent")
if core_package is None:
    core_package = types.ModuleType("crypto_trust_agent")
    core_package.__path__ = []
    sys.modules["crypto_trust_agent"] = core_package
checked_out_package = str(SRC / "crypto_trust_agent")
if checked_out_package not in core_package.__path__:
    core_package.__path__.insert(0, checked_out_package)

from crypto_trust_agent.infrastructure.aws.bedrock_reasoning import (
    FALLBACK_BASE_MODEL,
    FALLBACK_PROFILE_MODEL,
    PRIMARY_BASE_MODEL,
    PRIMARY_PROFILE_MODEL,
    ExplicitLiveBedrockClient,
)
from crypto_trust_agent.infrastructure.reasoning.adapter import ProviderFailure
from tests.integration.providers import pa73_live_evidence_runner as runner

FIXTURE_RAW = runner.FIXTURE_PATH.read_bytes()
FIXTURE_HASH = "sha256:" + hashlib.sha256(FIXTURE_RAW).hexdigest()
POLICY_FIXTURE_RAW = runner.POLICY_FIXTURE_PATH.read_bytes()
POLICY_FIXTURE_HASH = "sha256:" + hashlib.sha256(POLICY_FIXTURE_RAW).hexdigest()
NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)


def valid_environment(fixture_hash: str = FIXTURE_HASH) -> dict[str, str]:
    return {
        "PA73_BEDROCK_LIVE_INTEGRATION": "1",
        "PA73_AWS_REGION": "us-west-2",
        "PA73_AWS_PROFILE": "default",
        "PA73_AWS_CREDENTIAL_MODE": "profile",
        "PA73_PRIMARY_BASE_MODEL_ID": PRIMARY_BASE_MODEL,
        "PA73_PRIMARY_PROFILE_MODEL_ID": PRIMARY_PROFILE_MODEL,
        "PA73_FALLBACK_BASE_MODEL_ID": FALLBACK_BASE_MODEL,
        "PA73_FALLBACK_PROFILE_MODEL_ID": FALLBACK_PROFILE_MODEL,
        "PA73_GUARDRAIL_ID": "synthetic-guardrail-id",
        "PA73_GUARDRAIL_VERSION": "1",
        "PA73_GUARDRAIL_POLICY_VERSION": "reasoning-guardrail-1.0.0",
        "PA73_APPROVED_FIXTURE_SHA256": fixture_hash,
        "PA73_MAX_TOKENS": "1024",
        "PA73_TEMPERATURE": "0.0",
        "PA73_ALLOWED_REGIONS": "us-west-2",
        "PA73_GUARDRAIL_APPROVED": "1",
    }


def valid_policy_environment() -> dict[str, str]:
    return {
        "PA73_GUARDRAIL_POLICY_TEST": "1",
        "PA73_GUARDRAIL_POLICY_AWS_REGION": "us-west-2",
        "PA73_GUARDRAIL_POLICY_AWS_PROFILE": "default",
        "PA73_GUARDRAIL_POLICY_AWS_CREDENTIAL_MODE": "profile",
        "PA73_GUARDRAIL_ID": "synthetic-guardrail-id",
        "PA73_GUARDRAIL_VERSION": "DRAFT",
        "PA73_GUARDRAIL_POLICY_VERSION": "reasoning-guardrail-1.0.0",
        "PA73_GUARDRAIL_POLICY_APPROVED": "1",
    }


def valid_response() -> bytes:
    return json.dumps(
        {
            "facts": [
                {
                    "fact_id": "FACT-PA73-LIVE-001",
                    "statement": "The synthetic context is bounded.",
                    "evidence_refs": ["EVID-PA73-LIVE-001"],
                    "analysis_refs": ["ANALYSIS-PA73-LIVE-001"],
                }
            ],
            "inferences": [
                {
                    "inference_id": "INFER-PA73-LIVE-001",
                    "statement": "The fixture can validate the local boundary.",
                    "fact_refs": ["FACT-PA73-LIVE-001"],
                    "confidence": "0.8",
                }
            ],
            "conclusions": [
                {
                    "conclusion_id": "CONCL-PA73-LIVE-001",
                    "statement": "Only bounded validation is supported.",
                    "fact_refs": ["FACT-PA73-LIVE-001"],
                    "inference_refs": ["INFER-PA73-LIVE-001"],
                    "confidence": "0.8",
                }
            ],
            "limitations": ["Synthetic non-production context only."],
            "watchpoints": [],
            "confidence_components": {
                "evidence_quality": "0.8",
                "consistency": "0.8",
                "coverage": "0.8",
                "overall": "0.8",
            },
        },
        separators=(",", ":"),
    ).encode()


def valid_policy_response(case: runner.GuardrailPolicyCase) -> dict[str, object]:
    if case.expected_action == "NONE":
        return {"action": "NONE"}
    if case.expected_detector_kind == "content_filter":
        assessment: dict[str, object] = {
            "contentPolicy": {
                "filters": [
                    {
                        "type": case.expected_detector,
                        "confidence": "HIGH",
                        "filterStrength": "HIGH",
                        "action": "BLOCKED",
                        "detected": True,
                    }
                ]
            }
        }
    elif case.expected_detector_kind == "pii_entity":
        assessment = {
            "sensitiveInformationPolicy": {
                "piiEntities": [
                    {
                        "type": case.expected_detector,
                        "match": "synthetic-match",
                        "action": (
                            "ANONYMIZED"
                            if case.expected_action == "ANONYMIZED"
                            else "BLOCKED"
                        ),
                    }
                ]
            }
        }
    elif case.expected_detector_kind == "regex":
        assessment = {
            "sensitiveInformationPolicy": {
                "regexes": [
                    {
                        "name": case.expected_detector,
                        "regex": "synthetic-fixture-regex",
                        "match": "synthetic-match",
                        "action": "BLOCKED",
                    }
                ]
            }
        }
    else:
        raise AssertionError("fixture detector kind")
    response: dict[str, object] = {
        "action": "GUARDRAIL_INTERVENED",
        "assessments": [assessment],
    }
    if case.expected_action == "ANONYMIZED":
        response["outputs"] = [{"text": "[ANONYMIZED]"}]
    return response


class FakeGuardrailClient:
    def __init__(self, outcomes: list[object] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        if outcomes is None:
            cases = runner._policy_cases_from_fixture(POLICY_FIXTURE_RAW)
            self.outcomes = [valid_policy_response(case) for case in cases]
        else:
            self.outcomes = outcomes

    def apply_guardrail(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def run_policy(client: FakeGuardrailClient) -> dict[str, object]:
    return runner.run_guardrail_policy_tests(
        getenv=valid_policy_environment().get,
        fixture_reader=lambda path: POLICY_FIXTURE_RAW,
        client_factory=lambda configuration, counter: client,
    )


class FakeInvoker:
    non_production = False
    max_attempts = 1
    hidden_retries = 0

    def __init__(self, counter: runner.OperationCounter, order: list[str]) -> None:
        self.counter = counter
        self.order = order
        self.lifecycle = {"primary": True, "fallback": True}
        self.health: dict[str, object] = {"primary": True, "fallback": True}
        self.responses: dict[str, object] = {
            "primary": valid_response(),
            "fallback": valid_response(),
        }

    def lifecycle_preflight(
        self, *, model_role: str, timeout_ms: int, cancelled
    ) -> bool:
        del timeout_ms, cancelled
        self.counter.record(model_role, "get_foundation_model")
        self.order.append(f"{model_role}:lifecycle")
        result = self.lifecycle[model_role]
        if isinstance(result, BaseException):
            raise result
        return result

    def probe(self, *, model_role: str, timeout_ms: int, cancelled) -> bool:
        del timeout_ms, cancelled
        self.counter.record(model_role, "get_foundation_model_availability")
        self.order.append(f"{model_role}:availability")
        result = self.health[model_role]
        if result == "availability_failed":
            return False
        self.counter.record(model_role, "get_inference_profile")
        self.order.append(f"{model_role}:profile")
        if isinstance(result, BaseException):
            raise result
        return result is True

    def invoke(
        self,
        *,
        operation_id: str,
        model_role: str,
        body: bytes,
        timeout_ms: int,
        cancelled,
    ) -> bytes | str:
        del operation_id, body, timeout_ms, cancelled
        self.counter.record(model_role, "converse")
        self.order.append(f"{model_role}:converse")
        result = self.responses[model_role]
        if isinstance(result, BaseException):
            raise result
        if not isinstance(result, (bytes, str)):
            raise TypeError
        return result


class Harness:
    def __init__(self, logging_response: object | None = None) -> None:
        self.order: list[str] = []
        self.logging_response = (
            {"loggingConfig": {"textDataDeliveryEnabled": False}}
            if logging_response is None
            else logging_response
        )
        self.invoker: FakeInvoker | None = None
        self.sdk_factory_calls = 0

    def logging_factory(self, config, counter):
        del config
        self.sdk_factory_calls += 1

        def probe() -> object:
            counter.record(None, "get_model_invocation_logging_configuration")
            self.order.append("logging")
            return self.logging_response

        return probe

    def client_factory(self, config, counter) -> ExplicitLiveBedrockClient:
        del config
        self.sdk_factory_calls += 1
        self.invoker = FakeInvoker(counter, self.order)
        return ExplicitLiveBedrockClient(self.invoker, enabled=True)

    def run(
        self,
        *,
        environment: dict[str, str] | None = None,
        fixture_raw: bytes = FIXTURE_RAW,
    ) -> dict[str, object]:
        values = environment or valid_environment()
        return runner.run_live_evidence(
            getenv=lambda name, default=None: values.get(name, default),
            fixture_reader=lambda path: fixture_raw,
            logging_probe_factory=self.logging_factory,
            client_factory=self.client_factory,
            now_utc=lambda: NOW,
        )


def test_import_is_side_effect_free() -> None:
    module_path = Path(runner.__file__)
    attempted_sdk: list[str] = []
    opened: list[object] = []
    original_import = builtins.__import__
    original_open = builtins.open

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "boto3" or name.startswith("botocore"):
            attempted_sdk.append(name)
            raise AssertionError
        return original_import(name, *args, **kwargs)

    def guarded_open(*args: object, **kwargs: object) -> object:
        opened.append(args[0] if args else None)
        return original_open(*args, **kwargs)

    spec = importlib.util.spec_from_file_location("pa73_live_runner_isolated", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        with (
            patch("builtins.__import__", side_effect=guarded_import),
            patch("builtins.open", side_effect=guarded_open),
            patch("os.getenv", side_effect=AssertionError("environment read")),
        ):
            spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    assert attempted_sdk == []
    assert opened == []


def test_gate_default_off_and_disabled_reads_only_gate() -> None:
    reads: list[str] = []

    def getenv(name: str, default: str | None = None) -> str | None:
        reads.append(name)
        return default

    with pytest.raises(runner.LiveEvidenceError, match="^live_evidence_not_enabled$"):
        runner.run_live_evidence(
            getenv=getenv,
            fixture_reader=lambda path: (_ for _ in ()).throw(AssertionError(path)),
            logging_probe_factory=lambda config, counter: (_ for _ in ()).throw(
                AssertionError((config, counter))
            ),
            client_factory=lambda config, counter: (_ for _ in ()).throw(
                AssertionError((config, counter))
            ),
        )
    assert reads == ["PA73_BEDROCK_LIVE_INTEGRATION"]


def test_each_missing_or_invalid_environment_value_is_zero_sdk() -> None:
    complete = valid_environment()
    required = tuple(name for name in complete if name != "PA73_BEDROCK_LIVE_INTEGRATION")
    cases: list[dict[str, str]] = []
    for name in required:
        value = dict(complete)
        del value[name]
        cases.append(value)
    invalid = dict(complete)
    invalid["PA73_AWS_REGION"] = "us-east-1"
    cases.append(invalid)
    for environment in cases:
        logging_factory = Mock()
        client_factory = Mock()
        with pytest.raises(
            runner.LiveEvidenceError, match="^live_evidence_configuration_invalid$"
        ):
            runner.run_live_evidence(
                getenv=environment.get,
                fixture_reader=lambda path: FIXTURE_RAW,
                logging_probe_factory=logging_factory,
                client_factory=client_factory,
            )
        logging_factory.assert_not_called()
        client_factory.assert_not_called()


def test_credential_environment_is_never_read() -> None:
    values = valid_environment()
    forbidden = {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "PA73_AWS_ACCESS_KEY_ID",
        "PA73_AWS_SECRET_ACCESS_KEY",
        "PA73_AWS_SESSION_TOKEN",
    }
    reads: list[str] = []

    def getenv(name: str, default: str | None = None) -> str | None:
        if name in forbidden:
            raise AssertionError(name)
        reads.append(name)
        return values.get(name, default)

    Harness().run(environment=values)
    harness = Harness()
    runner.run_live_evidence(
        getenv=getenv,
        fixture_reader=lambda path: FIXTURE_RAW,
        logging_probe_factory=harness.logging_factory,
        client_factory=harness.client_factory,
        now_utc=lambda: NOW,
    )
    assert forbidden.isdisjoint(reads)


def test_fixture_hash_mismatch_and_invalid_shape_are_zero_sdk() -> None:
    malformed = b'{"synthetic_non_production_fixture":true,"reasoning_context":{}}\n'
    cases = (
        (valid_environment("sha256:" + "0" * 64), FIXTURE_RAW),
        (
            valid_environment("sha256:" + hashlib.sha256(malformed).hexdigest()),
            malformed,
        ),
    )
    for environment, fixture_raw in cases:
        harness = Harness()
        with pytest.raises(
            runner.LiveEvidenceError, match="^live_evidence_fixture_invalid$"
        ):
            harness.run(environment=environment, fixture_raw=fixture_raw)
        assert harness.sdk_factory_calls == 0
        assert harness.order == []


@pytest.mark.parametrize(
    "logging_response",
    [
        {"loggingConfig": {"textDataDeliveryEnabled": True}},
        {},
        {"loggingConfig": {}},
        {"loggingConfig": {"textDataDeliveryEnabled": "false"}},
    ],
)
def test_logging_enabled_or_unknown_shape_blocks_all_model_calls(
    logging_response: object,
) -> None:
    harness = Harness(logging_response)
    summary = harness.run()
    assert harness.order == ["logging"]
    assert summary["roles"][0]["error"] in {
        "logging_not_disabled",
        "logging_preflight_failed",
    }
    assert summary["operation_counts"]["roles"] == {
        "primary": {
            "get_foundation_model": 0,
            "get_foundation_model_availability": 0,
            "get_inference_profile": 0,
            "converse": 0,
        },
        "fallback": {
            "get_foundation_model": 0,
            "get_foundation_model_availability": 0,
            "get_inference_profile": 0,
            "converse": 0,
        },
    }


def run_configured(setup) -> tuple[Harness, dict[str, object]]:
    harness = Harness()

    def client_factory(config, counter) -> ExplicitLiveBedrockClient:
        client = harness.client_factory(config, counter)
        assert harness.invoker is not None
        setup(harness.invoker)
        return client

    summary = runner.run_live_evidence(
        getenv=lambda name, default=None: valid_environment().get(name, default),
        fixture_reader=lambda path: FIXTURE_RAW,
        logging_probe_factory=harness.logging_factory,
        client_factory=client_factory,
        now_utc=lambda: NOW,
    )
    return harness, summary


def test_full_primary_then_fallback_order_limits_and_retry_zero() -> None:
    harness = Harness()
    summary = harness.run()
    assert harness.order == [
        "logging",
        "primary:lifecycle",
        "primary:availability",
        "primary:profile",
        "primary:converse",
        "fallback:lifecycle",
        "fallback:availability",
        "fallback:profile",
        "fallback:converse",
    ]
    assert summary["retry_count"] == 0
    assert all(
        status["lifecycle"] and status["health"] and status["generate"]
        and status["error"] is None
        for status in summary["roles"]
    )
    counts = summary["operation_counts"]
    assert counts["get_model_invocation_logging_configuration"] == 1
    for role_counts in counts["roles"].values():
        assert role_counts == {
            "get_foundation_model": 1,
            "get_foundation_model_availability": 1,
            "get_inference_profile": 1,
            "converse": 1,
        }


def test_primary_failure_stops_before_all_fallback_calls() -> None:
    harness, summary = run_configured(
        lambda invoker: invoker.responses.update(
            primary=ProviderFailure("guardrail_rejected")
        )
    )
    assert harness.order[-1] == "primary:converse"
    assert not any(item.startswith("fallback:") for item in harness.order)
    assert summary["roles"][0]["generate"] is False
    assert summary["roles"][0]["error"] == "generate_failed"


def test_lifecycle_failure_skips_health_and_generate() -> None:
    harness, summary = run_configured(
        lambda invoker: invoker.lifecycle.update(primary=False)
    )
    assert harness.order == ["logging", "primary:lifecycle"]
    assert summary["roles"][0] == {
        "role": "primary",
        "lifecycle": False,
        "health": False,
        "generate": False,
        "error": "lifecycle_failed",
    }


def test_health_failure_skips_generate() -> None:
    harness, summary = run_configured(
        lambda invoker: invoker.health.update(primary="availability_failed")
    )
    assert harness.order == [
        "logging",
        "primary:lifecycle",
        "primary:availability",
    ]
    assert summary["roles"][0]["lifecycle"] is True
    assert summary["roles"][0]["health"] is False
    assert summary["roles"][0]["generate"] is False
    assert summary["roles"][0]["error"] == "health_failed"


@pytest.mark.parametrize("failure_kind", ["guardrail", "schema", "invalid", "citation"])
def test_guardrail_invalid_schema_and_citation_results_are_not_success(
    failure_kind: str,
) -> None:
    def setup(invoker: FakeInvoker) -> None:
        if failure_kind == "guardrail":
            invoker.responses["primary"] = ProviderFailure("guardrail_rejected")
        elif failure_kind == "schema":
            invoker.responses["primary"] = b"not-json"
        elif failure_kind == "invalid":
            payload = json.loads(valid_response())
            payload["facts"] = []
            payload["inferences"] = []
            payload["conclusions"] = []
            invoker.responses["primary"] = json.dumps(payload).encode()
        else:
            payload = json.loads(valid_response())
            payload["facts"][0]["evidence_refs"] = ["EVID-OUTSIDE-FIXTURE"]
            invoker.responses["primary"] = json.dumps(payload).encode()

    harness, summary = run_configured(setup)
    assert harness.order.count("primary:converse") == 1
    assert summary["roles"][0]["generate"] is False
    assert summary["roles"][0]["error"] == "generate_failed"
    assert not any(item.startswith("fallback:") for item in harness.order)
    assert summary["retry_count"] == 0


def test_safe_summary_contains_no_raw_or_external_metadata() -> None:
    secret = (
        "SYNTHETIC-SECRET raw prompt raw response ar"
        + "n:aws:bedrock:us-west-2:"
        + "000000"
        + "000000:guardrail/example Account ID User"
        + "Id request-id Authorization"
    )
    harness, summary = run_configured(
        lambda invoker: invoker.responses.update(primary=RuntimeError(secret))
    )
    rendered = json.dumps(summary, sort_keys=True)
    for forbidden in (
        "synthetic-secret",
        "raw prompt",
        "raw response",
        "ar" + "n:aws",
        "000000" + "000000",
        "account id",
        "user" + "id",
        "request-id",
        "authorization",
    ):
        assert forbidden not in rendered.lower()
    assert harness.order.count("primary:converse") == 1
    assert set(summary) == {
        "schema_version",
        "fixture_sha256",
        "region",
        "guardrail_policy_version",
        "guardrail_numeric_version",
        "roles",
        "operation_counts",
        "retry_count",
        "completed_at_utc",
    }


def test_normal_pytest_import_never_calls_production_factory() -> None:
    with (
        patch.object(
            runner,
            "_production_boundaries",
            side_effect=AssertionError("live production factory invoked"),
        ) as production,
        pytest.raises(runner.LiveEvidenceError, match="^live_evidence_not_enabled$"),
    ):
        runner.run_live_evidence(getenv=lambda name, default=None: default)
    production.assert_not_called()


def test_fixture_path_and_marker_are_fixed_and_contract_valid() -> None:
    assert runner.FIXTURE_DISPLAY_PATH == (
        "tests/fixtures/pa73/live_reasoning_context_v1.json"
    )
    assert runner.FIXTURE_PATH == runner.ROOT / runner.FIXTURE_DISPLAY_PATH
    context = runner._context_from_fixture(FIXTURE_RAW, FIXTURE_HASH)
    assert {item.evidence_id for item in context.evidence_refs} == {
        "EVID-PA73-LIVE-001"
    }
    assert {item.analysis_id for item in context.analysis_refs} == {
        "ANALYSIS-PA73-LIVE-001"
    }


def test_main_live_guardrail_id_and_fixed_values_fail_before_sdk() -> None:
    invalid_values = (
        (
            "PA73_GUARDRAIL_ID",
            "ar" + "n:aws:bedrock:region:" + "000000" + "000000:guardrail/example",
        ),
        ("PA73_GUARDRAIL_ID", "illegal/id"),
        ("PA73_GUARDRAIL_ID", "has whitespace"),
        ("PA73_GUARDRAIL_VERSION", "DRAFT"),
        ("PA73_GUARDRAIL_VERSION", "0"),
        ("PA73_MAX_TOKENS", "1023"),
        ("PA73_MAX_TOKENS", "01024"),
        ("PA73_TEMPERATURE", "0"),
        ("PA73_TEMPERATURE", "0.1"),
        ("PA73_GUARDRAIL_POLICY_VERSION", "reasoning-guardrail-1.0.1"),
    )
    for name, value in invalid_values:
        environment = valid_environment()
        environment[name] = value
        harness = Harness()
        with pytest.raises(
            runner.LiveEvidenceError, match="^live_evidence_configuration_invalid$"
        ):
            harness.run(environment=environment)
        assert harness.sdk_factory_calls == 0
        assert harness.order == []


def test_main_live_accepts_conservative_non_arn_guardrail_id() -> None:
    environment = valid_environment()
    environment["PA73_GUARDRAIL_ID"] = "A1_guardrail-2"
    configuration = runner._configuration(environment.get)
    binding = next(iter(configuration.bedrock.guardrails.values()))
    assert binding.identifier == "A1_guardrail-2"
    assert binding.version == "1"
    assert configuration.bedrock.max_tokens == 1024
    assert configuration.bedrock.temperature == 0.0
    assert configuration.guardrail_policy_version == "reasoning-guardrail-1.0.0"


def test_policy_gate_default_off_reads_only_policy_gate() -> None:
    reads: list[str] = []

    def getenv(name: str, default: str | None = None) -> str | None:
        reads.append(name)
        return default

    with pytest.raises(runner.LiveEvidenceError, match="^guardrail_policy_not_enabled$"):
        runner.run_guardrail_policy_tests(
            getenv=getenv,
            fixture_reader=lambda path: (_ for _ in ()).throw(AssertionError(path)),
            client_factory=lambda configuration, counter: (_ for _ in ()).throw(
                AssertionError((configuration, counter))
            ),
        )
    assert reads == ["PA73_GUARDRAIL_POLICY_TEST"]


def test_policy_configuration_rejects_arn_before_fixture_or_sdk() -> None:
    environment = valid_policy_environment()
    environment["PA73_GUARDRAIL_ID"] = (
        "ar" + "n:aws:bedrock:region:" + "000000" + "000000:guardrail/example"
    )
    fixture_reader = Mock()
    client_factory = Mock()
    with pytest.raises(
        runner.LiveEvidenceError, match="^guardrail_policy_configuration_invalid$"
    ):
        runner.run_guardrail_policy_tests(
            getenv=environment.get,
            fixture_reader=fixture_reader,
            client_factory=client_factory,
        )
    fixture_reader.assert_not_called()
    client_factory.assert_not_called()


@pytest.mark.parametrize("version", ["1", "0", "draft", "LATEST", " DRAFT"])
def test_policy_configuration_requires_exact_draft_before_fixture_or_sdk(
    version: str,
) -> None:
    environment = valid_policy_environment()
    environment["PA73_GUARDRAIL_VERSION"] = version
    fixture_reader = Mock()
    client_factory = Mock()
    with pytest.raises(
        runner.LiveEvidenceError, match="^guardrail_policy_configuration_invalid$"
    ):
        runner.run_guardrail_policy_tests(
            getenv=environment.get,
            fixture_reader=fixture_reader,
            client_factory=client_factory,
        )
    fixture_reader.assert_not_called()
    client_factory.assert_not_called()


def test_policy_configuration_accepts_exact_draft() -> None:
    configuration = runner._policy_configuration(valid_policy_environment().get)
    assert configuration.guardrail_version == "DRAFT"


def test_policy_fixture_fixed_hash_shape_enums_count_and_coverage() -> None:
    cases = runner._policy_cases_from_fixture(POLICY_FIXTURE_RAW)
    assert POLICY_FIXTURE_HASH == runner.POLICY_FIXTURE_SHA256
    assert runner.POLICY_FIXTURE_DISPLAY_PATH == (
        "tests/fixtures/pa73/guardrail_policy_cases_v1.json"
    )
    assert runner.POLICY_FIXTURE_PATH == runner.ROOT / runner.POLICY_FIXTURE_DISPLAY_PATH
    assert len(cases) == 12
    assert {case.source for case in cases} == {"INPUT", "OUTPUT"}
    assert {case.expected_action for case in cases} == {
        "NONE",
        "GUARDRAIL_INTERVENED",
        "ANONYMIZED",
    }
    assert {case.policy_category for case in cases} == runner._POLICY_CATEGORIES
    assert {
        case.policy_category: (case.expected_detector_kind, case.expected_detector)
        for case in cases
        if case.expected_action != "NONE"
    } == dict(runner._POLICY_EXPECTED_DETECTORS)
    assert all(
        case.expected_detector_kind is None and case.expected_detector is None
        for case in cases
        if case.expected_action == "NONE"
    )
    assert all(
        len(case.content_parts) >= 2
        for case in cases
        if case.policy_category in runner._SPLIT_SECRET_CATEGORIES
    )
    raw_wrapper = json.loads(POLICY_FIXTURE_RAW)
    assert all("content" not in raw_case for raw_case in raw_wrapper["cases"])


def test_policy_fixture_duplicate_shape_source_action_and_count_are_rejected() -> None:
    mutations: list[bytes] = []
    duplicate = POLICY_FIXTURE_RAW.decode().replace(
        '"case_id":"direct-prompt-attack"',
        '"case_id":"duplicate","case_id":"direct-prompt-attack"',
        1,
    )
    mutations.append(duplicate.encode())
    for field, value in (("source", "SIDEWAYS"), ("expected_action", "ALLOW")):
        changed = json.loads(POLICY_FIXTURE_RAW)
        changed["cases"][0][field] = value
        mutations.append(json.dumps(changed, separators=(",", ":")).encode())
    extra_shape = json.loads(POLICY_FIXTURE_RAW)
    extra_shape["cases"][0]["extra"] = True
    mutations.append(json.dumps(extra_shape, separators=(",", ":")).encode())
    too_many = json.loads(POLICY_FIXTURE_RAW)
    thirteenth = dict(too_many["cases"][0])
    thirteenth["case_id"] = "thirteenth"
    too_many["cases"].append(thirteenth)
    mutations.append(json.dumps(too_many, separators=(",", ":")).encode())
    for raw in mutations:
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        with pytest.raises(
            runner.LiveEvidenceError, match="^guardrail_policy_fixture_invalid$"
        ):
            runner._policy_cases_from_fixture(raw, digest)


def test_policy_fixture_hash_and_validation_happen_before_sdk() -> None:
    client_factory = Mock()
    corrupted = POLICY_FIXTURE_RAW.replace(b"direct-prompt-attack", b"changed-case-name")
    with pytest.raises(
        runner.LiveEvidenceError, match="^guardrail_policy_fixture_invalid$"
    ):
        runner.run_guardrail_policy_tests(
            getenv=valid_policy_environment().get,
            fixture_reader=lambda path: corrupted,
            client_factory=client_factory,
        )
    client_factory.assert_not_called()


def test_policy_success_is_fixture_order_once_per_case_and_safely_aggregated() -> None:
    client = FakeGuardrailClient()
    summary = run_policy(client)
    cases = runner._policy_cases_from_fixture(POLICY_FIXTURE_RAW)
    assert [call["source"] for call in client.calls] == [case.source for case in cases]
    assert [call["content"][0]["text"]["text"] for call in client.calls] == [
        case.content for case in cases
    ]
    assert all(
        call["guardrailIdentifier"] == "synthetic-guardrail-id"
        for call in client.calls
    )
    assert all(call["guardrailVersion"] == "DRAFT" for call in client.calls)
    assert len(client.calls) == 12
    assert summary == {
        "schema_version": "pa73-guardrail-policy-cases-1.0.0",
        "fixture_sha256": POLICY_FIXTURE_HASH,
        "passed": 12,
        "failed": 0,
        "executed": 12,
        "expected": 12,
        "policy_version": "reasoning-guardrail-1.0.0",
    }


def test_policy_official_optional_assessment_fields_are_ignored_and_not_summarized() -> None:
    cases = runner._policy_cases_from_fixture(POLICY_FIXTURE_RAW)
    outcomes = [valid_policy_response(case) for case in cases]
    for response in outcomes:
        assessments = response.get("assessments")
        if assessments is None:
            continue
        assert isinstance(assessments, list)
        assessment = assessments[0]
        assert isinstance(assessment, dict)
        assessment["appliedGuardrailDetails"] = {
            "guardrailId": "private-guardrail-id",
            "guardrailVersion": "DRAFT",
            "guardrailArn": (
                "arn:aws:bedrock:us-west-2:000000000000:guardrail/private"
            ),
        }
        assessment["invocationMetrics"] = {
            "guardrailProcessingLatency": 987654321,
            "usage": {"contentPolicyUnits": 7654321},
            "guardrailCoverage": {
                "textCharacters": {"guarded": 42, "total": 42}
            },
            "requestMetadata": {"requestId": "private-request-metadata"},
        }

    client = FakeGuardrailClient(outcomes)
    summary = run_policy(client)

    assert len(client.calls) == 12
    assert summary["passed"] == 12
    assert summary["failed"] == 0
    rendered = json.dumps(summary, sort_keys=True).lower()
    for forbidden in (
        "arn:aws:",
        "private-guardrail-id",
        "invocationmetrics",
        '"usage"',
        "7654321",
        "987654321",
        "requestmetadata",
        "private-request-metadata",
    ):
        assert forbidden not in rendered


def test_policy_unknown_assessment_field_still_fails_closed() -> None:
    cases = runner._policy_cases_from_fixture(POLICY_FIXTURE_RAW)
    outcomes = [valid_policy_response(case) for case in cases]
    assessments = outcomes[0]["assessments"]
    assert isinstance(assessments, list)
    assessment = assessments[0]
    assert isinstance(assessment, dict)
    assessment["unknownAssessmentField"] = {"requestId": "must-not-leak"}

    client = FakeGuardrailClient(outcomes)
    summary = run_policy(client)

    assert len(client.calls) == 1
    assert summary["passed"] == 0
    assert summary["failed"] == 1
    assert summary["executed"] == 1
    assert "must-not-leak" not in json.dumps(summary, sort_keys=True)


def test_policy_every_protected_case_requires_exact_detector_identity() -> None:
    cases = runner._policy_cases_from_fixture(POLICY_FIXTURE_RAW)
    for index, case in enumerate(cases):
        if case.expected_action == "NONE":
            continue
        response = valid_policy_response(case)
        assessment = response["assessments"][0]
        if case.expected_detector_kind == "content_filter":
            entry = assessment["contentPolicy"]["filters"][0]
            entry["type"] = (
                "VIOLENCE"
                if case.expected_detector != "VIOLENCE"
                else "PROMPT_ATTACK"
            )
        elif case.expected_detector_kind == "pii_entity":
            entry = assessment["sensitiveInformationPolicy"]["piiEntities"][0]
            entry["type"] = (
                "EMAIL" if case.expected_detector != "EMAIL" else "PASSWORD"
            )
        else:
            entry = assessment["sensitiveInformationPolicy"]["regexes"][0]
            entry["name"] = (
                "PA73_GENERIC_API_KEY"
                if case.expected_detector != "PA73_GENERIC_API_KEY"
                else "PA73_SESSION_TOKEN"
            )
        outcomes = [valid_policy_response(item) for item in cases]
        outcomes[index] = response
        client = FakeGuardrailClient(outcomes)
        summary = run_policy(client)
        assert len(client.calls) == index + 1
        assert summary["passed"] == index
        assert summary["failed"] == 1
        assert summary["executed"] == index + 1


def test_policy_missing_malformed_or_incorrect_assessment_stops_without_retry() -> None:
    malformed_responses: list[object] = [
        {"action": "GUARDRAIL_INTERVENED"},
        {"action": "GUARDRAIL_INTERVENED", "assessments": "invalid"},
        {
            "action": "GUARDRAIL_INTERVENED",
            "assessments": [{"contentPolicy": {"filters": [{}]}}],
        },
        {
            "action": "GUARDRAIL_INTERVENED",
            "assessments": [
                {
                    "contentPolicy": {
                        "filters": [
                            {
                                "type": "PROMPT_ATTACK",
                                "confidence": "HIGH",
                                "filterStrength": "HIGH",
                                "action": "BLOCKED",
                                "detected": False,
                            }
                        ]
                    }
                }
            ],
        },
    ]
    cases = runner._policy_cases_from_fixture(POLICY_FIXTURE_RAW)
    for response in malformed_responses:
        outcomes = [valid_policy_response(case) for case in cases]
        outcomes[0] = response
        client = FakeGuardrailClient(outcomes)
        summary = run_policy(client)
        assert len(client.calls) == 1
        assert summary["passed"] == 0
        assert summary["failed"] == 1
        assert summary["executed"] == 1


def test_policy_none_requires_top_level_none_without_assessment() -> None:
    cases = runner._policy_cases_from_fixture(POLICY_FIXTURE_RAW)
    none_index = next(
        index for index, case in enumerate(cases) if case.expected_action == "NONE"
    )
    outcomes = [valid_policy_response(case) for case in cases]
    outcomes[none_index] = {"action": "GUARDRAIL_INTERVENED", "assessments": []}
    client = FakeGuardrailClient(outcomes)
    summary = run_policy(client)
    assert len(client.calls) == none_index + 1
    assert summary["failed"] == 1
    assert summary["executed"] == none_index + 1


def test_policy_email_requires_anonymized_assessment_and_changed_output() -> None:
    cases = runner._policy_cases_from_fixture(POLICY_FIXTURE_RAW)
    pii_index = next(
        index for index, case in enumerate(cases)
        if case.expected_action == "ANONYMIZED"
    )
    response = valid_policy_response(cases[pii_index])
    response["outputs"] = [{"text": cases[pii_index].content}]
    outcomes = [valid_policy_response(case) for case in cases]
    outcomes[pii_index] = response
    client = FakeGuardrailClient(outcomes)
    summary = run_policy(client)
    assert len(client.calls) == pii_index + 1
    assert summary["failed"] == 1
    assert summary["executed"] == pii_index + 1


def test_policy_counter_rejects_repeat_and_over_budget() -> None:
    repeated = runner.GuardrailOperationCounter()
    repeated.record("case-1")
    with pytest.raises(
        runner.LiveEvidenceError, match="^guardrail_policy_budget_exceeded$"
    ):
        repeated.record("case-1")
    exceeded = runner.GuardrailOperationCounter()
    for index in range(12):
        exceeded.record(f"case-{index}")
    with pytest.raises(
        runner.LiveEvidenceError, match="^guardrail_policy_budget_exceeded$"
    ):
        exceeded.record("case-12")


@pytest.mark.parametrize(
    "failure",
    [
        {"action": "NONE"},
        RuntimeError(
            "provider request-id raw assessment User" + "Id credential Authorization"
        ),
        {"action": "ANONYMIZED", "providerMetadata": "unsafe"},
    ],
)
def test_policy_first_mismatch_exception_or_malformed_result_stops_without_retry(
    failure: object,
) -> None:
    client = FakeGuardrailClient()
    client.outcomes[2] = failure
    summary = run_policy(client)
    assert len(client.calls) == 3
    assert summary["passed"] == 2
    assert summary["failed"] == 1
    assert summary["executed"] == 3
    assert summary["expected"] == 12
    rendered = json.dumps(summary, sort_keys=True).lower()
    for forbidden in (
        "case_id",
        "content",
        "assessment",
        "providermetadata",
        "request-id",
        "user" + "id",
        "credential",
        "authorization",
        "arn:",
    ):
        assert forbidden not in rendered


def test_policy_normal_pytest_never_calls_production_factory() -> None:
    with (
        patch.object(
            runner,
            "_production_guardrail_policy_client",
            side_effect=AssertionError("live policy factory invoked"),
        ) as production,
        pytest.raises(runner.LiveEvidenceError, match="^guardrail_policy_not_enabled$"),
    ):
        runner.run_guardrail_policy_tests(getenv=lambda name, default=None: default)
    production.assert_not_called()


def test_test_module_declares_no_skip_or_xfail() -> None:
    module = sys.modules[__name__]
    test_functions = (
        value
        for name, value in vars(module).items()
        if name.startswith("test_") and callable(value)
    )
    for test_function in test_functions:
        assert not getattr(test_function, "__unittest_skip__", False)
        marker_names = {
            marker.name for marker in getattr(test_function, "pytestmark", ())
        }
        assert marker_names.isdisjoint({"skip", "skipif", "xfail"})
