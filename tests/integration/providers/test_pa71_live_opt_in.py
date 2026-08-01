"""PA71 offline opt-in boundary scaffold; not successful real Nova evidence."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Bind this Provider scaffold to the checked-out Core namespace package.
import types
core_package = types.ModuleType("crypto_trust_agent")
core_package.__path__ = [str(SRC / "crypto_trust_agent")]
sys.modules["crypto_trust_agent"] = core_package

from crypto_trust_agent.infrastructure.aws.nova_evidence_extractor import ExplicitLiveNovaClient


class RecordingInvoker:
    """Offline test double; this never loads an AWS SDK or performs network I/O."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def invoke(self, *, model_id: str, region: str, payload: Mapping[str, object],
               timeout_ms: int, cancelled: Callable[[], bool]) -> Mapping[str, object]:
        self.calls.append({
            "method": "invoke", "model_id": model_id, "region": region,
            "payload": dict(payload), "timeout_ms": timeout_ms,
            "cancelled": cancelled,
        })
        return {"recording_invoker": True}

    def probe(self, *, model_id: str, region: str, timeout_ms: int,
              cancelled: Callable[[], bool]) -> bool:
        self.calls.append({
            "method": "probe", "model_id": model_id, "region": region,
            "timeout_ms": timeout_ms, "cancelled": cancelled,
        })
        return True


class NoLiveByDefaultTests(unittest.TestCase):
    def test_environment_factory_fails_closed_without_explicit_flag(self) -> None:
        invoker = RecordingInvoker()
        with patch.dict(os.environ, {
            "PA71_NOVA_MODEL_ID": "configured-at-deployment",
            "PA71_AWS_REGION": "configured-at-deployment",
        }, clear=True):
            with self.assertRaises(RuntimeError):
                ExplicitLiveNovaClient.from_environment(invoker)
        self.assertEqual([], invoker.calls)

    def test_opt_in_boundary_delegates_invoke_and_probe_to_injected_recording_invoker(self) -> None:
        """Boundary/scaffold evidence only; this is not a successful real Nova integration."""
        invoker = RecordingInvoker()
        cancelled = lambda: False
        with patch.dict(os.environ, {
            "PA71_LIVE_INTEGRATION": "1",
            "PA71_NOVA_MODEL_ID": "deployment-model",
            "PA71_AWS_REGION": "deployment-region",
        }, clear=True):
            client = ExplicitLiveNovaClient.from_environment(invoker)
            response = client.invoke(
                operation_id="OP-OFFLINE-SCAFFOLD",
                payload={"mode": "extract"}, timeout_ms=1_234,
                cancelled=cancelled,
            )
            healthy = client.probe(timeout_ms=321, cancelled=cancelled)

        self.assertFalse(client.non_production)
        self.assertEqual({"recording_invoker": True}, response)
        self.assertTrue(healthy)
        self.assertEqual(["invoke", "probe"], [call["method"] for call in invoker.calls])
        self.assertEqual("deployment-model", invoker.calls[0]["model_id"])
        self.assertEqual("deployment-region", invoker.calls[0]["region"])
        self.assertEqual({"mode": "extract"}, invoker.calls[0]["payload"])
        self.assertEqual(1_234, invoker.calls[0]["timeout_ms"])
        self.assertIs(cancelled, invoker.calls[0]["cancelled"])
        self.assertEqual(321, invoker.calls[1]["timeout_ms"])
        self.assertIs(cancelled, invoker.calls[1]["cancelled"])


if __name__ == "__main__":
    unittest.main()
