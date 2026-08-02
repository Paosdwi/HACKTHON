"""PA73 offline live-boundary tests; never access AWS, network, or credentials."""
from __future__ import annotations

import builtins
import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
MODULE_PATH = SRC / "crypto_trust_agent" / "infrastructure" / "aws" / "bedrock_reasoning.py"
while str(SRC) in sys.path:
    sys.path.remove(str(SRC))
sys.path.insert(0, str(SRC))
core_package = sys.modules.get("crypto_trust_agent")
if core_package is None:
    core_package = types.ModuleType("crypto_trust_agent")
    core_package.__path__ = []
    sys.modules["crypto_trust_agent"] = core_package
package_path = core_package.__path__
checked_out_package = str(SRC / "crypto_trust_agent")
if checked_out_package not in package_path:
    package_path.insert(0, checked_out_package)

from crypto_trust_agent.infrastructure.aws.bedrock_reasoning import (
    FALLBACK_BASE_MODEL,
    FALLBACK_PROFILE_MODEL,
    PRIMARY_BASE_MODEL,
    PRIMARY_PROFILE_MODEL,
    BedrockReasoningConfig,
    Boto3BedrockReasoningInvoker,
    ExplicitLiveBedrockClient,
    GuardrailBinding,
    LiveBedrockError,
    RecordingReasoningInvoker,
)
from crypto_trust_agent.infrastructure.reasoning.adapter import (
    SYSTEM_INSTRUCTION,
    ProviderFailure,
)

POLICY = "reasoning-guardrail-1.0.0"


def explicit_config() -> BedrockReasoningConfig:
    return BedrockReasoningConfig(
        region="offline-region-1",
        profile_name="offline-profile",
        max_tokens=1024,
        temperature=0.0,
        base_model_ids={
            "primary": PRIMARY_BASE_MODEL,
            "fallback": FALLBACK_BASE_MODEL,
        },
        profile_model_ids={
            "primary": PRIMARY_PROFILE_MODEL,
            "fallback": FALLBACK_PROFILE_MODEL,
        },
        guardrails={POLICY: GuardrailBinding("offline-guardrail", "7")},
        approved_regions=frozenset({"offline-region-1"}),
        guardrail_approved=True,
    )


def request_body() -> bytes:
    return json.dumps({
        "system_instruction": SYSTEM_INSTRUCTION,
        "untrusted_input_begin": "<UNTRUSTED_REASONING_CONTEXT>",
        "request": {
            "schema_version": "1.0.0",
            "operation": "generate",
            "output_schema_version": "1.0.0",
            "guardrail_policy_version": POLICY,
            "untrusted_context_envelope": {"question": "bounded"},
        },
        "untrusted_input_end": "</UNTRUSTED_REASONING_CONTEXT>",
    }, sort_keys=True, separators=(",", ":")).encode()


class FakeConfigFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return dict(kwargs)


class FakeRuntimeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.response: object = {
            "stopReason": "end_turn",
            "output": {
                "message": {"role": "assistant", "content": [{"text": "{}"}]}
            },
        }
        self.after_call = lambda: None

    def converse(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        self.after_call()
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class FakeControlClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.availability: dict[str, object] = {}
        self.profile: dict[str, object] = {}
        self.model: dict[str, object] = {}
        self.after_availability = lambda: None
        self.after_profile = lambda: None

    def get_foundation_model_availability(self, **kwargs: object) -> object:
        self.calls.append(("availability", dict(kwargs)))
        self.after_availability()
        return self.availability

    def get_inference_profile(self, **kwargs: object) -> object:
        self.calls.append(("profile", dict(kwargs)))
        self.after_profile()
        return self.profile

    def get_foundation_model(self, **kwargs: object) -> object:
        self.calls.append(("lifecycle", dict(kwargs)))
        return self.model


class ManualClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeSession:
    def __init__(self) -> None:
        self.runtime = FakeRuntimeClient()
        self.control = FakeControlClient()
        self.calls: list[tuple[str, str, object]] = []

    def client(self, service_name: str, *, region_name: str, config: object) -> object:
        self.calls.append((service_name, region_name, config))
        return self.runtime if service_name == "bedrock-runtime" else self.control


def make_invoker(*, monotonic=None):
    session = FakeSession()
    config_factory = FakeConfigFactory()
    invoker = Boto3BedrockReasoningInvoker(
        session,
        config_factory,
        explicit_config(),
        monotonic=monotonic,
    )
    return invoker, session, config_factory


class NoLiveByDefaultTests(unittest.TestCase):
    def test_module_import_does_not_import_boto3_or_botocore(self) -> None:
        attempted: list[str] = []
        original_import = builtins.__import__

        def guarded(name: str, *args: object, **kwargs: object) -> object:
            if name == "boto3" or name.startswith("botocore"):
                attempted.append(name)
                raise AssertionError("AWS SDK import attempted")
            return original_import(name, *args, **kwargs)

        spec = importlib.util.spec_from_file_location("pa73_isolated_boundary", MODULE_PATH)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            with patch("builtins.__import__", side_effect=guarded):
                spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)
        self.assertEqual([], attempted)

    def test_disabled_gate_reads_only_gate_and_does_zero_sdk_work(self) -> None:
        reads: list[str] = []
        sessions: list[dict[str, str]] = []

        def getenv(name: str, default: str | None = None) -> str | None:
            reads.append(name)
            return default

        def session_factory(**kwargs: str) -> FakeSession:
            sessions.append(kwargs)
            return FakeSession()

        with (
            patch("crypto_trust_agent.infrastructure.aws.bedrock_reasoning.os.getenv", side_effect=getenv),
            self.assertRaisesRegex(RuntimeError, "requires explicit opt-in"),
        ):
            ExplicitLiveBedrockClient.from_environment(
                session_factory=session_factory,
                config_factory=FakeConfigFactory(),
            )
        self.assertEqual(["PA73_BEDROCK_LIVE_INTEGRATION"], reads)
        self.assertEqual([], sessions)

    def test_missing_guardrail_fails_before_session_or_client(self) -> None:
        values = {
            "PA73_BEDROCK_LIVE_INTEGRATION": "1",
            "PA73_MAX_TOKENS": "1024",
            "PA73_TEMPERATURE": "0.0",
            "PA73_ALLOWED_REGIONS": "offline-region-1",
            "PA73_AWS_CREDENTIAL_MODE": "profile",
            "PA73_AWS_REGION": "offline-region-1",
            "PA73_AWS_PROFILE": "offline-profile",
            "PA73_PRIMARY_BASE_MODEL_ID": PRIMARY_BASE_MODEL,
            "PA73_FALLBACK_BASE_MODEL_ID": FALLBACK_BASE_MODEL,
            "PA73_PRIMARY_PROFILE_MODEL_ID": PRIMARY_PROFILE_MODEL,
            "PA73_FALLBACK_PROFILE_MODEL_ID": FALLBACK_PROFILE_MODEL,
            "PA73_GUARDRAIL_POLICY_VERSION": POLICY,
            "PA73_GUARDRAIL_APPROVED": "1",
        }
        sessions: list[dict[str, str]] = []

        def session_factory(**kwargs: str) -> FakeSession:
            sessions.append(kwargs)
            return FakeSession()

        with (
            patch.dict(os.environ, values, clear=True),
            self.assertRaisesRegex(ValueError, "not approved"),
        ):
            ExplicitLiveBedrockClient.from_environment(
                session_factory=session_factory,
                config_factory=FakeConfigFactory(),
            )
        self.assertEqual([], sessions)

    def test_unknown_role_causes_zero_sdk_calls(self) -> None:
        invoker, session, configs = make_invoker()
        with self.assertRaises(LiveBedrockError):
            invoker.invoke(
                operation_id="OP-OFFLINE",
                model_role="unknown",  # type: ignore[arg-type]
                body=request_body(),
                timeout_ms=1000,
                cancelled=lambda: False,
            )
        self.assertEqual([], session.calls)
        self.assertEqual([], configs.calls)


