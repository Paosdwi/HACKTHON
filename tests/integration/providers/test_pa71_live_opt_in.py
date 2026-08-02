"""PA71 offline opt-in boundary scaffold; not successful real Nova evidence."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
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

from crypto_trust_agent.application.dto.evidence_extractor import ExtractionResultDTO
from crypto_trust_agent.infrastructure.aws.nova_evidence_extractor import (
    ExplicitLiveNovaClient,
)
from crypto_trust_agent.infrastructure.extraction.adapter_v2 import (
    NovaLiteEvidenceExtractorV2,
)
from tests.contract.shared_evidence_extractor_v2_assertions import repair_request

NOW = datetime(2026, 8, 1, 2, 0, 0, tzinfo=UTC)


class RecordingInvoker:
    """Offline test double; this never loads an AWS SDK or performs network I/O."""

    max_attempts = 1
    hidden_retries = 0

    def __init__(self, response: Mapping[str, object] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.response = response or {"recording_invoker": True}

    def invoke(
        self,
        *,
        model_id: str,
        region: str,
        payload: Mapping[str, object],
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> Mapping[str, object]:
        self.calls.append({
            "method": "invoke",
            "model_id": model_id,
            "region": region,
            "payload": dict(payload),
            "timeout_ms": timeout_ms,
            "cancelled": cancelled,
        })
        return self.response

    def probe(
        self,
        *,
        model_id: str,
        region: str,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bool:
        self.calls.append({
            "method": "probe",
            "model_id": model_id,
            "region": region,
            "timeout_ms": timeout_ms,
            "cancelled": cancelled,
        })
        return True


class InlineOnlyResolver:
    """The offline integration uses inline authority and performs no locator I/O."""

    non_production = True

    def resolve(
        self,
        *,
        locator: str,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> str:
        del locator, timeout_ms, cancelled
        raise AssertionError("inline repair must not resolve a locator")


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

    def test_live_boundary_rejects_invoker_hidden_retry_policy(self) -> None:
        class RetryingInvoker(RecordingInvoker):
            max_attempts = 2
            hidden_retries = 1

        with patch.dict(os.environ, {
            "PA71_LIVE_INTEGRATION": "1",
            "PA71_NOVA_MODEL_ID": "deployment-model",
            "PA71_AWS_REGION": "deployment-region",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "one attempt"):
                ExplicitLiveNovaClient.from_environment(RetryingInvoker())

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
                payload={"mode": "extract"},
                timeout_ms=1_234,
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

    def test_v2_adapter_binds_successful_repair_through_opt_in_boundary_offline(self) -> None:
        """Uses a recording invoker only; it is not real Nova/AWS evidence."""
        operation = "OP-REP-V2-OFFLINE-BOUNDARY"
        invoker = RecordingInvoker({
            "claims": [{
                "text": "Bounded repaired claim.",
                "quote": "BTC approval remains under review.",
                "related_assets": ["BTC"],
                "event_type": "regulatory",
                "sentiment": "neutral",
                "relevance": "high",
            }],
            "validation_errors": [],
            "usage": {"input_units": 12, "output_units": 6},
            "invocation_id": "INV-OFFLINE-V2",
        })
        with patch.dict(os.environ, {
            "PA71_LIVE_INTEGRATION": "1",
            "PA71_NOVA_MODEL_ID": "deployment-model",
            "PA71_AWS_REGION": "deployment-region",
        }, clear=True):
            client = ExplicitLiveNovaClient.from_environment(invoker)
            adapter = NovaLiteEvidenceExtractorV2(
                client,
                content_resolver=InlineOnlyResolver(),
                model_version="deployment-model-version",
                now_utc=lambda: NOW,
                monotonic_ms=lambda: 123_000,
                runtime_id="pa71-v2-offline-boundary",
            )
            result = adapter.repair(repair_request(operation))

        self.assertIsInstance(result, ExtractionResultDTO)
        self.assertEqual("valid", result.outcome)
        self.assertTrue(adapter.non_production)
        self.assertEqual(1, adapter.provider_invocation_count)
        self.assertEqual(1, len(invoker.calls))
        call = invoker.calls[0]
        self.assertEqual("invoke", call["method"])
        self.assertLessEqual(call["timeout_ms"], 20_000)
        self.assertEqual("repair", call["payload"]["mode"])
        for forbidden in (
            "task_id",
            "execution_id",
            "raw_record_id",
            "repair_authorization_hash",
        ):
            self.assertNotIn(forbidden, call["payload"])


if __name__ == "__main__":
    unittest.main()
