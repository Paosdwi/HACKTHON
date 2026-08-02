"""Tests for the minimal task-role Bedrock demo boundary."""

from __future__ import annotations

import json
import unittest

from crypto_trust_agent.infrastructure.aws.bedrock_demo import (
    DemoBedrockConfig,
    DemoBedrockReasoningClient,
)


class _Runtime:
    def __init__(self) -> None:
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        payload = {
            "facts": [],
            "inferences": [],
            "conclusions": [],
            "limitations": ["bounded demo"],
            "watchpoints": [],
            "confidence_components": {
                "evidence_quality": "0.5",
                "consistency": "0.5",
                "coverage": "0.5",
                "overall": "0.5",
            },
        }
        return {"output": {"message": {"content": [{"text": f"```json\n{json.dumps(payload)}\n```"}]}}}


class _Session:
    def __init__(self, runtime: _Runtime) -> None:
        self.runtime = runtime

    def client(self, service_name, **kwargs):
        self.service_name = service_name
        self.kwargs = kwargs
        return self.runtime


class DemoBedrockTests(unittest.TestCase):
    def test_demo_defaults_allow_detailed_but_bounded_analysis(self) -> None:
        config = DemoBedrockConfig()
        self.assertEqual(4_096, config.max_tokens)
        self.assertEqual(0.2, config.temperature)

    def test_task_role_client_uses_converse_without_guardrail_gate(self) -> None:
        runtime = _Runtime()
        session = _Session(runtime)
        client = DemoBedrockReasoningClient(
            DemoBedrockConfig(),
            session=session,
            config_factory=lambda **kwargs: kwargs,
        )
        body = json.dumps({"request": {"untrusted_context_envelope": {}}}).encode()
        result = client.invoke(
            operation_id="OP-DEMO",
            model_role="primary",
            body=body,
            timeout_ms=5_000,
            cancelled=lambda: False,
        )
        self.assertEqual("bedrock-runtime", session.service_name)
        self.assertEqual("us.anthropic.claude-sonnet-4-20250514-v1:0", runtime.calls[0]["modelId"])
        self.assertNotIn("guardrailConfig", runtime.calls[0])
        self.assertEqual([], json.loads(result)["facts"])
        system_prompt = runtime.calls[0]["system"][0]["text"]
        self.assertIn("繁體中文", system_prompt)
        self.assertIn("逐一涵蓋", system_prompt)
        self.assertIn("正方與反方訊號", system_prompt)

    def test_ungrounded_claims_are_removed_before_core_validation(self) -> None:
        payload = {
            "facts": [
                {"fact_id": "FACT-1", "statement": "grounded", "evidence_refs": ["EVID-1", "EVID-FAKE"], "analysis_refs": []},
                {"fact_id": "FACT-2", "statement": "ungrounded", "evidence_refs": ["EVID-FAKE"], "analysis_refs": []},
            ],
            "inferences": [
                {"inference_id": "INFER-1", "statement": "kept", "fact_refs": ["FACT-1", "FACT-2"], "confidence": "0.5"},
            ],
            "conclusions": [
                {"conclusion_id": "CONCL-1", "statement": "kept", "fact_refs": ["FACT-1", "FACT-2"], "inference_refs": ["INFER-1"], "confidence": "0.5"},
            ],
            "limitations": [],
            "watchpoints": [],
            "confidence_components": {"evidence_quality": "0.5", "consistency": "0.5", "coverage": "0.5", "overall": "0.5"},
        }
        grounded = json.loads(DemoBedrockReasoningClient._drop_ungrounded_claims(
            json.dumps(payload).encode(),
            allowed_evidence={"EVID-1"},
            allowed_analysis=set(),
        ))
        self.assertEqual(["FACT-001"], [item["fact_id"] for item in grounded["facts"]])
        self.assertEqual(["FACT-001"], grounded["inferences"][0]["fact_refs"])
        self.assertEqual(["FACT-001"], grounded["conclusions"][0]["fact_refs"])
        self.assertEqual("INFER-001", grounded["inferences"][0]["inference_id"])
        self.assertEqual("CONCL-001", grounded["conclusions"][0]["conclusion_id"])


if __name__ == "__main__":
    unittest.main()
