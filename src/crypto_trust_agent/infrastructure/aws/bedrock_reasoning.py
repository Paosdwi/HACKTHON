"""PA73 Bedrock reasoning boundary; Phase 2 contains no AWS SDK client.

Import is credential-independent and never imports boto3. The explicit-live wrapper
can only delegate to an injected one-attempt invoker after an explicit opt-in.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from typing import Protocol


class ReasoningInvoker(Protocol):
    non_production: bool
    max_attempts: int
    hidden_retries: int

    def invoke(
        self, *, body: bytes, timeout_ms: int, cancelled: Callable[[], bool]
    ) -> bytes | str: ...

    def probe(self, *, timeout_ms: int, cancelled: Callable[[], bool]) -> bool: ...


class RecordingReasoningInvoker:
    """Explicit non-production fixture invoker with no SDK, credentials, or network."""

    non_production = True
    max_attempts = 1
    hidden_retries = 0

    def __init__(self) -> None:
        self.responses: dict[str, object] = {}
        self.calls: list[dict[str, object]] = []
        self.probe_result: object = True
        self.probe_count = 0

    def configure(self, operation_id: str, response: object) -> None:
        self.responses[operation_id] = response

    @staticmethod
    def _default_fixture(body: bytes) -> bytes:
        """Return a deterministic test fixture, never production model behavior."""
        outer = json.loads(body.decode("utf-8"))
        request = outer["request"]
        context = request["untrusted_context_envelope"]
        facts: list[dict[str, object]] = []
        inferences: list[dict[str, object]] = []
        conclusions: list[dict[str, object]] = []
        evidence = context["evidence_refs"]
        analyses = context["analysis_refs"]
        if evidence or analyses:
            facts.append({
                "fact_id": "FACT-001",
                "statement": "A bounded cited fact.",
                "evidence_refs": [evidence[0]["evidence_id"]] if evidence else [],
                "analysis_refs": [analyses[0]["analysis_id"]] if not evidence else [],
            })
            inferences.append({
                "inference_id": "INFER-001",
                "statement": "A bounded inference.",
                "fact_refs": ["FACT-001"],
                "confidence": "0.8",
            })
            conclusions.append({
                "conclusion_id": "CONCL-001",
                "statement": "A bounded conclusion.",
                "fact_refs": ["FACT-001"],
                "inference_refs": ["INFER-001"],
                "confidence": "0.75",
            })
        response = {
            "facts": facts,
            "inferences": inferences,
            "conclusions": conclusions,
            "limitations": context["limitations"],
            "watchpoints": [],
            "confidence_components": {
                "evidence_quality": "0.8",
                "consistency": "0.8",
                "coverage": "0.8",
                "overall": "0.8",
            },
        }
        return json.dumps(response, separators=(",", ":")).encode("utf-8")

    def invoke(
        self, *, body: bytes, timeout_ms: int, cancelled: Callable[[], bool]
    ) -> bytes | str:
        if cancelled():
            raise TimeoutError("recording invocation cancelled")
        payload = json.loads(body.decode("utf-8"))
        request = payload.get("request")
        if not isinstance(request, Mapping):
            raise TypeError("invalid fixture request")
        operation_id = request.get("operation_id")
        # Adapter intentionally does not put identity authority in the model envelope.
        # The local operation binding is carried separately by the client wrapper.
        if not isinstance(operation_id, str):
            operation_id = ""
        self.calls.append({
            "body": body,
            "timeout_ms": timeout_ms,
            "cancelled": cancelled,
        })
        configured = self.responses.get(operation_id)
        if isinstance(configured, BaseException):
            raise configured
        if configured is not None:
            if isinstance(configured, (bytes, str)):
                return configured
            raise TypeError("recording response must be JSON bytes or text")
        return self._default_fixture(body)

    def probe(self, *, timeout_ms: int, cancelled: Callable[[], bool]) -> bool:
        del timeout_ms
        self.probe_count += 1
        if cancelled():
            raise TimeoutError("recording probe cancelled")
        if isinstance(self.probe_result, BaseException):
            raise self.probe_result
        return self.probe_result is True


class ExplicitLiveBedrockClient:
    """Opt-in delegation scaffold only; Phase 2 deliberately has no concrete SDK client."""

    non_production = False
    max_attempts = 1
    hidden_retries = 0

    def __init__(self, invoker: ReasoningInvoker, *, enabled: bool) -> None:
        if not enabled:
            raise RuntimeError("Live Bedrock reasoning requires explicit opt-in")
        if invoker.max_attempts != 1 or invoker.hidden_retries != 0:
            raise ValueError("Bedrock invoker must use one attempt and zero hidden retries")
        self._invoker = invoker
        self.non_production = bool(invoker.non_production)

    @classmethod
    def from_environment(
        cls, invoker: ReasoningInvoker | None = None
    ) -> ExplicitLiveBedrockClient:
        if os.getenv("PA73_BEDROCK_LIVE_INTEGRATION") != "1":
            raise RuntimeError("Live Bedrock reasoning requires explicit opt-in")
        if invoker is None:
            raise RuntimeError("Phase 2 requires an explicitly injected Bedrock invoker")
        return cls(invoker, enabled=True)

    def invoke(
        self, *, operation_id: str, body: bytes, timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bytes | str:
        del operation_id
        return self._invoker.invoke(
            body=body, timeout_ms=timeout_ms, cancelled=cancelled
        )

    def probe(self, *, timeout_ms: int, cancelled: Callable[[], bool]) -> bool:
        return self._invoker.probe(timeout_ms=timeout_ms, cancelled=cancelled)


class OperationBoundRecordingClient:
    """Adapter-facing offline client that binds operation ID outside model input."""

    non_production = True
    max_attempts = 1
    hidden_retries = 0

    def __init__(self, invoker: RecordingReasoningInvoker | None = None) -> None:
        self.invoker = invoker or RecordingReasoningInvoker()

    def invoke(
        self, *, operation_id: str, body: bytes, timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bytes | str:
        decoded = json.loads(body.decode("utf-8"))
        request = decoded["request"]
        if not isinstance(request, dict):
            raise TypeError("invalid fixture request")
        request["operation_id"] = operation_id
        bound = json.dumps(decoded, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self.invoker.invoke(
            body=bound, timeout_ms=timeout_ms, cancelled=cancelled
        )

    def probe(self, *, timeout_ms: int, cancelled: Callable[[], bool]) -> bool:
        return self.invoker.probe(timeout_ms=timeout_ms, cancelled=cancelled)


__all__ = (
    "ExplicitLiveBedrockClient",
    "OperationBoundRecordingClient",
    "ReasoningInvoker",
    "RecordingReasoningInvoker",
)