class ConverseBoundaryTests(unittest.TestCase):
    def test_profile_mapping_system_user_split_guardrail_and_zero_retries(self) -> None:
        for role, expected_profile in (
            ("primary", PRIMARY_PROFILE_MODEL),
            ("fallback", FALLBACK_PROFILE_MODEL),
        ):
            with self.subTest(role=role):
                invoker, session, configs = make_invoker()
                self.assertEqual(
                    b"{}",
                    invoker.invoke(
                        operation_id="OP-OFFLINE",
                        model_role=role,
                        body=request_body(),
                        timeout_ms=1000,
                        cancelled=lambda: False,
                    ),
                )
                sent = session.runtime.calls[0]
                self.assertEqual(expected_profile, sent["modelId"])
                self.assertEqual([{"text": SYSTEM_INSTRUCTION}], sent["system"])
                user_text = sent["messages"][0]["content"][0]["text"]
                self.assertNotIn("system_instruction", user_text)
                self.assertNotIn("model_role", user_text)
                self.assertNotIn("operation_id", user_text)
                self.assertNotIn("toolConfig", sent)
                self.assertNotIn("additionalModelRequestFields", sent)
                self.assertEqual("disabled", sent["guardrailConfig"]["trace"])
                self.assertEqual(
                    {"total_max_attempts": 1, "mode": "standard"},
                    configs.calls[-1]["retries"],
                )
                remaining = configs.calls[-1]["connect_timeout"]
                self.assertLessEqual(remaining, 1.0)
                self.assertEqual(remaining, configs.calls[-1]["read_timeout"])

    def test_invoke_rejects_invalid_timeout_before_sdk_io(self) -> None:
        for timeout_ms in (0, -1, True, 1.5, "1000", None):
            with self.subTest(timeout_ms=timeout_ms):
                invoker, session, configs = make_invoker()
                with self.assertRaises(TimeoutError):
                    invoker.invoke(
                        operation_id="OP-OFFLINE",
                        model_role="fallback",
                        body=request_body(),
                        timeout_ms=timeout_ms,  # type: ignore[arg-type]
                        cancelled=lambda: False,
                    )
                self.assertEqual([], session.calls)
                self.assertEqual([], configs.calls)
                self.assertEqual([], session.runtime.calls)

    def test_parsing_that_exhausts_deadline_causes_zero_sdk_io(self) -> None:
        clock = ManualClock()
        invoker, session, configs = make_invoker(monotonic=clock)
        original_loads = json.loads

        def slow_loads(*args: object, **kwargs: object) -> object:
            parsed = original_loads(*args, **kwargs)
            clock.advance(1.1)
            return parsed

        with (
            patch(
                "crypto_trust_agent.infrastructure.aws.bedrock_reasoning.json.loads",
                side_effect=slow_loads,
            ),
            self.assertRaises(TimeoutError),
        ):
            invoker.invoke(
                operation_id="OP-OFFLINE",
                model_role="fallback",
                body=request_body(),
                timeout_ms=1000,
                cancelled=lambda: False,
            )
        self.assertEqual([], configs.calls)
        self.assertEqual([], session.calls)
        self.assertEqual([], session.runtime.calls)

    def test_partial_parsing_time_bounds_runtime_config_to_remainder(self) -> None:
        clock = ManualClock()
        invoker, session, configs = make_invoker(monotonic=clock)
        original_loads = json.loads

        def partial_loads(*args: object, **kwargs: object) -> object:
            parsed = original_loads(*args, **kwargs)
            clock.advance(0.75)
            return parsed

        with patch(
            "crypto_trust_agent.infrastructure.aws.bedrock_reasoning.json.loads",
            side_effect=partial_loads,
        ):
            self.assertEqual(
                b"{}",
                invoker.invoke(
                    operation_id="OP-OFFLINE",
                    model_role="fallback",
                    body=request_body(),
                    timeout_ms=1000,
                    cancelled=lambda: False,
                ),
            )
        self.assertEqual(1, len(configs.calls))
        connect_timeout = configs.calls[0]["connect_timeout"]
        self.assertIsInstance(connect_timeout, float)
        self.assertGreater(connect_timeout, 0.0)
        self.assertLessEqual(connect_timeout, 0.25)
        self.assertEqual(connect_timeout, configs.calls[0]["read_timeout"])
        self.assertEqual(1, len(session.runtime.calls))

    def test_accepts_only_exact_assistant_message_role_without_leakage(self) -> None:
        invoker, _, _ = make_invoker()
        self.assertEqual(
            b"{}",
            invoker.invoke(
                operation_id="OP-OFFLINE",
                model_role="fallback",
                body=request_body(),
                timeout_ms=1000,
                cancelled=lambda: False,
            ),
        )

        missing = object()
        for role in (missing, "user", "system", "TOP-SECRET-UNKNOWN", 7):
            with self.subTest(role=role):
                invoker, session, _ = make_invoker()
                message: dict[str, object] = {"content": [{"text": "raw-payload"}]}
                if role is not missing:
                    message["role"] = role
                session.runtime.response = {
                    "stopReason": "end_turn",
                    "output": {"message": message},
                    "vendorMetadata": "Authorization Bearer TOP-SECRET",
                }
                with self.assertRaises(LiveBedrockError) as caught:
                    invoker.invoke(
                        operation_id="OP-OFFLINE",
                        model_role="fallback",
                        body=request_body(),
                        timeout_ms=1000,
                        cancelled=lambda: False,
                    )
                rendered = repr(caught.exception).lower()
                self.assertEqual(
                    "LiveBedrockError('Bedrock Converse response is invalid')",
                    repr(caught.exception),
                )
                self.assertNotIn("top-secret", rendered)
                self.assertNotIn("raw-payload", rendered)
                self.assertNotIn("authorization", rendered)

    def test_rejects_reasoning_non_text_multiple_missing_and_oversized_output(self) -> None:
        bad_content = (
            [{"reasoningContent": {"reasoningText": {"text": "secret"}}}],
            [{"image": {"format": "png"}}],
            [{"text": "one"}, {"text": "two"}],
            [],
            [{"text": "x" * 1_048_577}],
        )
        for index, content in enumerate(bad_content):
            with self.subTest(index=index):
                invoker, session, _ = make_invoker()
                session.runtime.response = {
                    "stopReason": "end_turn",
                    "output": {
                        "message": {"role": "assistant", "content": content}
                    },
                }
                with self.assertRaisesRegex(LiveBedrockError, "response is invalid"):
                    invoker.invoke(
                        operation_id="OP-OFFLINE",
                        model_role="fallback",
                        body=request_body(),
                        timeout_ms=1000,
                        cancelled=lambda: False,
                    )

    def test_guardrail_intervention_is_fixed_and_vendor_exception_is_redacted(self) -> None:
        invoker, session, _ = make_invoker()
        session.runtime.response = {"stopReason": "guardrail_intervened"}
        with self.assertRaises(ProviderFailure) as caught:
            invoker.invoke(
                operation_id="OP-OFFLINE",
                model_role="primary",
                body=request_body(),
                timeout_ms=1000,
                cancelled=lambda: False,
            )
        self.assertEqual("guardrail_rejected", caught.exception.code)

        invoker, session, _ = make_invoker()
        session.runtime.response = RuntimeError(
            "Authorization Bearer TOP-SECRET Account UserId arn:secret"
        )
        with self.assertRaises(LiveBedrockError) as redacted:
            invoker.invoke(
                operation_id="OP-OFFLINE",
                model_role="primary",
                body=request_body(),
                timeout_ms=1000,
                cancelled=lambda: False,
            )
        rendered = repr(redacted.exception).lower()
        self.assertNotIn("secret", rendered)
        self.assertNotIn("authorization", rendered)
        self.assertNotIn("arn:", rendered)

    def test_pre_and_post_cancellation_discard_results(self) -> None:
        invoker, session, _ = make_invoker()
        with self.assertRaises(TimeoutError):
            invoker.invoke(
                operation_id="OP-OFFLINE",
                model_role="primary",
                body=request_body(),
                timeout_ms=1000,
                cancelled=lambda: True,
            )
        self.assertEqual([], session.calls)

        state = {"cancelled": False}
        invoker, session, _ = make_invoker()
        session.runtime.after_call = lambda: state.update(cancelled=True)
        with self.assertRaises(TimeoutError):
            invoker.invoke(
                operation_id="OP-OFFLINE",
                model_role="primary",
                body=request_body(),
                timeout_ms=1000,
                cancelled=lambda: state["cancelled"],
            )
        self.assertEqual(1, len(session.runtime.calls))

    def test_late_deadline_result_is_discarded(self) -> None:
        class LateClock:
            def __init__(self) -> None:
                self.reads = 0

            def __call__(self) -> float:
                self.reads += 1
                return 0.0 if self.reads <= 6 else 2.0

        invoker, session, _ = make_invoker(monotonic=LateClock())
        with self.assertRaises(TimeoutError):
            invoker.invoke(
                operation_id="OP-OFFLINE",
                model_role="fallback",
                body=request_body(),
                timeout_ms=1000,
                cancelled=lambda: False,
            )
        self.assertEqual(1, len(session.runtime.calls))


