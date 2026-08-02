"""Fail-closed Amazon Bedrock reasoning boundary for PA73.

Importing this module never imports an AWS SDK. Production construction validates
its explicit live gate and complete local configuration before any SDK activity.
"""
from __future__ import annotations

import importlib
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from crypto_trust_agent.infrastructure.reasoning.adapter import (
    MAX_RESPONSE_BYTES,
    SYSTEM_INSTRUCTION,
    ModelRole,
    ProviderFailure,
)

PRIMARY_BASE_MODEL = "anthropic.claude-opus-4-8"
PRIMARY_PROFILE_MODEL = "us.anthropic.claude-opus-4-8"
FALLBACK_BASE_MODEL = "amazon.nova-2-lite-v1:0"
FALLBACK_PROFILE_MODEL = "us.amazon.nova-2-lite-v1:0"
_REQUIRED_ROLES = frozenset({"primary", "fallback"})
_RETRY_POLICY: dict[str, object] = {"total_max_attempts": 1, "mode": "standard"}
_MAX_REQUEST_BYTES = 589_824


class LiveBedrockError(RuntimeError):
    """Fixed local failure containing no SDK or provider content."""


class LiveBedrockDependencyError(LiveBedrockError):
    """The optional SDK is unavailable after all local checks passed."""


@dataclass(frozen=True, slots=True)
class GuardrailBinding:
    identifier: str
    version: str


@dataclass(frozen=True, slots=True)
class BedrockReasoningConfig:
    """Explicit immutable local authority; values are not production approval."""

    region: str
    profile_name: str
    max_tokens: int
    temperature: float
    base_model_ids: Mapping[ModelRole, str]
    profile_model_ids: Mapping[ModelRole, str]
    guardrails: Mapping[str, GuardrailBinding]
    approved_regions: frozenset[str]
    guardrail_approved: bool

    def __post_init__(self) -> None:
        base = dict(self.base_model_ids)
        profiles = dict(self.profile_model_ids)
        guardrails = dict(self.guardrails)
        expected_base = {
            "primary": PRIMARY_BASE_MODEL,
            "fallback": FALLBACK_BASE_MODEL,
        }
        expected_profiles = {
            "primary": PRIMARY_PROFILE_MODEL,
            "fallback": FALLBACK_PROFILE_MODEL,
        }
        valid_guardrails = bool(guardrails) and all(
            isinstance(policy, str)
            and policy
            and isinstance(binding, GuardrailBinding)
            and bool(binding.identifier)
            and bool(binding.version)
            for policy, binding in guardrails.items()
        )
        if (
            not self.region
            or self.region not in self.approved_regions
            or not self.profile_name
            or type(self.max_tokens) is not int
            or not 1 <= self.max_tokens <= 32_768
            or type(self.temperature) is not float
            or not 0.0 <= self.temperature <= 1.0
            or base != expected_base
            or profiles != expected_profiles
            or not self.guardrail_approved
            or not valid_guardrails
        ):
            raise ValueError("Bedrock reasoning configuration is not approved")
        object.__setattr__(self, "base_model_ids", MappingProxyType(base))
        object.__setattr__(self, "profile_model_ids", MappingProxyType(profiles))
        object.__setattr__(self, "guardrails", MappingProxyType(guardrails))
        object.__setattr__(self, "approved_regions", frozenset(self.approved_regions))


class BedrockConfigFactory(Protocol):
    def __call__(
        self,
        *,
        connect_timeout: float,
        read_timeout: float,
        retries: dict[str, object],
    ) -> object: ...


class BedrockSession(Protocol):
    def client(
        self, service_name: str, *, region_name: str, config: object
    ) -> object: ...


class BedrockSessionFactory(Protocol):
    def __call__(self, **kwargs: str) -> BedrockSession: ...


