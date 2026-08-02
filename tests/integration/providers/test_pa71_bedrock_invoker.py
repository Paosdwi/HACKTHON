"""Offline tests for PA71's concrete Bedrock API boundary.

These tests inject every SDK seam.  They never resolve credentials or perform
network/AWS calls.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from crypto_trust_agent.infrastructure.aws.nova_evidence_extractor import (
    BEDROCK_MAX_RESPONSE_TEXT_BYTES,
    Boto3BedrockInvoker,
    ExplicitLiveNovaClient,
    LiveBedrockCredentialError,
    LiveBedrockDependencyError,
    LiveBedrockError,
    LiveBedrockRateLimitError,
    LiveBedrockTimeoutError,
    LiveBedrockUnavailableError,
)
from crypto_trust_agent.infrastructure.extraction.adapter import ProviderFailure
from crypto_trust_agent.application.dto.evidence_extractor import ExtractionResultDTO
from crypto_trust_agent.infrastructure.extraction.adapter_v2 import (
    NovaLiteEvidenceExtractorV2,
)
from tests.contract.shared_evidence_extractor_v2_assertions import repair_request


def valid_model_json() -> str:
    return json.dumps({
        "claims": [{
            "text": "BTC approval remains under review.",
            "quote": "BTC approval remains under review.",
            "related_assets": ["BTC"],
            "event_type": "regulatory",
            "sentiment": "neutral",
            "relevance": "high",
        }],
        "validation_errors": [],
    })


def bedrock_response(text: str | None = None) -> dict[str, object]:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": text or valid_model_json()}],
            }
        },
        "usage": {"inputTokens": 17, "outputTokens": 23},
        "ResponseMetadata": {"RequestId": "aws-request-id-safe-hash-input"},
    }


class ConfigRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return dict(kwargs)


class RuntimeClient:
    def __init__(
        self,
        response: object | None = None,
        *,
        failure: Exception | None = None,
        after_call: Callable[[], None] | None = None,
    ) -> None:
        self.response = response if response is not None else bedrock_response()
        self.failure = failure
        self.after_call = after_call
        self.calls: list[dict[str, object]] = []

    def converse(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        if self.after_call is not None:
            self.after_call()
        return self.response


class ControlClient:
    def __init__(
        self,
        base_response: object | None = None,
        profile_response: object | None = None,
    ) -> None:
        self.base_response = base_response or {
            "modelDetails": {"modelLifecycle": {"status": "ACTIVE"}}
        }
        self.profile_response = profile_response or {"status": "ACTIVE"}
        self.calls: list[dict[str, object]] = []

    def get_foundation_model(self, **kwargs: object) -> object:
        self.calls.append({"method": "base", **kwargs})
        return self.base_response

    def get_inference_profile(self, **kwargs: object) -> object:
        self.calls.append({"method": "profile", **kwargs})
        return self.profile_response


class ClientRecorder:
    def __init__(self, runtime: object, control: object) -> None:
        self.runtime = runtime
        self.control = control
        self.calls: list[dict[str, object]] = []

    def __call__(
        self, service_name: str, *, region_name: str, config: object
    ) -> object:
        self.calls.append({
            "service_name": service_name,
            "region_name": region_name,
            "config": config,
        })
        return self.runtime if service_name == "bedrock-runtime" else self.control


class FakeSession:
    def __init__(self, clients: ClientRecorder) -> None:
        self._clients = clients

    def client(
        self, service_name: str, *, region_name: str, config: object
    ) -> object:
        return self._clients(
            service_name, region_name=region_name, config=config
        )


class SessionFactoryRecorder:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.calls: list[dict[str, str]] = []

    def __call__(self, **kwargs: str) -> FakeSession:
        self.calls.append(dict(kwargs))
        return self.session


class SdkFailure(RuntimeError):
    def __init__(self, code: str, secret: str = "SECRET-SDK-DIAGNOSTIC") -> None:
        super().__init__(secret)
        self.response = {"Error": {"Code": code, "Message": secret}}


class ConcreteBedrockInvokerTests(unittest.TestCase):
    def make_invoker(
        self,
        *,
        runtime: RuntimeClient | None = None,
        control: ControlClient | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> tuple[
        Boto3BedrockInvoker,
        RuntimeClient,
        ControlClient,
        ClientRecorder,
        ConfigRecorder,
    ]:
        runtime = runtime or RuntimeClient()
        control = control or ControlClient()
        clients = ClientRecorder(runtime, control)
        configs = ConfigRecorder()
        invoker = Boto3BedrockInvoker(
            clients,
            configs,
            monotonic=monotonic or (lambda: 100.0),
        )
        return invoker, runtime, control, clients, configs

    def test_converse_request_and_safe_result_mapping(self) -> None:
        invoker, runtime, _, clients, configs = self.make_invoker()
        result = invoker.invoke(
            model_id="amazon.nova-2-lite-v1:0",
            region="us-west-2",
            payload={
                "mode": "repair",
                "authoritative_content": "BTC approval remains under review.",
            },
            timeout_ms=20_000,
            cancelled=lambda: False,
        )

        self.assertEqual(1, len(runtime.calls))
        call = runtime.calls[0]
        self.assertEqual("amazon.nova-2-lite-v1:0", call["modelId"])
        self.assertEqual(0.0, call["inferenceConfig"]["temperature"])
        self.assertEqual(8192, call["inferenceConfig"]["maxTokens"])
        prompt = call["messages"][0]["content"][0]["text"]
        self.assertIn("authoritative_content", prompt)
        self.assertNotIn("aws-request-id-safe-hash-input", repr(result))
        self.assertEqual({"input_units": 17, "output_units": 23}, result["usage"])
        self.assertRegex(result["invocation_id"], r"^INV-BR-[A-F0-9]{24}$")
        self.assertEqual("bedrock-runtime", clients.calls[0]["service_name"])
        self.assertEqual("us-west-2", clients.calls[0]["region_name"])
        self.assertEqual(1, len(configs.calls))
        self.assertEqual(
            {"total_max_attempts": 1, "mode": "standard"},
            configs.calls[0]["retries"],
        )
        self.assertLessEqual(configs.calls[0]["connect_timeout"], 20.0)
        self.assertLessEqual(configs.calls[0]["read_timeout"], 20.0)

    def test_concrete_invoker_composes_through_v2_successful_repair(self) -> None:
        invoker, runtime, _, _, _ = self.make_invoker()
        live_client = ExplicitLiveNovaClient(
            invoker,
            model_id="us.amazon.nova-2-lite-v1:0",
            region="us-west-2",
            enabled=True,
        )

        class InlineOnlyResolver:
            non_production = True

            def resolve(self, **_: object) -> str:
                raise AssertionError("inline repair must not resolve a locator")

        adapter = NovaLiteEvidenceExtractorV2(
            live_client,
            content_resolver=InlineOnlyResolver(),
            model_version="amazon.nova-2-lite-v1:0",
            now_utc=lambda: datetime(2026, 8, 1, 2, 0, tzinfo=UTC),
            monotonic_ms=lambda: 123_000,
            runtime_id="pa71-bedrock-offline-composition",
        )
        result = adapter.repair(repair_request("OP-REP-BEDROCK-COMPOSE"))

        self.assertIsInstance(result, ExtractionResultDTO)
        self.assertEqual("valid", result.outcome)
        self.assertEqual(1, len(runtime.calls))
        self.assertEqual("repair", json.loads(
            runtime.calls[0]["messages"][0]["content"][0]["text"].split("\n", 1)[1]
        )["mode"])

    def test_pre_cancelled_or_expired_budget_starts_no_io(self) -> None:
        for timeout_ms, cancelled in ((1_000, lambda: True), (0, lambda: False)):
            with self.subTest(timeout_ms=timeout_ms):
                invoker, runtime, _, clients, configs = self.make_invoker()
                with self.assertRaises(LiveBedrockTimeoutError):
                    invoker.invoke(
                        model_id="model",
                        region="region",
                        payload={"secret": "MUST-NOT-DISPATCH"},
                        timeout_ms=timeout_ms,
                        cancelled=cancelled,
                    )
                self.assertEqual([], runtime.calls)
                self.assertEqual([], clients.calls)
                self.assertEqual([], configs.calls)

    def test_late_or_cancelled_response_is_discarded_without_retry(self) -> None:
        for outcome in ("late", "cancel"):
            with self.subTest(outcome=outcome):
                clock = [50.0]
                cancelled = [False]

                def finish() -> None:
                    if outcome == "late":
                        clock[0] = 52.0
                    else:
                        cancelled[0] = True

                runtime = RuntimeClient(after_call=finish)
                invoker, _, _, clients, _ = self.make_invoker(
                    runtime=runtime, monotonic=lambda: clock[0]
                )
                with self.assertRaises(LiveBedrockTimeoutError):
                    invoker.invoke(
                        model_id="model",
                        region="region",
                        payload={"mode": "extract"},
                        timeout_ms=1_000,
                        cancelled=lambda: cancelled[0],
                    )
                self.assertEqual(1, len(runtime.calls))
                self.assertEqual(1, len(clients.calls))

    def test_response_accepts_only_an_exact_json_code_fence(self) -> None:
        fenced = f"```json\n{valid_model_json()}\n```"
        invoker, _, _, _, _ = self.make_invoker(
            runtime=RuntimeClient(bedrock_response(fenced))
        )

        result = invoker.invoke(
            model_id="model",
            region="region",
            payload={"mode": "extract"},
            timeout_ms=1_000,
            cancelled=lambda: False,
        )

        self.assertEqual(1, len(result["claims"]))
        self.assertEqual([], result["validation_errors"])

        for response_text in (
            f"Here is the result:\n{fenced}",
            f"{fenced}\nAdditional explanation",
            f"```python\n{valid_model_json()}\n```",
        ):
            with self.subTest(response_text=response_text[:20]):
                rejected, _, _, _, _ = self.make_invoker(
                    runtime=RuntimeClient(bedrock_response(response_text))
                )
                with self.assertRaises(LiveBedrockError):
                    rejected.invoke(
                        model_id="model",
                        region="region",
                        payload={"mode": "extract"},
                        timeout_ms=1_000,
                        cancelled=lambda: False,
                    )

    def test_response_is_strict_bounded_and_diagnostics_are_redacted(self) -> None:
        self.assertEqual(2_097_152, BEDROCK_MAX_RESPONSE_TEXT_BYTES)
        scenarios = (
            bedrock_response('{"claims":[],"validation_errors":[],"extra":1}'),
            bedrock_response("x" * (BEDROCK_MAX_RESPONSE_TEXT_BYTES + 1)),
            {"secret": "SECRET-RAW-RESPONSE"},
        )
        for response in scenarios:
            with self.subTest(response_type=type(response).__name__):
                invoker, _, _, _, _ = self.make_invoker(
                    runtime=RuntimeClient(response)
                )
                with self.assertRaises(LiveBedrockError) as caught:
                    invoker.invoke(
                        model_id="SECRET-MODEL",
                        region="SECRET-REGION",
                        payload={"secret": "SECRET-PAYLOAD"},
                        timeout_ms=1_000,
                        cancelled=lambda: False,
                    )
                rendered = repr(caught.exception)
                for forbidden in (
                    "SECRET-MODEL",
                    "SECRET-REGION",
                    "SECRET-PAYLOAD",
                    "SECRET-RAW-RESPONSE",
                ):
                    self.assertNotIn(forbidden, rendered)

    def test_service_errors_are_allowlist_classified_without_diagnostics(self) -> None:
        cases = (
            ("ModelTimeoutException", LiveBedrockTimeoutError),
            ("ThrottlingException", LiveBedrockRateLimitError),
            ("ServiceUnavailableException", LiveBedrockUnavailableError),
            ("AccessDeniedException", LiveBedrockError),
        )
        for code, expected in cases:
            with self.subTest(code=code):
                invoker, _, _, _, _ = self.make_invoker(
                    runtime=RuntimeClient(failure=SdkFailure(code))
                )
                with self.assertRaises(expected) as caught:
                    invoker.invoke(
                        model_id="model",
                        region="region",
                        payload={"mode": "extract"},
                        timeout_ms=1_000,
                        cancelled=lambda: False,
                    )
                self.assertNotIn("SECRET-SDK-DIAGNOSTIC", repr(caught.exception))

    def test_probe_supports_base_model_and_cross_region_profile(self) -> None:
        cases = (
            (
                "amazon.nova-2-lite-v1:0",
                "base",
                {"modelIdentifier": "amazon.nova-2-lite-v1:0"},
            ),
            (
                "us.amazon.nova-2-lite-v1:0",
                "profile",
                {"inferenceProfileIdentifier": "us.amazon.nova-2-lite-v1:0"},
            ),
        )
        for model_id, method, expected_args in cases:
            with self.subTest(model_id=model_id):
                invoker, _, control, clients, _ = self.make_invoker()
                self.assertTrue(invoker.probe(
                    model_id=model_id,
                    region="us-west-2",
                    timeout_ms=3_000,
                    cancelled=lambda: False,
                ))
                self.assertEqual(
                    {"method": method, **expected_args}, control.calls[0]
                )
                self.assertEqual("bedrock", clients.calls[0]["service_name"])


class ProductionFactoryTests(unittest.TestCase):
    @staticmethod
    def environment(**overrides: str) -> dict[str, str]:
        values = {
            "PA71_LIVE_INTEGRATION": "1",
            "PA71_NOVA_MODEL_ID": "us.amazon.nova-2-lite-v1:0",
            "PA71_AWS_REGION": "us-west-2",
        }
        values.update(overrides)
        return values

    def test_invalid_credential_mode_fails_before_sdk_import(self) -> None:
        for mode, profile in (("", ""), ("default", ""), ("profile", "")):
            with self.subTest(mode=mode):
                environment = self.environment(PA71_AWS_CREDENTIAL_MODE=mode)
                if profile:
                    environment["PA71_AWS_PROFILE"] = profile
                with (
                    patch.dict(os.environ, environment, clear=True),
                    patch(
                        "crypto_trust_agent.infrastructure.aws."
                        "nova_evidence_extractor.importlib.import_module"
                    ) as lazy_import,
                    self.assertRaises(LiveBedrockCredentialError),
                ):
                    Boto3BedrockInvoker.production()
                lazy_import.assert_not_called()

    def test_profile_uses_only_the_explicit_profile(self) -> None:
        runtime = RuntimeClient()
        control = ControlClient()
        clients = ClientRecorder(runtime, control)
        session_factory = SessionFactoryRecorder(FakeSession(clients))
        configs = ConfigRecorder()
        with patch.dict(
            os.environ,
            self.environment(
                PA71_AWS_CREDENTIAL_MODE="profile",
                PA71_AWS_PROFILE="approved-workshop-profile",
            ),
            clear=True,
        ):
            invoker = Boto3BedrockInvoker.production(
                session_factory=session_factory,
                config_factory=configs,
                monotonic=lambda: 100.0,
            )
            self.assertTrue(invoker.probe(
                model_id="amazon.nova-2-lite-v1:0",
                region="us-west-2",
                timeout_ms=3_000,
                cancelled=lambda: False,
            ))
        self.assertEqual(
            [{"profile_name": "approved-workshop-profile"}],
            session_factory.calls,
        )

    def test_environment_factory_wires_concrete_invoker_with_injected_sdk_seams(self) -> None:
        runtime = RuntimeClient()
        clients = ClientRecorder(runtime, ControlClient())
        configs = ConfigRecorder()
        with patch.dict(os.environ, self.environment(), clear=True):
            client = ExplicitLiveNovaClient.from_environment(
                client_factory=clients,
                config_factory=configs,
                monotonic=lambda: 100.0,
            )
            result = client.invoke(
                operation_id="OP-OFFLINE-CONCRETE",
                payload={"mode": "extract"},
                timeout_ms=1_000,
                cancelled=lambda: False,
            )
        self.assertEqual(1, len(runtime.calls))
        self.assertEqual("bedrock-runtime", clients.calls[0]["service_name"])
        self.assertRegex(result["invocation_id"], r"^INV-BR-[A-F0-9]{24}$")

    def test_missing_sdk_is_a_fixed_dependency_error(self) -> None:
        with (
            patch.dict(
                os.environ,
                self.environment(PA71_AWS_CREDENTIAL_MODE="runtime_role"),
                clear=True,
            ),
            patch(
                "crypto_trust_agent.infrastructure.aws."
                "nova_evidence_extractor.importlib.import_module",
                side_effect=ImportError("SECRET-IMPORT-DIAGNOSTIC"),
            ),
            self.assertRaises(LiveBedrockDependencyError) as caught,
        ):
            Boto3BedrockInvoker.production()
        self.assertNotIn("SECRET-IMPORT-DIAGNOSTIC", repr(caught.exception))

    def test_module_import_path_contains_no_eager_boto_import(self) -> None:
        module_path = (
            SRC
            / "crypto_trust_agent"
            / "infrastructure"
            / "aws"
            / "nova_evidence_extractor.py"
        )
        source = module_path.read_text(encoding="utf-8")
        self.assertNotIn("import boto3", source)
        self.assertNotIn("from botocore", source)

    def test_live_client_translates_only_safe_typed_failures(self) -> None:
        class FailingInvoker:
            max_attempts = 1
            hidden_retries = 0

            def __init__(self, failure: LiveBedrockError) -> None:
                self.failure = failure

            def invoke(self, **_: object) -> Mapping[str, object]:
                raise self.failure

            def probe(self, **_: object) -> bool:
                raise self.failure

        cases: tuple[tuple[LiveBedrockError, type[BaseException], str | None], ...] = (
            (LiveBedrockTimeoutError("secret"), TimeoutError, None),
            (
                LiveBedrockRateLimitError("secret"),
                ProviderFailure,
                "extractor_rate_limited",
            ),
            (
                LiveBedrockUnavailableError("secret"),
                ProviderFailure,
                "extractor_unavailable",
            ),
            (
                LiveBedrockError("secret"),
                ProviderFailure,
                "unexpected_provider_error",
            ),
        )
        for failure, expected_type, expected_code in cases:
            with self.subTest(failure=type(failure).__name__):
                client = ExplicitLiveNovaClient(
                    FailingInvoker(failure),
                    model_id="model",
                    region="region",
                    enabled=True,
                )
                with self.assertRaises(expected_type) as caught:
                    client.invoke(
                        operation_id="OP-LIVE",
                        payload={"mode": "extract"},
                        timeout_ms=1_000,
                        cancelled=lambda: False,
                    )
                self.assertNotIn("secret", repr(caught.exception))
                if expected_code is not None:
                    self.assertEqual(expected_code, caught.exception.code)


if __name__ == "__main__":
    unittest.main()
