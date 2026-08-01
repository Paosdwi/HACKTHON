"""PA72 offline live-readiness tests; never call AWS or load credentials."""
from __future__ import annotations

import builtins
import hashlib
import hmac
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import types
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
MODULE_PATH = (
    SRC
    / "crypto_trust_agent"
    / "infrastructure"
    / "aws"
    / "sagemaker_market_regime.py"
)
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Bind this Provider scaffold to the checked-out Core namespace package.
core_package = types.ModuleType("crypto_trust_agent")
core_package.__path__ = [str(SRC / "crypto_trust_agent")]
sys.modules["crypto_trust_agent"] = core_package

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.market_regime import (
    ExpectedModelDTO,
    FeatureDTO,
    FeatureWindowDTO,
    InferRequestDTO,
    MarketRegimeResultDTO,
)
from crypto_trust_agent.infrastructure.aws.sagemaker_market_regime import (
    SAGEMAKER_INVOKE_ENDPOINT_MAX_RESPONSE_BYTES,
    Boto3SageMakerInvoker,
    ExplicitLiveSageMakerClient,
    LiveSageMakerCredentialError,
    LiveSageMakerDependencyError,
    LiveSageMakerError,
)
from crypto_trust_agent.infrastructure.market_regime.adapter import (
    FeatureSchema,
    SageMakerMarketRegimeProvider,
)


