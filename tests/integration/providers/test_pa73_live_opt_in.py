"""PA73 Phase 2 no-live scaffold tests; never access AWS, network, or credentials."""
from __future__ import annotations

import builtins
import importlib.util
import os
import sys
import types
import unittest
from datetime import UTC, datetime
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

from crypto_trust_agent.application.dto.reasoning import ReasoningHealthCheckRequestDTO
from crypto_trust_agent.infrastructure.aws.bedrock_reasoning import (
    ExplicitLiveBedrockClient,
    OperationBoundRecordingClient,
    RecordingReasoningInvoker,
)
from crypto_trust_agent.infrastructure.reasoning.adapter import BedrockReasoningProvider
from tests.contract.shared_reasoning_assertions import deadline


class FixedClock:
    runtime_id = "pa73-integration-runtime"

    def now_utc(self, operation_id: str) -> datetime:
        del operation_id
        return datetime(2026, 8, 1, 2, 0, 0, tzinfo=UTC)

    def monotonic_ms(self, operation_id: str) -> int:
        del operation_id
        return 100_000


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
        with patch("builtins.__import__", side_effect=guarded):
            spec.loader.exec_module(module)
        self.assertEqual([], attempted)

    def test_disabled_gate_fails_before_sdk_credentials_or_invoker(self) -> None:
        invoker = RecordingReasoningInvoker()
        reads: list[str] = []
        original_getenv = os.getenv

        def getenv(name: str, default: str | None = None) -> str | None:
            reads.append(name)
            if name.startswith("AWS_") or "CREDENTIAL" in name or "PROFILE" in name:
                raise AssertionError("credential environment was read")
            return original_getenv(name, default)

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("crypto_trust_agent.infrastructure.aws.bedrock_reasoning.os.getenv", side_effect=getenv),
            self.assertRaisesRegex(RuntimeError, "requires explicit opt-in"),
        ):
            ExplicitLiveBedrockClient.from_environment(invoker)
        self.assertEqual(["PA73_BEDROCK_LIVE_INTEGRATION"], reads)
        self.assertEqual([], invoker.calls)
        self.assertEqual(0, invoker.probe_count)

    def test_recording_invoker_is_explicitly_non_production(self) -> None:
        invoker = RecordingReasoningInvoker()
        self.assertTrue(invoker.non_production)
        self.assertEqual(1, invoker.max_attempts)
        self.assertEqual(0, invoker.hidden_retries)

    def test_live_wrapper_propagates_injected_non_production_marker(self) -> None:
        recording = RecordingReasoningInvoker()
        self.assertTrue(
            ExplicitLiveBedrockClient(recording, enabled=True).non_production
        )

        class FutureProductionInvoker(RecordingReasoningInvoker):
            non_production = False

        future = FutureProductionInvoker()
        self.assertFalse(
            ExplicitLiveBedrockClient(future, enabled=True).non_production
        )
        self.assertEqual([], future.calls)
        self.assertEqual(0, future.probe_count)

    def test_explicit_opt_in_only_delegates_to_injected_recorder(self) -> None:
        invoker = RecordingReasoningInvoker()
        client = ExplicitLiveBedrockClient(invoker, enabled=True)
        self.assertTrue(client.non_production)
        response = client.invoke(
            operation_id="OP-PA73-OFFLINE",
            body=(
                b'{"request":{"operation_id":"OP-PA73-OFFLINE",'
                b'"untrusted_context_envelope":{"evidence_refs":[],"analysis_refs":[],'
                b'"limitations":[]}}}'
            ),
            timeout_ms=1_234,
            cancelled=lambda: False,
        )
        self.assertIsInstance(response, bytes)
        self.assertEqual(1, len(invoker.calls))
        self.assertEqual(1_234, invoker.calls[0]["timeout_ms"])

    def test_from_environment_needs_injected_phase2_invoker(self) -> None:
        with (
            patch.dict(os.environ, {"PA73_BEDROCK_LIVE_INTEGRATION": "1"}, clear=True),
            self.assertRaisesRegex(RuntimeError, "explicitly injected"),
        ):
            ExplicitLiveBedrockClient.from_environment()

    def test_rejects_hidden_retry_invoker(self) -> None:
        invoker = RecordingReasoningInvoker()
        invoker.hidden_retries = 1
        with self.assertRaises(ValueError):
            ExplicitLiveBedrockClient(invoker, enabled=True)
        self.assertEqual([], invoker.calls)

    def test_health_probe_does_not_invoke_reasoning(self) -> None:
        invoker = RecordingReasoningInvoker()
        adapter = BedrockReasoningProvider(
            OperationBoundRecordingClient(invoker), clock=FixedClock()
        )
        operation = "OP-PA73-HEALTH-NO-INVOKE"
        result = adapter.health_check(ReasoningHealthCheckRequestDTO(
            operation,
            "primary",
            deadline(operation, seconds=3, at="2026-08-01T02:00:03Z"),
        ))
        self.assertEqual("healthy", result.status)
        self.assertEqual([], invoker.calls)
        self.assertEqual(1, invoker.probe_count)
        self.assertEqual(0, adapter.invocation_count)


if __name__ == "__main__":
    unittest.main()