class HealthAndLifecycleTests(unittest.TestCase):
    @staticmethod
    def configure_healthy(session: FakeSession, role: str) -> None:
        if role == "primary":
            session.control.availability = {
                "modelId": PRIMARY_BASE_MODEL,
                "agreementAvailability": {"status": "AVAILABLE"},
                "authorizationStatus": "AUTHORIZED",
                "entitlementAvailability": "AVAILABLE",
                "regionAvailability": "AVAILABLE",
            }
            session.control.profile = {
                "inferenceProfileId": PRIMARY_PROFILE_MODEL,
                "status": "ACTIVE",
            }
        else:
            session.control.availability = {
                "modelId": FALLBACK_BASE_MODEL,
                "agreementAvailability": {"status": "NOT_AVAILABLE"},
                "authorizationStatus": "AUTHORIZED",
                "entitlementAvailability": "NOT_AVAILABLE",
                "regionAvailability": "AVAILABLE",
            }
            session.control.profile = {
                "inferenceProfileId": FALLBACK_PROFILE_MODEL,
                "status": "ACTIVE",
            }

    def test_anthropic_and_amazon_use_distinct_approved_predicates(self) -> None:
        for role in ("primary", "fallback"):
            with self.subTest(role=role):
                invoker, session, _ = make_invoker()
                self.configure_healthy(session, role)
                self.assertTrue(invoker.probe(
                    model_role=role,
                    timeout_ms=3000,
                    cancelled=lambda: False,
                ))
                self.assertEqual(["availability", "profile"], [
                    name for name, _ in session.control.calls
                ])
                self.assertLessEqual(len(session.control.calls), 2)

        invoker, session, _ = make_invoker()
        self.configure_healthy(session, "primary")
        session.control.availability["agreementAvailability"] = {
            "status": "NOT_AVAILABLE"
        }
        self.assertFalse(invoker.probe(
            model_role="primary", timeout_ms=3000, cancelled=lambda: False
        ))
        self.assertEqual(1, len(session.control.calls))

    def test_amazon_agreement_and_entitlement_are_not_health_gates(self) -> None:
        for agreement in ({"status": "NOT_AVAILABLE"}, None):
            with self.subTest(agreement=agreement):
                invoker, session, _ = make_invoker()
                self.configure_healthy(session, "fallback")
                if agreement is None:
                    session.control.availability.pop("agreementAvailability")
                else:
                    session.control.availability["agreementAvailability"] = agreement
                session.control.availability.pop("entitlementAvailability", None)
                self.assertTrue(invoker.probe(
                    model_role="fallback",
                    timeout_ms=3000,
                    cancelled=lambda: False,
                ))
                self.assertEqual(2, len(session.control.calls))

    def test_each_required_health_predicate_fails_closed(self) -> None:
        invoker, session, _ = make_invoker()
        self.configure_healthy(session, "fallback")
        session.control.profile["status"] = "UNKNOWN"
        self.assertFalse(invoker.probe(
            model_role="fallback", timeout_ms=3000, cancelled=lambda: False
        ))
        self.assertEqual(2, len(session.control.calls))

    def test_profile_client_uses_budget_remaining_after_availability(self) -> None:
        clock = ManualClock()
        invoker, session, configs = make_invoker(monotonic=clock)
        self.configure_healthy(session, "fallback")
        session.control.after_availability = lambda: clock.advance(2.6)

        self.assertTrue(invoker.probe(
            model_role="fallback", timeout_ms=3000, cancelled=lambda: False
        ))

        self.assertEqual(["availability", "profile"], [
            name for name, _ in session.control.calls
        ])
        self.assertEqual(2, len(session.calls))
        self.assertEqual(2, len(configs.calls))
        second_connect = configs.calls[1]["connect_timeout"]
        self.assertIsInstance(second_connect, float)
        self.assertGreater(second_connect, 0.0)
        self.assertLessEqual(second_connect, 0.4)
        self.assertEqual(second_connect, configs.calls[1]["read_timeout"])

    def test_exhausted_budget_after_availability_builds_no_profile_client(self) -> None:
        clock = ManualClock()
        invoker, session, configs = make_invoker(monotonic=clock)
        self.configure_healthy(session, "fallback")
        session.control.after_availability = lambda: clock.advance(3.0)

        with self.assertRaises(TimeoutError):
            invoker.probe(
                model_role="fallback", timeout_ms=3000, cancelled=lambda: False
            )

        self.assertEqual(["availability"], [
            name for name, _ in session.control.calls
        ])
        self.assertEqual(1, len(session.calls))
        self.assertEqual(1, len(configs.calls))

    def test_cancellation_after_availability_builds_no_profile_client(self) -> None:
        state = {"cancelled": False}
        invoker, session, configs = make_invoker()
        self.configure_healthy(session, "fallback")
        session.control.after_availability = lambda: state.update(cancelled=True)

        with self.assertRaises(TimeoutError):
            invoker.probe(
                model_role="fallback",
                timeout_ms=3000,
                cancelled=lambda: state["cancelled"],
            )

        self.assertEqual(["availability"], [
            name for name, _ in session.control.calls
        ])
        self.assertEqual(1, len(session.calls))
        self.assertEqual(1, len(configs.calls))

    def test_every_client_uses_exactly_one_fresh_bounded_config(self) -> None:
        invoker, session, configs = make_invoker()
        self.configure_healthy(session, "fallback")
        session.control.model = {
            "modelDetails": {
                "modelId": FALLBACK_BASE_MODEL,
                "modelLifecycle": {"status": "ACTIVE"},
            }
        }

        invoker.invoke(
            operation_id="OP-OFFLINE",
            model_role="fallback",
            body=request_body(),
            timeout_ms=1000,
            cancelled=lambda: False,
        )
        self.assertTrue(invoker.probe(
            model_role="fallback", timeout_ms=3000, cancelled=lambda: False
        ))
        self.assertTrue(invoker.lifecycle_preflight(
            model_role="fallback", timeout_ms=1000, cancelled=lambda: False
        ))

        self.assertEqual(4, len(session.calls))
        self.assertEqual(4, len(configs.calls))
        self.assertEqual(4, len({id(call[2]) for call in session.calls}))
        for call in configs.calls:
            self.assertEqual(call["connect_timeout"], call["read_timeout"])
            self.assertEqual(
                {"total_max_attempts": 1, "mode": "standard"},
                call["retries"],
            )
            self.assertNotIn("max_attempts", call["retries"])
        self.assertLessEqual(len(session.control.calls), 3)
        self.assertEqual(["availability", "profile", "lifecycle"], [
            name for name, _ in session.control.calls
        ])

    def test_lifecycle_preflight_is_separate_and_one_call_per_role(self) -> None:
        for role, model in (
            ("primary", PRIMARY_BASE_MODEL),
            ("fallback", FALLBACK_BASE_MODEL),
        ):
            with self.subTest(role=role):
                invoker, session, _ = make_invoker()
                session.control.model = {
                    "modelDetails": {
                        "modelId": model,
                        "modelLifecycle": {"status": "ACTIVE"},
                    }
                }
                self.assertTrue(invoker.lifecycle_preflight(
                    model_role=role,
                    timeout_ms=1000,
                    cancelled=lambda: False,
                ))
                self.assertEqual(["lifecycle"], [
                    name for name, _ in session.control.calls
                ])


class WrapperPropagationTests(unittest.TestCase):
    def test_wrapper_propagates_roles_without_embedding_them(self) -> None:
        recorder = RecordingReasoningInvoker()
        client = ExplicitLiveBedrockClient(recorder, enabled=True)
        for role in ("primary", "fallback"):
            operation_id = f"OP-{role.upper()}"
            recorder.configure(operation_id, b"{}")
            client.invoke(
                operation_id=operation_id,
                model_role=role,
                body=request_body(),
                timeout_ms=1234,
                cancelled=lambda: False,
            )
            client.probe(
                model_role=role,
                timeout_ms=3000,
                cancelled=lambda: False,
            )
        self.assertEqual(["primary", "fallback"], [
            call["model_role"] for call in recorder.calls
        ])
        self.assertEqual(["primary", "fallback"], recorder.probe_calls)
        self.assertEqual(1, client.max_attempts)
        self.assertEqual(0, client.hidden_retries)


if __name__ == "__main__":
    unittest.main()
