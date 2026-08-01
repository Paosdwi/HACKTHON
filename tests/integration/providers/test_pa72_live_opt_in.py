"""PA72 offline live-boundary scaffold; not real SageMaker success evidence."""
from __future__ import annotations

import os
import sys
import types
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Bind this Provider scaffold to the checked-out Core namespace package.
core_package = types.ModuleType("crypto_trust_agent")
core_package.__path__ = [str(SRC / "crypto_trust_agent")]
sys.modules["crypto_trust_agent"] = core_package

from crypto_trust_agent.infrastructure.aws.sagemaker_market_regime import (
    ExplicitLiveSageMakerClient,
)


class RecordingInvoker:
    """Offline invoker that does not load credentials, an AWS SDK, or a network."""

    max_attempts = 1
    hidden_retries = 0

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def invoke(
        self, *, endpoint_name: str, region: str, body: bytes, content_type: str,
        accept: str, timeout_ms: int, cancelled: Callable[[], bool],
    ) -> bytes:
        self.calls.append({
            "method": "invoke", "endpoint_name": endpoint_name, "region": region,
            "body": body, "content_type": content_type, "accept": accept,
            "timeout_ms": timeout_ms, "cancelled": cancelled,
        })
        return b'{"recording_invoker":true}'

    def probe(
        self, *, endpoint_name: str, region: str, timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bool:
        self.calls.append({
            "method": "probe", "endpoint_name": endpoint_name, "region": region,
            "timeout_ms": timeout_ms, "cancelled": cancelled,
        })
        return True


class NoLiveByDefaultTests(unittest.TestCase):
    def test_environment_factory_fails_closed_without_flag_and_without_invocation(self) -> None:
        invoker = RecordingInvoker()
        with patch.dict(os.environ, {
            "PA72_SAGEMAKER_ENDPOINT": "configured-endpoint",
            "PA72_AWS_REGION": "configured-region",
            "AWS_ACCESS_KEY_ID": "must-not-be-read",
        }, clear=True), self.assertRaises(RuntimeError):
            ExplicitLiveSageMakerClient.from_environment(invoker)
        self.assertEqual([], invoker.calls)

    def test_opt_in_delegates_to_injected_recording_invoker_only(self) -> None:
        """Scaffold evidence only; this is not a successful real SageMaker call."""
        invoker = RecordingInvoker()
        cancelled = lambda: False
        with patch.dict(os.environ, {
            "PA72_SAGEMAKER_LIVE_INTEGRATION": "1",
            "PA72_SAGEMAKER_ENDPOINT": "deployment-endpoint",
            "PA72_AWS_REGION": "deployment-region",
        }, clear=True):
            client = ExplicitLiveSageMakerClient.from_environment(invoker)
            response = client.invoke(
                operation_id="OP-PA72-OFFLINE",
                body=b'{"feature":"0.1"}', timeout_ms=12_345,
                cancelled=cancelled,
            )
            healthy = client.probe(timeout_ms=987, cancelled=cancelled)
        self.assertEqual(b'{"recording_invoker":true}', response)
        self.assertTrue(healthy)
        self.assertEqual(["invoke", "probe"], [call["method"] for call in invoker.calls])
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
                invoker, endpoint_name="endpoint", region="region", enabled=True,
            )
        self.assertEqual([], invoker.calls)


if __name__ == "__main__":
    unittest.main()