class RecordingInvoker:
    """Offline invoker that does not load credentials, an SDK, or a network."""

    max_attempts = 1
    hidden_retries = 0

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def invoke(
        self,
        *,
        endpoint_name: str,
        region: str,
        body: bytes,
        content_type: str,
        accept: str,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bytes:
        self.calls.append(
            {
                "method": "invoke",
                "endpoint_name": endpoint_name,
                "region": region,
                "body": body,
                "content_type": content_type,
                "accept": accept,
                "timeout_ms": timeout_ms,
                "cancelled": cancelled,
            }
        )
        return b'{"recording_invoker":true}'

    def probe(
        self,
        *,
        endpoint_name: str,
        region: str,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bool:
        self.calls.append(
            {
                "method": "probe",
                "endpoint_name": endpoint_name,
                "region": region,
                "timeout_ms": timeout_ms,
                "cancelled": cancelled,
            }
        )
        return True


class FakeBody:
    def __init__(
        self,
        data: object = b'{"ok":true}',
        *,
        failure: Exception | None = None,
        after_read: Callable[[], None] | None = None,
    ) -> None:
        self.data = data
        self.failure = failure
        self.after_read = after_read
        self.read_arguments: list[tuple[object, ...]] = []
        self.close_count = 0

    def read(self, *args: object) -> object:
        self.read_arguments.append(args)
        if self.failure is not None:
            raise self.failure
        if self.after_read is not None:
            self.after_read()
        return self.data

    def close(self) -> None:
        self.close_count += 1


class FakeRuntimeClient:
    def __init__(
        self,
        response: object,
        *,
        failure: Exception | None = None,
        after_call: Callable[[], None] | None = None,
    ) -> None:
        self.response = response
        self.failure = failure
        self.after_call = after_call
        self.calls: list[dict[str, object]] = []

    def invoke_endpoint(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        if self.after_call is not None:
            self.after_call()
        return self.response


class FakeControlClient:
    def __init__(
        self,
        response: object,
        *,
        after_call: Callable[[], None] | None = None,
    ) -> None:
        self.response = response
        self.after_call = after_call
        self.calls: list[dict[str, object]] = []

    def describe_endpoint(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.after_call is not None:
            self.after_call()
        return self.response


class ConfigRecorder:
    def __init__(self, after_first: Callable[[], None] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.after_first = after_first

    def __call__(
        self,
        *,
        connect_timeout: float,
        read_timeout: float,
        retries: dict[str, object],
    ) -> object:
        config = {
            "connect_timeout": connect_timeout,
            "read_timeout": read_timeout,
            "retries": retries,
        }
        self.calls.append(config)
        if len(self.calls) == 1 and self.after_first is not None:
            self.after_first()
        return config


class ClientRecorder:
    def __init__(
        self,
        runtime_factory: Callable[[], object],
        control_factory: Callable[[], object],
    ) -> None:
        self.runtime_factory = runtime_factory
        self.control_factory = control_factory
        self.calls: list[dict[str, object]] = []
        self.clients: list[object] = []

    def __call__(
        self, service_name: str, *, region_name: str, config: object
    ) -> object:
        client = (
            self.runtime_factory()
            if service_name == "sagemaker-runtime"
            else self.control_factory()
        )
        self.calls.append(
            {
                "service_name": service_name,
                "region_name": region_name,
                "config": config,
            }
        )
        self.clients.append(client)
        return client


def object_factory(value: object) -> Callable[[], object]:
    return lambda: value


def post_call_change(
    outcome: str, state: list[bool], clock: list[float]
) -> Callable[[], None]:
    def change() -> None:
        if outcome == "cancel":
            state[0] = True
        else:
            clock[0] = 22.0

    return change


class SessionFactoryRecorder:
    def __init__(self, session: object, *, failure: Exception | None = None) -> None:
        self.session = session
        self.failure = failure
        self.calls: list[dict[str, str]] = []

    def __call__(self, **kwargs: str) -> object:
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return self.session


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def client(
        self, service_name: str, *, region_name: str, config: object
    ) -> object:
        self.calls.append(
            {
                "service_name": service_name,
                "region_name": region_name,
                "config": config,
            }
        )
        if service_name == "sagemaker":
            return FakeControlClient({"EndpointStatus": "InService"})
        return FakeRuntimeClient({"Body": FakeBody()})


@dataclass(frozen=True, slots=True)
class LiveConfiguration:
    model_name: str
    model_version: str
    fixture_path: Path
    expected_fixture_sha256: str


_LIVE_TEST_ENABLED_AT_IMPORT = (
    os.getenv("PA72_SAGEMAKER_LIVE_INTEGRATION") == "1"
)
_LIVE_CONFIGURATION_ERROR = "PA72 live integration configuration is incomplete"
_FIXTURE_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
# This is a live fixture harness limit, not a Core contract limit.
MAX_LIVE_FIXTURE_BYTES = 524_288


def _required_environment_text(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError
    return value


def _live_preflight() -> LiveConfiguration:
    try:
        if os.getenv("PA72_SAGEMAKER_LIVE_INTEGRATION") != "1":
            raise ValueError
        _required_environment_text("PA72_AWS_REGION")
        _required_environment_text("PA72_SAGEMAKER_ENDPOINT")
        model_name = _required_environment_text("PA72_SAGEMAKER_MODEL_NAME")
        model_version = _required_environment_text("PA72_SAGEMAKER_MODEL_VERSION")
        mode = _required_environment_text("PA72_AWS_CREDENTIAL_MODE")
        if mode not in {"profile", "runtime_role"}:
            raise ValueError
        if mode == "profile":
            _required_environment_text("PA72_AWS_PROFILE")
        if os.getenv("PA72_SAGEMAKER_LIVE_AUTHORIZED") != "1":
            raise ValueError
        expected_fixture_sha256 = _required_environment_text(
            "PA72_APPROVED_FIXTURE_SHA256"
        )
        if _FIXTURE_SHA256_PATTERN.fullmatch(expected_fixture_sha256) is None:
            raise ValueError
        fixture_path = Path(
            _required_environment_text("PA72_APPROVED_FIXTURE_PATH")
        )
        if not fixture_path.is_absolute():
            raise ValueError
        return LiveConfiguration(
            model_name,
            model_version,
            fixture_path,
            expected_fixture_sha256,
        )
    except Exception:  # noqa: BLE001 - all configuration details are sensitive
        raise RuntimeError(_LIVE_CONFIGURATION_ERROR) from None


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError
    return value


def _text(mapping: dict[str, object], key: str) -> str:
    value = mapping[key]
    if not isinstance(value, str):
        raise TypeError
    return value


def _approved_request(path: Path, expected_sha256: str) -> InferRequestDTO:
    try:
        with path.open("rb") as fixture:
            raw = fixture.read(MAX_LIVE_FIXTURE_BYTES + 1)
        if not isinstance(raw, bytes) or len(raw) > MAX_LIVE_FIXTURE_BYTES:
            raise ValueError
        actual_sha256 = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        if not hmac.compare_digest(actual_sha256, expected_sha256):
            raise ValueError
        decoded = raw.decode("utf-8")
        payload = _mapping(json.loads(decoded))
        window = _mapping(payload["feature_window"])
        expected_model = _mapping(payload["expected_model"])
        deadline = _mapping(payload["deadline"])
        raw_features = payload["features"]
        if not isinstance(raw_features, list):
            raise TypeError
        features: list[FeatureDTO] = []
        for item in raw_features:
            feature = _mapping(item)
            raw_source_refs = feature["source_refs"]
            if (
                not isinstance(raw_source_refs, list)
                or not raw_source_refs
                or not all(
                    isinstance(ref, str) and bool(ref)
                    for ref in raw_source_refs
                )
            ):
                raise TypeError
            features.append(
                FeatureDTO(
                    _text(feature, "name"),
                    _text(feature, "value"),
                    _text(feature, "calculation_version"),
                    tuple(raw_source_refs),
                )
            )
        request = InferRequestDTO(
            _text(payload, "operation_id"),
            _text(payload, "task_id"),
            _text(payload, "execution_id"),
            _text(payload, "asset"),
            _text(payload, "as_of"),
            FeatureWindowDTO(_text(window, "start"), _text(window, "end")),
            tuple(features),
            _text(payload, "input_feature_hash"),
            ExpectedModelDTO(
                _text(expected_model, "name"),
                _text(expected_model, "contract_version"),
            ),
            DeadlineDTO(
                _text(deadline, "schema_version"),
                _text(deadline, "operation_id"),
                _text(deadline, "deadline_at_utc"),
                deadline["budget_ms"],
                _text(deadline, "sent_at_utc"),
                deadline["safety_margin_ms"],
            ),
            _text(payload, "schema_version"),
        )
        if request.to_wire() != payload:
            raise ValueError
        return request
    except Exception:  # noqa: BLE001 - redact path, content, and validation details
        raise RuntimeError(_LIVE_CONFIGURATION_ERROR) from None


def _authorized_live_context() -> tuple[
    ExplicitLiveSageMakerClient,
    SageMakerMarketRegimeProvider,
    InferRequestDTO,
]:
    configuration = _live_preflight()
    request = _approved_request(
        configuration.fixture_path,
        configuration.expected_fixture_sha256,
    )
    if request.expected_model.name != configuration.model_name:
        raise RuntimeError(_LIVE_CONFIGURATION_ERROR)
    client = ExplicitLiveSageMakerClient.from_environment()
    provider = SageMakerMarketRegimeProvider(
        client,
        feature_schema=tuple(
            FeatureSchema(feature.name, feature.calculation_version)
            for feature in request.features
        ),
        model_name=configuration.model_name,
        model_version=configuration.model_version,
    )
    return client, provider, request


class NoLiveByDefaultTests(unittest.TestCase):
    def test_module_import_does_not_import_boto3_or_botocore(self) -> None:
        requested_sdk_modules: list[str] = []
        original_import = builtins.__import__

        def guarded_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "boto3" or name.startswith("botocore"):
                requested_sdk_modules.append(name)
                raise AssertionError("AWS SDK import attempted during module import")
            return original_import(name, *args, **kwargs)

        spec = importlib.util.spec_from_file_location(
            "pa72_isolated_sagemaker_boundary", MODULE_PATH
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        isolated = importlib.util.module_from_spec(spec)
        with patch("builtins.__import__", side_effect=guarded_import):
            spec.loader.exec_module(isolated)
        self.assertEqual([], requested_sdk_modules)

    def test_live_case_is_default_off_and_preflight_fails_before_sdk(self) -> None:
        self.assertTrue(
            getattr(
                AuthorizedSageMakerLiveIntegrationTests.test_authorized_live_round_trip,
                "__unittest_skip__",
                False,
            )
        )
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "crypto_trust_agent.infrastructure.aws."
                "sagemaker_market_regime.importlib.import_module"
            ) as lazy_import,
            self.assertRaisesRegex(RuntimeError, _LIVE_CONFIGURATION_ERROR),
        ):
            _authorized_live_context()
        lazy_import.assert_not_called()

    def test_each_missing_live_setting_fails_pre_sdk_and_pre_credentials(self) -> None:
        complete = {
            "PA72_SAGEMAKER_LIVE_INTEGRATION": "1",
            "PA72_AWS_REGION": "offline-region",
            "PA72_SAGEMAKER_ENDPOINT": "offline-endpoint",
            "PA72_SAGEMAKER_MODEL_NAME": "offline-model",
            "PA72_SAGEMAKER_MODEL_VERSION": "offline-version",
            "PA72_AWS_CREDENTIAL_MODE": "runtime_role",
            "PA72_APPROVED_FIXTURE_PATH": str(Path(__file__).resolve()),
            "PA72_APPROVED_FIXTURE_SHA256": "sha256:" + "a" * 64,
            "PA72_SAGEMAKER_LIVE_AUTHORIZED": "1",
        }
        for missing in tuple(complete):
            with self.subTest(missing=missing):
                environment = dict(complete)
                del environment[missing]
                with (
                    patch.dict(os.environ, environment, clear=True),
                    patch(
                        "crypto_trust_agent.infrastructure.aws."
                        "sagemaker_market_regime.importlib.import_module"
                    ) as lazy_import,
                    self.assertRaisesRegex(
                        RuntimeError, _LIVE_CONFIGURATION_ERROR
                    ) as caught,
                ):
                    _live_preflight()
                self.assertIsNone(caught.exception.__cause__)
                lazy_import.assert_not_called()

    def test_malformed_fixture_hash_fails_before_sdk_or_credentials(self) -> None:
        complete = {
            "PA72_SAGEMAKER_LIVE_INTEGRATION": "1",
            "PA72_AWS_REGION": "offline-region",
            "PA72_SAGEMAKER_ENDPOINT": "offline-endpoint",
            "PA72_SAGEMAKER_MODEL_NAME": "offline-model",
            "PA72_SAGEMAKER_MODEL_VERSION": "offline-version",
            "PA72_AWS_CREDENTIAL_MODE": "runtime_role",
            "PA72_APPROVED_FIXTURE_PATH": str(Path(__file__).resolve()),
            "PA72_SAGEMAKER_LIVE_AUTHORIZED": "1",
        }
        malformed = (
            "",
            " ",
            "sha256:" + "a" * 63,
            "sha256:" + "A" * 64,
            "sha256:" + "a" * 65,
            "md5:" + "a" * 64,
        )
        for expected_hash in malformed:
            with self.subTest(expected_hash=expected_hash):
                environment = {
                    **complete,
                    "PA72_APPROVED_FIXTURE_SHA256": expected_hash,
                }
                with (
                    patch.dict(os.environ, environment, clear=True),
                    patch(
                        "crypto_trust_agent.infrastructure.aws."
                        "sagemaker_market_regime.importlib.import_module"
                    ) as lazy_import,
                    self.assertRaisesRegex(
                        RuntimeError, _LIVE_CONFIGURATION_ERROR
                    ) as caught,
                ):
                    _live_preflight()
                self.assertIsNone(caught.exception.__cause__)
                lazy_import.assert_not_called()

    def test_preflight_does_not_probe_fixture_metadata(self) -> None:
        fixture_path = Path(__file__).resolve().parent / "not-created.json"
        environment = {
            "PA72_SAGEMAKER_LIVE_INTEGRATION": "1",
            "PA72_AWS_REGION": "offline-region",
            "PA72_SAGEMAKER_ENDPOINT": "offline-endpoint",
            "PA72_SAGEMAKER_MODEL_NAME": "offline-model",
            "PA72_SAGEMAKER_MODEL_VERSION": "offline-version",
            "PA72_AWS_CREDENTIAL_MODE": "runtime_role",
            "PA72_APPROVED_FIXTURE_PATH": str(fixture_path),
            "PA72_APPROVED_FIXTURE_SHA256": "sha256:" + "a" * 64,
            "PA72_SAGEMAKER_LIVE_AUTHORIZED": "1",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(
                Path,
                "is_file",
                side_effect=AssertionError("preflight called Path.is_file"),
            ) as is_file,
            patch.object(
                Path,
                "stat",
                side_effect=AssertionError("preflight called Path.stat"),
            ) as stat,
        ):
            configuration = _live_preflight()

        self.assertEqual(fixture_path, configuration.fixture_path)
        is_file.assert_not_called()
        stat.assert_not_called()

    def test_fixture_open_failures_are_redacted_and_pre_sdk(self) -> None:
        expected_sha256 = "sha256:" + "a" * 64
        secret = "SECRET-UNREADABLE-OS-DETAIL"
        original_open = Path.open
        class TrackingOpen:
            def __init__(self, failure: Exception | None) -> None:
                self.failure = failure
                self.calls: list[tuple[Path, str]] = []

            def __call__(self, path: Path, mode: str) -> object:
                self.calls.append((path, mode))
                if self.failure is not None:
                    raise self.failure
                return original_open(path, mode)

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            cases = (
                ("missing", directory / "SECRET-MISSING.json", None),
                ("directory", directory, None),
                ("unreadable", Path(__file__).resolve(), PermissionError(secret)),
            )
            for label, fixture_path, forced_failure in cases:
                with self.subTest(label=label):
                    tracking_open = TrackingOpen(forced_failure)
                    environment = {
                        "PA72_SAGEMAKER_LIVE_INTEGRATION": "1",
                        "PA72_AWS_REGION": "offline-region",
                        "PA72_SAGEMAKER_ENDPOINT": "offline-endpoint",
                        "PA72_SAGEMAKER_MODEL_NAME": "offline-model",
                        "PA72_SAGEMAKER_MODEL_VERSION": "offline-version",
                        "PA72_AWS_CREDENTIAL_MODE": "runtime_role",
                        "PA72_APPROVED_FIXTURE_PATH": str(fixture_path),
                        "PA72_APPROVED_FIXTURE_SHA256": expected_sha256,
                        "PA72_SAGEMAKER_LIVE_AUTHORIZED": "1",
                    }
                    with (
                        patch.dict(os.environ, environment, clear=True),
                        patch.object(
                            Path,
                            "open",
                            autospec=True,
                            side_effect=tracking_open,
                        ),
                        patch(
                            "crypto_trust_agent.infrastructure.aws."
                            "sagemaker_market_regime.importlib.import_module"
                        ) as lazy_import,
                        self.assertRaisesRegex(
                            RuntimeError,
                            f"^{re.escape(_LIVE_CONFIGURATION_ERROR)}$",
                        ) as caught,
                    ):
                        _authorized_live_context()

                    self.assertIsNone(caught.exception.__cause__)
                    self.assertEqual([(fixture_path, "rb")], tracking_open.calls)
                    lazy_import.assert_not_called()
                    rendered = repr(caught.exception)
                    for forbidden in (
                        str(fixture_path),
                        expected_sha256,
                        secret,
                    ):
                        self.assertNotIn(forbidden, rendered)

    def test_environment_factory_fails_before_lazy_import_without_flag(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "PA72_SAGEMAKER_ENDPOINT": "configured-endpoint",
                    "PA72_AWS_REGION": "configured-region",
                    "AWS_ACCESS_KEY_ID": "must-not-be-read",
                },
                clear=True,
            ),
            patch(
                "crypto_trust_agent.infrastructure.aws."
                "sagemaker_market_regime.importlib.import_module"
            ) as lazy_import,
            self.assertRaisesRegex(RuntimeError, "requires explicit opt-in"),
        ):
            ExplicitLiveSageMakerClient.from_environment()
        lazy_import.assert_not_called()

    def test_missing_optional_sdk_fails_closed_with_fixed_safe_error(self) -> None:
        secret = "SECRET-SYS-PATH-CREDENTIAL"
        with (
            patch.dict(
                os.environ,
                {
                    "PA72_SAGEMAKER_LIVE_INTEGRATION": "1",
                    "PA72_SAGEMAKER_ENDPOINT": "private-endpoint",
                    "PA72_AWS_REGION": "private-region",
                    "PA72_AWS_CREDENTIAL_MODE": "runtime_role",
                },
                clear=True,
            ),
            patch(
                "crypto_trust_agent.infrastructure.aws."
                "sagemaker_market_regime.importlib.import_module",
                side_effect=ImportError(secret),
            ),
            self.assertRaises(LiveSageMakerDependencyError) as caught,
        ):
            ExplicitLiveSageMakerClient.from_environment()
        self.assertEqual(
            "Live SageMaker SDK dependency is unavailable", str(caught.exception)
        )
        rendered = repr(caught.exception)
        for forbidden in (secret, "private-endpoint", "private-region"):
            self.assertNotIn(forbidden, rendered)

    def test_opt_in_delegates_to_injected_recording_invoker_only(self) -> None:
        """Offline scaffold evidence only; this is not a real SageMaker call."""
        invoker = RecordingInvoker()
        cancelled = lambda: False
        with patch.dict(
            os.environ,
            {
                "PA72_SAGEMAKER_LIVE_INTEGRATION": "1",
                "PA72_SAGEMAKER_ENDPOINT": "deployment-endpoint",
                "PA72_AWS_REGION": "deployment-region",
            },
            clear=True,
        ):
            client = ExplicitLiveSageMakerClient.from_environment(invoker)
            response = client.invoke(
                operation_id="OP-PA72-OFFLINE",
                body=b'{"feature":"0.1"}',
                timeout_ms=12_345,
                cancelled=cancelled,
            )
            healthy = client.probe(timeout_ms=987, cancelled=cancelled)
        self.assertEqual(b'{"recording_invoker":true}', response)
        self.assertTrue(healthy)
        self.assertEqual(
            ["invoke", "probe"], [call["method"] for call in invoker.calls]
        )
        self.assertEqual("deployment-endpoint", invoker.calls[0]["endpoint_name"])
        self.assertEqual("deployment-region", invoker.calls[0]["region"])
        self.assertEqual("application/json", invoker.calls[0]["content_type"])
        self.assertEqual(12_345, invoker.calls[0]["timeout_ms"])
        self.assertIs(cancelled, invoker.calls[0]["cancelled"])
        self.assertEqual(987, invoker.calls[1]["timeout_ms"])

    def test_rejects_invoker_with_hidden_retries(self) -> None:
        invoker = RecordingInvoker()
        invoker.hidden_retries = 1
        with self.assertRaises(ValueError):
            ExplicitLiveSageMakerClient(
                invoker,
                endpoint_name="endpoint",
                region="region",
                enabled=True,
            )
        self.assertEqual([], invoker.calls)


class ApprovedLiveFixtureOfflineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self._directory = Path(self._temporary_directory.name)

    @staticmethod
    def _payload() -> dict[str, object]:
        operation_id = "OP-PA72-APPROVED"
        return {
            "schema_version": "1.0.0",
            "operation_id": operation_id,
            "task_id": "TASK-PA72-APPROVED",
            "execution_id": "EXEC-PA72-APPROVED",
            "asset": "BTC",
            "as_of": "2026-08-01T00:00:00Z",
            "feature_window": {
                "start": "2026-07-03",
                "end": "2026-08-01",
            },
            "features": [
                {
                    "name": "return_14d",
                    "value": "-0.0312",
                    "calculation_version": "market-formulas-1.0.0",
                    "source_refs": ["DATASET:APPROVED"],
                }
            ],
            "input_feature_hash": "sha256:" + "b" * 64,
            "expected_model": {
                "name": "market-regime-xgboost",
                "contract_version": "1.0.0",
            },
            "deadline": {
                "schema_version": "1.0.0",
                "operation_id": operation_id,
                "deadline_at_utc": "2026-08-01T02:00:25Z",
                "budget_ms": 25_000,
                "sent_at_utc": "2026-08-01T02:00:00Z",
                "safety_margin_ms": 1_000,
            },
        }

    @staticmethod
    def _sha256(raw: bytes) -> str:
        return f"sha256:{hashlib.sha256(raw).hexdigest()}"

    def _write_raw(
        self, raw: bytes, *, name: str = "approved-fixture.json"
    ) -> tuple[Path, str]:
        path = self._directory / name
        path.write_bytes(raw)
        return path, self._sha256(raw)

    def _write_payload(
        self, payload: object, *, name: str = "approved-fixture.json"
    ) -> tuple[Path, str]:
        raw = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return self._write_raw(raw, name=name)

    def _assert_fixed_rejection(
        self,
        path: Path,
        expected_sha256: str,
        *,
        forbidden: tuple[str, ...] = (),
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError, f"^{re.escape(_LIVE_CONFIGURATION_ERROR)}$"
        ) as caught:
            _approved_request(path, expected_sha256)
        self.assertIsNone(caught.exception.__cause__)
        rendered = repr(caught.exception)
        for value in forbidden:
            self.assertNotIn(value, rendered)

    @staticmethod
    def _features(payload: dict[str, object]) -> list[object]:
        value = payload["features"]
        if not isinstance(value, list):
            raise TypeError("test payload features must be a list")
        return value

    def test_valid_fixture_hash_builds_exact_formal_request(self) -> None:
        payload = self._payload()
        path, expected_sha256 = self._write_payload(payload)

        request = _approved_request(path, expected_sha256)

        self.assertIsInstance(request, InferRequestDTO)
        self.assertEqual(payload, request.to_wire())
        self.assertEqual(payload["input_feature_hash"], request.input_feature_hash)

    def test_fixture_is_opened_once_and_read_once_with_bound(self) -> None:
        payload = self._payload()
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        class TrackingReader(io.BytesIO):
            def __init__(self, value: bytes) -> None:
                super().__init__(value)
                self.read_sizes: list[int] = []

            def read(self, size: int = -1) -> bytes:
                self.read_sizes.append(size)
                return super().read(size)

        class SingleOpenPath:
            def __init__(self, value: bytes) -> None:
                self.reader = TrackingReader(value)
                self.open_modes: list[str] = []

            def open(self, mode: str) -> TrackingReader:
                self.open_modes.append(mode)
                if len(self.open_modes) != 1:
                    raise AssertionError("fixture was opened more than once")
                return self.reader

        path = SingleOpenPath(raw)
        request = _approved_request(  # type: ignore[arg-type]
            path, self._sha256(raw)
        )

        self.assertIsInstance(request, InferRequestDTO)
        self.assertEqual(["rb"], path.open_modes)
        self.assertEqual([MAX_LIVE_FIXTURE_BYTES + 1], path.reader.read_sizes)

    def test_tampered_fixture_is_rejected_against_approved_hash(self) -> None:
        payload = self._payload()
        path, expected_sha256 = self._write_payload(payload)
        tampered = json.dumps(
            {**payload, "asset": "ETH"}, separators=(",", ":")
        ).encode("utf-8")
        path.write_bytes(tampered)

        self._assert_fixed_rejection(path, expected_sha256)

    def test_size_encoding_json_and_read_failures_are_fixed_and_safe(self) -> None:
        cases = (
            b"x" * (MAX_LIVE_FIXTURE_BYTES + 1),
            b"\xff\xfeSECRET-NON-UTF8",
            b'{"SECRET-RAW":',
        )
        for index, raw in enumerate(cases):
            with self.subTest(index=index):
                path, expected_sha256 = self._write_raw(
                    raw, name=f"invalid-{index}.json"
                )
                self._assert_fixed_rejection(
                    path,
                    expected_sha256,
                    forbidden=(str(path), "SECRET-NON-UTF8", "SECRET-RAW"),
                )

        secret = "SECRET-UNREADABLE-OS-DETAIL"
        path, expected_sha256 = self._write_payload(self._payload())
        with patch.object(Path, "open", side_effect=PermissionError(secret)):
            self._assert_fixed_rejection(
                path,
                expected_sha256,
                forbidden=(str(path), secret),
            )

    def test_source_refs_must_be_a_nonempty_list_of_nonempty_strings(self) -> None:
        for source_refs in ([7], [""], []):
            with self.subTest(source_refs=source_refs):
                payload = self._payload()
                feature = _mapping(self._features(payload)[0])
                feature["source_refs"] = source_refs
                path, expected_sha256 = self._write_payload(payload)

                self._assert_fixed_rejection(path, expected_sha256)

    def test_extra_and_missing_fields_fail_exact_round_trip(self) -> None:
        cases: list[dict[str, object]] = []

        extra_root = self._payload()
        extra_root["unexpected"] = "root"
        cases.append(extra_root)

        extra_feature = self._payload()
        _mapping(self._features(extra_feature)[0])["unexpected"] = "feature"
        cases.append(extra_feature)

        extra_nested = self._payload()
        _mapping(extra_nested["deadline"])["unexpected"] = "deadline"
        cases.append(extra_nested)

        missing_root = self._payload()
        del missing_root["task_id"]
        cases.append(missing_root)

        for index, payload in enumerate(cases):
            with self.subTest(index=index):
                path, expected_sha256 = self._write_payload(
                    payload, name=f"shape-{index}.json"
                )
                self._assert_fixed_rejection(path, expected_sha256)

    def test_dto_normalization_cannot_make_payload_approved(self) -> None:
        payload = self._payload()
        payload["as_of"] = "2026-08-01T00:00:00+00:00"
        path, expected_sha256 = self._write_payload(payload)

        self._assert_fixed_rejection(path, expected_sha256)

    def test_failure_repr_and_cause_redact_path_raw_and_hash_details(self) -> None:
        raw_secret = "SECRET-APPROVED-FIXTURE-CONTENT"
        path, expected_sha256 = self._write_raw(
            f'{{"raw":"{raw_secret}"'.encode(),
            name="SECRET-APPROVED-PATH.json",
        )
        wrong_hash = "sha256:" + "c" * 64

        self._assert_fixed_rejection(
            path,
            wrong_hash,
            forbidden=(
                str(path),
                raw_secret,
                expected_sha256,
                wrong_hash,
                "SECRET-APPROVED-PATH",
            ),
        )


class CredentialSelectionOfflineTests(unittest.TestCase):
    def _production_environment(self, **changes: str) -> dict[str, str]:
        environment = {
            "PA72_SAGEMAKER_LIVE_INTEGRATION": "1",
            "PA72_SAGEMAKER_ENDPOINT": "offline-endpoint",
            "PA72_AWS_REGION": "offline-region",
        }
        environment.update(changes)
        return environment

    def test_missing_blank_or_unknown_mode_fails_before_sdk_or_session(self) -> None:
        for mode in (None, "", " ", "unknown", "environment_keys", "access_key"):
            with self.subTest(mode=mode):
                environment = self._production_environment()
                if mode is not None:
                    environment["PA72_AWS_CREDENTIAL_MODE"] = mode
                session = SessionFactoryRecorder(FakeSession())
                with (
                    patch.dict(os.environ, environment, clear=True),
                    patch(
                        "crypto_trust_agent.infrastructure.aws."
                        "sagemaker_market_regime.importlib.import_module"
                    ) as lazy_import,
                    self.assertRaises(LiveSageMakerCredentialError) as caught,
                ):
                    Boto3SageMakerInvoker.production(
                        session_factory=session,
                        config_factory=ConfigRecorder(),
                    )
                self.assertEqual(
                    "Live SageMaker credential configuration is invalid",
                    str(caught.exception),
                )
                self.assertEqual([], session.calls)
                lazy_import.assert_not_called()

    def test_profile_without_nonblank_name_fails_before_session(self) -> None:
        for profile_name in (None, "", " "):
            with self.subTest(profile_name=profile_name):
                environment = self._production_environment(
                    PA72_AWS_CREDENTIAL_MODE="profile"
                )
                if profile_name is not None:
                    environment["PA72_AWS_PROFILE"] = profile_name
                session = SessionFactoryRecorder(FakeSession())
                with (
                    patch.dict(os.environ, environment, clear=True),
                    self.assertRaises(LiveSageMakerCredentialError),
                ):
                    Boto3SageMakerInvoker.production(
                        session_factory=session,
                        config_factory=ConfigRecorder(),
                    )
                self.assertEqual([], session.calls)

    def test_profile_uses_only_explicit_profile_session(self) -> None:
        fake_session = FakeSession()
        session = SessionFactoryRecorder(fake_session)
        with patch.dict(
            os.environ,
            self._production_environment(
                PA72_AWS_CREDENTIAL_MODE="profile",
                PA72_AWS_PROFILE="approved-profile",
            ),
            clear=True,
        ):
            invoker = Boto3SageMakerInvoker.production(
                session_factory=session,
                config_factory=ConfigRecorder(),
                monotonic=lambda: 100.0,
            )
            self.assertTrue(
                invoker.probe(
                    endpoint_name="offline-endpoint",
                    region="offline-region",
                    timeout_ms=1_000,
                    cancelled=lambda: False,
                )
            )
        self.assertEqual([{"profile_name": "approved-profile"}], session.calls)
        self.assertEqual(1, len(fake_session.calls))

    def test_runtime_role_uses_injected_default_session_factory_only(self) -> None:
        fake_session = FakeSession()
        session = SessionFactoryRecorder(fake_session)
        with (
            patch.dict(
                os.environ,
                self._production_environment(
                    PA72_AWS_CREDENTIAL_MODE="runtime_role"
                ),
                clear=True,
            ),
            patch(
                "crypto_trust_agent.infrastructure.aws."
                "sagemaker_market_regime.importlib.import_module"
            ) as lazy_import,
        ):
            invoker = Boto3SageMakerInvoker.production(
                session_factory=session,
                config_factory=ConfigRecorder(),
                monotonic=lambda: 100.0,
            )
            self.assertTrue(
                invoker.probe(
                    endpoint_name="offline-endpoint",
                    region="offline-region",
                    timeout_ms=1_000,
                    cancelled=lambda: False,
                )
            )
        self.assertEqual([{}], session.calls)
        self.assertEqual(1, len(fake_session.calls))
        lazy_import.assert_not_called()

    def test_profile_session_failure_has_no_default_chain_fallback(self) -> None:
        session = SessionFactoryRecorder(
            FakeSession(), failure=RuntimeError("SECRET-CREDENTIAL-FAILURE")
        )
        with (
            patch.dict(
                os.environ,
                self._production_environment(
                    PA72_AWS_CREDENTIAL_MODE="profile",
                    PA72_AWS_PROFILE="approved-profile",
                ),
                clear=True,
            ),
            self.assertRaisesRegex(
                LiveSageMakerError, "SageMaker session setup failed"
            ) as caught,
        ):
            Boto3SageMakerInvoker.production(
                session_factory=session,
                config_factory=ConfigRecorder(),
            )
        self.assertEqual([{"profile_name": "approved-profile"}], session.calls)
        self.assertNotIn("SECRET-CREDENTIAL-FAILURE", repr(caught.exception))


class ConcreteInvokerOfflineTests(unittest.TestCase):
    def _invoker(
        self,
        *,
        runtime_factory: Callable[[], object] | None = None,
        control_factory: Callable[[], object] | None = None,
        monotonic: Callable[[], float] | None = None,
        config: ConfigRecorder | None = None,
    ) -> tuple[Boto3SageMakerInvoker, ClientRecorder, ConfigRecorder]:
        body = FakeBody()
        clients = ClientRecorder(
            runtime_factory or (lambda: FakeRuntimeClient({"Body": body})),
            control_factory
            or (lambda: FakeControlClient({"EndpointStatus": "InService"})),
        )
        configs = config or ConfigRecorder()
        return (
            Boto3SageMakerInvoker(
                clients, configs, monotonic=monotonic or (lambda: 100.0)
            ),
            clients,
            configs,
        )

    def test_injected_factories_never_lazy_import_or_touch_credentials(self) -> None:
        invoker, clients, _ = self._invoker()
        with (
            patch.dict(
                os.environ,
                {
                    "PA72_SAGEMAKER_LIVE_INTEGRATION": "1",
                    "PA72_SAGEMAKER_ENDPOINT": "fake-endpoint",
                    "PA72_AWS_REGION": "fake-region",
                    "AWS_SECRET_ACCESS_KEY": "must-not-be-read",
                },
                clear=True,
            ),
            patch(
                "crypto_trust_agent.infrastructure.aws."
                "sagemaker_market_regime.importlib.import_module",
                side_effect=AssertionError("production import path used"),
            ) as lazy_import,
        ):
            client = ExplicitLiveSageMakerClient.from_environment(invoker)
            self.assertEqual(
                b'{"ok":true}',
                client.invoke(
                    operation_id="OP-OFFLINE",
                    body=b"{}",
                    timeout_ms=1_000,
                    cancelled=lambda: False,
                ),
            )
        lazy_import.assert_not_called()
        self.assertEqual(1, len(clients.calls))

    def test_each_operation_builds_new_client_with_exact_retry_policy(self) -> None:
        invoker, clients, configs = self._invoker()
        for _ in range(2):
            self.assertEqual(
                b'{"ok":true}',
                invoker.invoke(
                    endpoint_name="endpoint",
                    region="region",
                    body=b"{}",
                    content_type="application/json",
                    accept="application/json",
                    timeout_ms=2_000,
                    cancelled=lambda: False,
                ),
            )
            self.assertTrue(
                invoker.probe(
                    endpoint_name="endpoint",
                    region="region",
                    timeout_ms=500,
                    cancelled=lambda: False,
                )
            )
        self.assertEqual(
            ["sagemaker-runtime", "sagemaker"] * 2,
            [call["service_name"] for call in clients.calls],
        )
        self.assertEqual(4, len({id(client) for client in clients.clients}))
        self.assertEqual(4, len(configs.calls))
        for call, expected_bound in zip(
            clients.calls, (2.0, 0.5, 2.0, 0.5), strict=True
        ):
            config = call["config"]
            self.assertIsInstance(config, dict)
            self.assertEqual(
                {"total_max_attempts": 1, "mode": "standard"},
                config["retries"],
            )
            self.assertNotIn("max_attempts", config["retries"])
            self.assertGreater(config["connect_timeout"], 0)
            self.assertLessEqual(config["connect_timeout"], expected_bound)
            self.assertLessEqual(config["read_timeout"], expected_bound)

    def test_client_timeouts_are_reduced_to_current_remaining_budget(self) -> None:
        clock = [10.0]
        configs = ConfigRecorder(after_first=lambda: clock.__setitem__(0, 10.25))
        invoker, clients, _ = self._invoker(
            monotonic=lambda: clock[0], config=configs
        )
        self.assertTrue(
            invoker.probe(
                endpoint_name="endpoint",
                region="region",
                timeout_ms=1_000,
                cancelled=lambda: False,
            )
        )
        final_config = clients.calls[0]["config"]
        self.assertLessEqual(final_config["connect_timeout"], 0.75)
        self.assertLessEqual(final_config["read_timeout"], 0.75)
        self.assertEqual(
            {"total_max_attempts": 1, "mode": "standard"},
            final_config["retries"],
        )

    def test_invoke_and_probe_make_at_most_one_service_call(self) -> None:
        runtime = FakeRuntimeClient({"Body": FakeBody()})
        control = FakeControlClient({"EndpointStatus": "InService"})
        invoker, _, _ = self._invoker(
            runtime_factory=lambda: runtime,
            control_factory=lambda: control,
        )
        invoker.invoke(
            endpoint_name="endpoint",
            region="region",
            body=b"{}",
            content_type="application/json",
            accept="application/json",
            timeout_ms=1_000,
            cancelled=lambda: False,
        )
        self.assertTrue(
            invoker.probe(
                endpoint_name="endpoint",
                region="region",
                timeout_ms=1_000,
                cancelled=lambda: False,
            )
        )
        self.assertEqual(1, len(runtime.calls))
        self.assertEqual(1, len(control.calls))

    def test_probe_uses_control_plane_and_only_in_service_is_true(self) -> None:
        cases = (
            ({"EndpointStatus": "InService"}, True),
            ({"EndpointStatus": "Creating"}, False),
            ({"EndpointStatus": "Unknown"}, False),
            ({}, False),
            ({"EndpointStatus": 7}, False),
            (object(), False),
        )
        for response, expected in cases:
            with self.subTest(response=response):
                control = FakeControlClient(response)

                def forbidden_runtime() -> object:
                    raise AssertionError("probe created a runtime client")

                invoker, clients, _ = self._invoker(
                    runtime_factory=forbidden_runtime,
                    control_factory=object_factory(control),
                )
                actual = invoker.probe(
                    endpoint_name="endpoint",
                    region="region",
                    timeout_ms=1_000,
                    cancelled=lambda: False,
                )
                self.assertIs(expected, actual)
                self.assertEqual(["sagemaker"], [
                    call["service_name"] for call in clients.calls
                ])
                self.assertEqual(1, len(control.calls))
                self.assertEqual(
                    {"EndpointName": "endpoint"}, control.calls[0]
                )

    def test_pre_cancelled_or_expired_budget_starts_no_io(self) -> None:
        for timeout_ms, cancelled in ((1_000, lambda: True), (0, lambda: False)):
            with self.subTest(timeout_ms=timeout_ms):
                invoker, clients, configs = self._invoker()
                with self.assertRaises(TimeoutError):
                    invoker.invoke(
                        endpoint_name="endpoint",
                        region="region",
                        body=b"secret-payload",
                        content_type="application/json",
                        accept="application/json",
                        timeout_ms=timeout_ms,
                        cancelled=cancelled,
                    )
                self.assertEqual([], clients.calls)
                self.assertEqual([], configs.calls)

    def test_post_call_cancel_or_late_result_is_discarded_without_reentry(self) -> None:
        for outcome in ("cancel", "late"):
            with self.subTest(outcome=outcome):
                clock = [20.0]
                state = [False]
                body = FakeBody()
                runtime = FakeRuntimeClient(
                    {"Body": body},
                    after_call=post_call_change(outcome, state, clock),
                )
                invoker, clients, _ = self._invoker(
                    runtime_factory=object_factory(runtime),
                    monotonic=lambda values=clock: values[0],
                )
                with self.assertRaises(TimeoutError):
                    invoker.invoke(
                        endpoint_name="endpoint",
                        region="region",
                        body=b"{}",
                        content_type="application/json",
                        accept="application/json",
                        timeout_ms=1_000,
                        cancelled=lambda values=state: values[0],
                    )
                self.assertEqual(1, len(runtime.calls))
                self.assertEqual(1, len(clients.calls))
                self.assertEqual([], body.read_arguments)
                self.assertEqual(1, body.close_count)

    def test_probe_discards_post_call_cancelled_result_without_retry(self) -> None:
        cancelled = [False]
        control = FakeControlClient(
            {"EndpointStatus": "InService"},
            after_call=lambda: cancelled.__setitem__(0, True),
        )
        invoker, clients, _ = self._invoker(control_factory=lambda: control)
        with self.assertRaises(TimeoutError):
            invoker.probe(
                endpoint_name="endpoint",
                region="region",
                timeout_ms=1_000,
                cancelled=lambda: cancelled[0],
            )
        self.assertEqual(1, len(control.calls))
        self.assertEqual(1, len(clients.calls))

    def test_body_read_is_bounded_and_always_closed(self) -> None:
        self.assertEqual(
            6_291_456, SAGEMAKER_INVOKE_ENDPOINT_MAX_RESPONSE_BYTES
        )
        cases = (
            (FakeBody(b"normal"), b"normal", None),
            (
                FakeBody(
                    b"x" * (SAGEMAKER_INVOKE_ENDPOINT_MAX_RESPONSE_BYTES + 1)
                ),
                None,
                LiveSageMakerError,
            ),
            (
                FakeBody(failure=RuntimeError("SECRET-READ-FAILURE")),
                None,
                LiveSageMakerError,
            ),
        )
        for body, expected, error_type in cases:
            with self.subTest(error_type=error_type):
                runtime = FakeRuntimeClient({"Body": body})
                invoker, _, _ = self._invoker(
                    runtime_factory=object_factory(runtime)
                )
                if error_type is None:
                    actual = invoker.invoke(
                        endpoint_name="endpoint",
                        region="region",
                        body=b"{}",
                        content_type="application/json",
                        accept="application/json",
                        timeout_ms=1_000,
                        cancelled=lambda: False,
                    )
                    self.assertEqual(expected, actual)
                    self.assertIsInstance(actual, bytes)
                else:
                    with self.assertRaises(error_type):
                        invoker.invoke(
                            endpoint_name="endpoint",
                            region="region",
                            body=b"{}",
                            content_type="application/json",
                            accept="application/json",
                            timeout_ms=1_000,
                            cancelled=lambda: False,
                        )
                self.assertEqual(
                    [(SAGEMAKER_INVOKE_ENDPOINT_MAX_RESPONSE_BYTES + 1,)],
                    body.read_arguments,
                )
                self.assertNotIn((), body.read_arguments)
                self.assertEqual(1, body.close_count)

    def test_post_read_late_result_is_discarded_and_body_closed(self) -> None:
        clock = [30.0]
        body = FakeBody(
            b"late", after_read=lambda: clock.__setitem__(0, 32.0)
        )
        runtime = FakeRuntimeClient({"Body": body})
        invoker, _, _ = self._invoker(
            runtime_factory=lambda: runtime, monotonic=lambda: clock[0]
        )
        with self.assertRaises(TimeoutError):
            invoker.invoke(
                endpoint_name="endpoint",
                region="region",
                body=b"{}",
                content_type="application/json",
                accept="application/json",
                timeout_ms=1_000,
                cancelled=lambda: False,
            )
        self.assertEqual(
            [(SAGEMAKER_INVOKE_ENDPOINT_MAX_RESPONSE_BYTES + 1,)],
            body.read_arguments,
        )
        self.assertEqual(1, body.close_count)
        self.assertEqual(1, len(runtime.calls))

    def test_failures_are_fixed_and_do_not_leak_sensitive_values(self) -> None:
        endpoint = "SECRET-ENDPOINT"
        region = "SECRET-REGION"
        payload = b"SECRET-PAYLOAD"
        vendor_secret = "Authorization Bearer SECRET-TOKEN"
        scenarios: tuple[Callable[[], None], ...] = (
            lambda: self._invoke_failure(
                endpoint,
                region,
                payload,
                FakeRuntimeClient(
                    {}, failure=RuntimeError(vendor_secret)
                ),
            ),
            lambda: self._invoke_failure(
                endpoint,
                region,
                payload,
                FakeRuntimeClient(
                    {"Body": FakeBody(failure=RuntimeError(vendor_secret))}
                ),
            ),
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                with self.assertRaises(LiveSageMakerError) as caught:
                    scenario()
                rendered = repr(caught.exception)
                for forbidden in (
                    endpoint,
                    region,
                    payload.decode(),
                    vendor_secret,
                    "SECRET-TOKEN",
                ):
                    self.assertNotIn(forbidden, rendered)

    def _invoke_failure(
        self,
        endpoint: str,
        region: str,
        payload: bytes,
        runtime: FakeRuntimeClient,
    ) -> None:
        invoker, _, _ = self._invoker(runtime_factory=lambda: runtime)
        invoker.invoke(
            endpoint_name=endpoint,
            region=region,
            body=payload,
            content_type="application/json",
            accept="application/json",
            timeout_ms=1_000,
            cancelled=lambda: False,
        )


class AuthorizedSageMakerLiveIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(
        _LIVE_TEST_ENABLED_AT_IMPORT,
        "PA72 live SageMaker integration is explicitly disabled",
    )
    def test_authorized_live_round_trip(self) -> None:
        """Run only with explicit authorization and an approved external fixture."""
        client, provider, request = _authorized_live_context()
        if not client.probe(timeout_ms=3_000, cancelled=lambda: False):
            self.fail("PA72 live SageMaker endpoint is not ready")
        result = provider.infer(request)
        self.assertNotIsInstance(result, ErrorResultDTO)
        self.assertIsInstance(result, MarketRegimeResultDTO)


if __name__ == "__main__":
    unittest.main()