class ReasoningInvoker(Protocol):
    non_production: bool
    max_attempts: int
    hidden_retries: int

    def invoke(
        self,
        *,
        operation_id: str,
        model_role: ModelRole,
        body: bytes,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bytes | str: ...

    def probe(
        self,
        *,
        model_role: ModelRole,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bool: ...

    def lifecycle_preflight(
        self,
        *,
        model_role: ModelRole,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bool: ...


class RecordingReasoningInvoker:
    """Offline fixture invoker; it performs no SDK, credential, or network work."""

    non_production = True
    max_attempts = 1
    hidden_retries = 0

    def __init__(self) -> None:
        self.responses: dict[str, object] = {}
        self.calls: list[dict[str, object]] = []
        self.probe_result: object = True
        self.probe_results: dict[ModelRole, object] = {}
        self.probe_calls: list[ModelRole] = []
        self.preflight_result: object = True
        self.preflight_calls: list[ModelRole] = []

    @property
    def probe_count(self) -> int:
        return len(self.probe_calls)

    def configure(self, operation_id: str, response: object) -> None:
        self.responses[operation_id] = response

    @staticmethod
    def _default_fixture(body: bytes) -> bytes:
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
        self,
        *,
        operation_id: str,
        model_role: ModelRole,
        body: bytes,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bytes | str:
        if cancelled():
            raise TimeoutError("recording invocation cancelled")
        self.calls.append({
            "operation_id": operation_id,
            "model_role": model_role,
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

    def probe(
        self,
        *,
        model_role: ModelRole,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bool:
        del timeout_ms
        self.probe_calls.append(model_role)
        if cancelled():
            raise TimeoutError("recording probe cancelled")
        result = self.probe_results.get(model_role, self.probe_result)
        if isinstance(result, BaseException):
            raise result
        return result is True

    def lifecycle_preflight(
        self,
        *,
        model_role: ModelRole,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bool:
        del timeout_ms
        self.preflight_calls.append(model_role)
        if cancelled():
            raise TimeoutError("recording preflight cancelled")
        if isinstance(self.preflight_result, BaseException):
            raise self.preflight_result
        return self.preflight_result is True


class Boto3BedrockReasoningInvoker:
    """One-attempt Converse/control-plane invoker with per-call bounded clients."""

    non_production = False
    max_attempts = 1
    hidden_retries = 0

    def __init__(
        self,
        session: BedrockSession,
        config_factory: BedrockConfigFactory,
        config: BedrockReasoningConfig,
        *,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._session = session
        self._config_factory = config_factory
        self._config = config
        self._monotonic = monotonic or time.monotonic

    @classmethod
    def production(
        cls,
        config: BedrockReasoningConfig,
        *,
        enabled: bool,
        monotonic: Callable[[], float] | None = None,
        session_factory: BedrockSessionFactory | None = None,
        config_factory: BedrockConfigFactory | None = None,
    ) -> Boto3BedrockReasoningInvoker:
        if not enabled:
            raise RuntimeError("Live Bedrock reasoning requires explicit opt-in")
        # Force complete local validation before optional SDK import or Session work.
        if not isinstance(config, BedrockReasoningConfig):
            raise TypeError("Bedrock reasoning configuration is not approved")
        if (session_factory is None) != (config_factory is None):
            raise ValueError("Bedrock SDK factories must be provided together")
        if session_factory is None:
            try:
                boto3_module = importlib.import_module("boto3")
                config_module = importlib.import_module("botocore.config")
                session_factory = boto3_module.Session
                config_factory = config_module.Config
                if not callable(session_factory) or not callable(config_factory):
                    raise TypeError
            except Exception:  # noqa: BLE001 - optional SDK fails closed
                raise LiveBedrockDependencyError(
                    "Live Bedrock SDK dependency is unavailable"
                ) from None
        assert session_factory is not None
        assert config_factory is not None
        try:
            session = session_factory(profile_name=config.profile_name)
        except Exception:  # noqa: BLE001 - redact profile/session failures
            raise LiveBedrockError("Bedrock session setup failed") from None
        return cls(session, config_factory, config, monotonic=monotonic)

    def _remaining(self, deadline: float, cancelled: Callable[[], bool]) -> float:
        if cancelled():
            raise TimeoutError("Bedrock request cancelled")
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise TimeoutError("Bedrock request deadline exceeded")
        return remaining

    @staticmethod
    def _role(model_role: object) -> ModelRole:
        if model_role == "primary":
            return "primary"
        if model_role == "fallback":
            return "fallback"
        raise LiveBedrockError("Bedrock model role is invalid")

    def _client(
        self,
        service_name: str,
        *,
        deadline: float,
        cancelled: Callable[[], bool],
    ) -> object:
        remaining = self._remaining(deadline, cancelled)
        try:
            sdk_config = self._config_factory(
                connect_timeout=remaining,
                read_timeout=remaining,
                retries=dict(_RETRY_POLICY),
            )
            self._remaining(deadline, cancelled)
            return self._session.client(
                service_name,
                region_name=self._config.region,
                config=sdk_config,
            )
        except TimeoutError:
            raise
        except Exception:  # noqa: BLE001 - SDK setup remains Infrastructure-local
            raise LiveBedrockError("Bedrock request setup failed") from None

    def _guardrail_and_user(self, body: bytes) -> tuple[GuardrailBinding, str]:
        if not isinstance(body, bytes) or len(body) > _MAX_REQUEST_BYTES:
            raise LiveBedrockError("Bedrock request envelope is invalid")
        try:
            outer = json.loads(body.decode("utf-8"))
            if not isinstance(outer, dict) or set(outer) != {
                "system_instruction",
                "untrusted_input_begin",
                "request",
                "untrusted_input_end",
            }:
                raise ValueError
            if outer["system_instruction"] != SYSTEM_INSTRUCTION:
                raise ValueError
            request = outer["request"]
            if not isinstance(request, dict):
                raise TypeError
            if "model_role" in request or "operation_id" in request:
                raise ValueError
            policy = request.get("guardrail_policy_version")
            if not isinstance(policy, str):
                raise TypeError
            binding = self._config.guardrails.get(policy)
            if binding is None:
                raise ProviderFailure("guardrail_rejected")
            user_envelope = {
                "untrusted_input_begin": outer["untrusted_input_begin"],
                "request": request,
                "untrusted_input_end": outer["untrusted_input_end"],
            }
            user_text = json.dumps(
                user_envelope,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except ProviderFailure:
            raise
        except Exception:  # noqa: BLE001 - raw request must not enter errors
            raise LiveBedrockError("Bedrock request envelope is invalid") from None
        return binding, user_text

    def invoke(
        self,
        *,
        operation_id: str,
        model_role: ModelRole,
        body: bytes,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bytes:
        del operation_id
        if type(timeout_ms) is not int or timeout_ms <= 0:
            raise TimeoutError("Bedrock request deadline exceeded")
        deadline = self._monotonic() + (timeout_ms / 1_000)
        self._remaining(deadline, cancelled)
        role = self._role(model_role)
        binding, user_text = self._guardrail_and_user(body)
        self._remaining(deadline, cancelled)
        client = self._client(
            "bedrock-runtime", deadline=deadline, cancelled=cancelled
        )
        converse = getattr(client, "converse", None)
        if not callable(converse):
            raise LiveBedrockError("Bedrock runtime client is invalid")
        self._remaining(deadline, cancelled)
        try:
            response = converse(
                modelId=self._config.profile_model_ids[role],
                system=[{"text": SYSTEM_INSTRUCTION}],
                messages=[{
                    "role": "user",
                    "content": [{"text": user_text}],
                }],
                inferenceConfig={
                    "maxTokens": self._config.max_tokens,
                    "temperature": self._config.temperature,
                },
                guardrailConfig={
                    "guardrailIdentifier": binding.identifier,
                    "guardrailVersion": binding.version,
                    "trace": "disabled",
                },
            )
        except Exception:  # noqa: BLE001 - vendor exception is never exposed
            raise LiveBedrockError("Bedrock Converse request failed") from None
        self._remaining(deadline, cancelled)
        try:
            if not isinstance(response, Mapping):
                raise TypeError
            if response.get("stopReason") == "guardrail_intervened":
                raise ProviderFailure("guardrail_rejected")
            output = response.get("output")
            if not isinstance(output, Mapping):
                raise TypeError
            message = output.get("message")
            if not isinstance(message, Mapping):
                raise TypeError
            if message.get("role") != "assistant":
                raise ValueError
            content = message.get("content")
            if not isinstance(content, list) or len(content) != 1:
                raise ValueError
            block = content[0]
            if not isinstance(block, Mapping) or set(block) != {"text"}:
                raise ValueError
            text = block["text"]
            if not isinstance(text, str):
                raise TypeError
            raw = text.encode("utf-8")
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError
        except ProviderFailure:
            raise
        except Exception:  # noqa: BLE001 - reject all non-text/reasoning/tool output
            raise LiveBedrockError("Bedrock Converse response is invalid") from None
        self._remaining(deadline, cancelled)
        return raw

    @staticmethod
    def _availability_is_healthy(
        role: ModelRole, expected_model: str, response: object
    ) -> bool:
        if not isinstance(response, Mapping) or response.get("modelId") != expected_model:
            return False
        if response.get("authorizationStatus") != "AUTHORIZED":
            return False
        if response.get("regionAvailability") != "AVAILABLE":
            return False
        if role == "fallback":
            return True
        agreement = response.get("agreementAvailability")
        return (
            isinstance(agreement, Mapping)
            and agreement.get("status") == "AVAILABLE"
            and response.get("entitlementAvailability") == "AVAILABLE"
        )

    @staticmethod
    def _profile_is_healthy(expected_profile: str, response: object) -> bool:
        return (
            isinstance(response, Mapping)
            and response.get("inferenceProfileId") == expected_profile
            and response.get("status") == "ACTIVE"
        )

    def probe(
        self,
        *,
        model_role: ModelRole,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bool:
        role = self._role(model_role)
        deadline = self._monotonic() + (min(timeout_ms, 3_000) / 1_000)
        self._remaining(deadline, cancelled)

        availability_client = self._client(
            "bedrock", deadline=deadline, cancelled=cancelled
        )
        availability_call = getattr(
            availability_client, "get_foundation_model_availability", None
        )
        if not callable(availability_call):
            raise LiveBedrockError("Bedrock control client is invalid")
        self._remaining(deadline, cancelled)
        try:
            availability = availability_call(
                modelId=self._config.base_model_ids[role]
            )
        except Exception:  # noqa: BLE001 - provider details stay local
            raise LiveBedrockError("Bedrock availability probe failed") from None
        self._remaining(deadline, cancelled)
        if not self._availability_is_healthy(
            role, self._config.base_model_ids[role], availability
        ):
            return False

        profile_client = self._client(
            "bedrock", deadline=deadline, cancelled=cancelled
        )
        profile_call = getattr(profile_client, "get_inference_profile", None)
        if not callable(profile_call):
            raise LiveBedrockError("Bedrock control client is invalid")
        self._remaining(deadline, cancelled)
        try:
            profile = profile_call(
                inferenceProfileIdentifier=self._config.profile_model_ids[role]
            )
        except Exception:  # noqa: BLE001 - provider details stay local
            raise LiveBedrockError("Bedrock profile probe failed") from None
        self._remaining(deadline, cancelled)
        return self._profile_is_healthy(
            self._config.profile_model_ids[role], profile
        )

    def lifecycle_preflight(
        self,
        *,
        model_role: ModelRole,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bool:
        role = self._role(model_role)
        deadline = self._monotonic() + (timeout_ms / 1_000)
        self._remaining(deadline, cancelled)
        client = self._client("bedrock", deadline=deadline, cancelled=cancelled)
        get_model = getattr(client, "get_foundation_model", None)
        if not callable(get_model):
            raise LiveBedrockError("Bedrock control client is invalid")
        try:
            response = get_model(modelIdentifier=self._config.base_model_ids[role])
        except Exception:  # noqa: BLE001 - provider details stay local
            raise LiveBedrockError("Bedrock lifecycle preflight failed") from None
        self._remaining(deadline, cancelled)
        if not isinstance(response, Mapping):
            return False
        details = response.get("modelDetails")
        if not isinstance(details, Mapping):
            return False
        lifecycle = details.get("modelLifecycle")
        return (
            details.get("modelId") == self._config.base_model_ids[role]
            and isinstance(lifecycle, Mapping)
            and lifecycle.get("status") == "ACTIVE"
        )


class ExplicitLiveBedrockClient:
    """Thin opt-in adapter-facing client; routing remains an independent argument."""

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

    @staticmethod
    def _environment_config() -> BedrockReasoningConfig:
        try:
            max_tokens = int(os.getenv("PA73_MAX_TOKENS", ""))
            temperature = float(os.getenv("PA73_TEMPERATURE", ""))
        except ValueError:
            raise ValueError("Bedrock reasoning configuration is not approved") from None
        policy = os.getenv("PA73_GUARDRAIL_POLICY_VERSION", "").strip()
        identifier = os.getenv("PA73_GUARDRAIL_ID", "").strip()
        version = os.getenv("PA73_GUARDRAIL_VERSION", "").strip()
        allowed_regions = frozenset(
            item.strip()
            for item in os.getenv("PA73_ALLOWED_REGIONS", "").split(",")
            if item.strip()
        )
        if os.getenv("PA73_AWS_CREDENTIAL_MODE", "").strip() != "profile":
            raise ValueError("Bedrock reasoning configuration is not approved")
        return BedrockReasoningConfig(
            region=os.getenv("PA73_AWS_REGION", "").strip(),
            profile_name=os.getenv("PA73_AWS_PROFILE", "").strip(),
            max_tokens=max_tokens,
            temperature=temperature,
            base_model_ids={
                "primary": os.getenv("PA73_PRIMARY_BASE_MODEL_ID", "").strip(),
                "fallback": os.getenv("PA73_FALLBACK_BASE_MODEL_ID", "").strip(),
            },
            profile_model_ids={
                "primary": os.getenv("PA73_PRIMARY_PROFILE_MODEL_ID", "").strip(),
                "fallback": os.getenv("PA73_FALLBACK_PROFILE_MODEL_ID", "").strip(),
            },
            guardrails={policy: GuardrailBinding(identifier, version)} if policy else {},
            approved_regions=allowed_regions,
            guardrail_approved=os.getenv("PA73_GUARDRAIL_APPROVED", "") == "1",
        )

    @classmethod
    def from_environment(
        cls,
        invoker: ReasoningInvoker | None = None,
        *,
        session_factory: BedrockSessionFactory | None = None,
        config_factory: BedrockConfigFactory | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> ExplicitLiveBedrockClient:
        # This gate is intentionally the sole read before all config and SDK work.
        if os.getenv("PA73_BEDROCK_LIVE_INTEGRATION") != "1":
            raise RuntimeError("Live Bedrock reasoning requires explicit opt-in")
        config = cls._environment_config()
        if invoker is not None and (
            session_factory is not None or config_factory is not None
        ):
            raise ValueError("Bedrock invoker configuration is ambiguous")
        if invoker is None:
            invoker = Boto3BedrockReasoningInvoker.production(
                config,
                enabled=True,
                monotonic=monotonic,
                session_factory=session_factory,
                config_factory=config_factory,
            )
        return cls(invoker, enabled=True)

    def invoke(
        self,
        *,
        operation_id: str,
        model_role: ModelRole,
        body: bytes,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bytes | str:
        return self._invoker.invoke(
            operation_id=operation_id,
            model_role=model_role,
            body=body,
            timeout_ms=timeout_ms,
            cancelled=cancelled,
        )

    def probe(
        self,
        *,
        model_role: ModelRole,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bool:
        return self._invoker.probe(
            model_role=model_role,
            timeout_ms=timeout_ms,
            cancelled=cancelled,
        )

    def lifecycle_preflight(
        self,
        *,
        model_role: ModelRole,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bool:
        return self._invoker.lifecycle_preflight(
            model_role=model_role,
            timeout_ms=timeout_ms,
            cancelled=cancelled,
        )


class OperationBoundRecordingClient:
    """Adapter-facing offline client; operation identity never enters model input."""

    non_production = True
    max_attempts = 1
    hidden_retries = 0

    def __init__(self, invoker: RecordingReasoningInvoker | None = None) -> None:
        self.invoker = invoker or RecordingReasoningInvoker()

    def invoke(
        self,
        *,
        operation_id: str,
        model_role: ModelRole,
        body: bytes,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bytes | str:
        return self.invoker.invoke(
            operation_id=operation_id,
            model_role=model_role,
            body=body,
            timeout_ms=timeout_ms,
            cancelled=cancelled,
        )

    def probe(
        self,
        *,
        model_role: ModelRole,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bool:
        return self.invoker.probe(
            model_role=model_role,
            timeout_ms=timeout_ms,
            cancelled=cancelled,
        )

    def lifecycle_preflight(
        self,
        *,
        model_role: ModelRole,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bool:
        return self.invoker.lifecycle_preflight(
            model_role=model_role,
            timeout_ms=timeout_ms,
            cancelled=cancelled,
        )


__all__ = (
    "FALLBACK_BASE_MODEL",
    "FALLBACK_PROFILE_MODEL",
    "PRIMARY_BASE_MODEL",
    "PRIMARY_PROFILE_MODEL",
    "BedrockConfigFactory",
    "BedrockReasoningConfig",
    "BedrockSessionFactory",
    "Boto3BedrockReasoningInvoker",
    "ExplicitLiveBedrockClient",
    "GuardrailBinding",
    "LiveBedrockDependencyError",
    "LiveBedrockError",
    "OperationBoundRecordingClient",
    "ReasoningInvoker",
    "RecordingReasoningInvoker",
)
