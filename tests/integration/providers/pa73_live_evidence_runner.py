"""Explicit-only PA73 Bedrock live-evidence runner.

Importing this module performs no environment, fixture, credential, SDK, client, or
network work. Execute this file directly only under the separately approved plan.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar, Protocol, cast

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.reasoning import (
    AnalysisRefDTO,
    ContradictionDTO,
    EvidenceRefDTO,
    GenerateRequestDTO,
    OmissionDTO,
    ReasoningContextDTO,
    ReasoningHealthCheckRequestDTO,
    ReasoningResultDTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO
from crypto_trust_agent.infrastructure.aws.bedrock_reasoning import (
    FALLBACK_BASE_MODEL,
    FALLBACK_PROFILE_MODEL,
    PRIMARY_BASE_MODEL,
    PRIMARY_PROFILE_MODEL,
    BedrockReasoningConfig,
    Boto3BedrockReasoningInvoker,
    ExplicitLiveBedrockClient,
    GuardrailBinding,
)
from crypto_trust_agent.infrastructure.reasoning.adapter import (
    BedrockReasoningProvider,
    ModelRole,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "pa73" / "live_reasoning_context_v1.json"
FIXTURE_DISPLAY_PATH = "tests/fixtures/pa73/live_reasoning_context_v1.json"
POLICY_FIXTURE_DISPLAY_PATH = "tests/fixtures/pa73/guardrail_policy_cases_v1.json"
POLICY_FIXTURE_PATH = ROOT / POLICY_FIXTURE_DISPLAY_PATH
POLICY_FIXTURE_SHA256 = (
    "sha256:a8fc84bfe38189d93373faf3f7f7cd12a671bb034d73cec0b8a04ca29fd9a4e5"
)
POLICY_FIXTURE_SCHEMA_VERSION = "pa73-guardrail-policy-cases-1.0.0"
GUARDRAIL_POLICY_VERSION = "reasoning-guardrail-1.0.0"
REGION = "us-west-2"
PROFILE = "default"
SCHEMA_VERSION = "1.0.0"
MAX_TOKENS = 1024
TEMPERATURE = 0.0
_MAX_FIXTURE_BYTES = 524_288
_MAX_POLICY_CASES = 12
_MAX_POLICY_CONTENT_PARTS = 8
_MAX_POLICY_CONTENT_PART_SCALARS = 500
_MAX_POLICY_CONTENT_SCALARS = 2_000
_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_NUMERIC_VERSION = re.compile(r"^[1-9][0-9]*$")
_GUARDRAIL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_POLICY_SOURCES = frozenset({"INPUT", "OUTPUT"})
_POLICY_ACTIONS = frozenset({"NONE", "GUARDRAIL_INTERVENED", "ANONYMIZED"})
_POLICY_CATEGORIES = frozenset(
    {
        "prompt_attack",
        "prompt_attack_benign_discussion",
        "harmful_request",
        "cryptotrust_security_reporting",
        "aws_access_key",
        "aws_secret_key",
        "password",
        "session_token",
        "authorization_header",
        "generic_api_key",
        "ordinary_pii",
        "output_intervention",
    }
)
_POLICY_EXPECTED_DETECTORS: Mapping[str, tuple[str, str]] = {
    "prompt_attack": ("content_filter", "PROMPT_ATTACK"),
    "harmful_request": ("content_filter", "VIOLENCE"),
    "aws_access_key": ("pii_entity", "AWS_ACCESS_KEY"),
    "aws_secret_key": ("pii_entity", "AWS_SECRET_KEY"),
    "password": ("pii_entity", "PASSWORD"),
    "session_token": ("regex", "PA73_SESSION_TOKEN"),
    "authorization_header": ("regex", "PA73_AUTHORIZATION_HEADER"),
    "generic_api_key": ("regex", "PA73_GENERIC_API_KEY"),
    "ordinary_pii": ("pii_entity", "EMAIL"),
    "output_intervention": ("content_filter", "VIOLENCE"),
}
_SPLIT_SECRET_CATEGORIES = frozenset(
    {
        "aws_access_key",
        "aws_secret_key",
        "password",
        "session_token",
        "authorization_header",
        "generic_api_key",
        "ordinary_pii",
    }
)
_CONTENT_FILTER_TYPES = frozenset({"PROMPT_ATTACK", "VIOLENCE"})
_PII_ENTITY_TYPES = frozenset(
    {"AWS_ACCESS_KEY", "AWS_SECRET_KEY", "PASSWORD", "EMAIL"}
)
_REGEX_NAMES = frozenset(
    {
        "PA73_SESSION_TOKEN",
        "PA73_AUTHORIZATION_HEADER",
        "PA73_GENERIC_API_KEY",
    }
)
_COMPLETE_ACCESS_KEY = re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}")
_COMPLETE_SECRET_VALUE = re.compile(r"[A-Za-z0-9/+=]{40,}")
_COMPLETE_SESSION_VALUE = re.compile(
    r"(?:AWS_SESSION_TOKEN|SESSION_TOKEN)\s*[:=]\s*[A-Za-z0-9/+=._-]{16,}"
)
_COMPLETE_AUTHORIZATION_VALUE = re.compile(
    r"Authorization\s*:\s*(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}"
)
_COMPLETE_API_KEY_VALUE = re.compile(
    r"(?:API_KEY|api_key|x-api-key)\s*[:=]\s*[A-Za-z0-9._-]{16,}"
)
_COMPLETE_SECRET_PATTERNS = (
    _COMPLETE_ACCESS_KEY,
    _COMPLETE_SECRET_VALUE,
    _COMPLETE_SESSION_VALUE,
    _COMPLETE_AUTHORIZATION_VALUE,
    _COMPLETE_API_KEY_VALUE,
)


class LiveEvidenceError(RuntimeError):
    """Fixed local preparation/execution failure with no external detail."""


class EnvReader(Protocol):
    def __call__(self, name: str, default: str | None = None) -> str | None: ...


class ReasoningClientFactory(Protocol):
    def __call__(
        self,
        config: BedrockReasoningConfig,
        counter: OperationCounter,
    ) -> ExplicitLiveBedrockClient: ...


class GuardrailPolicyClient(Protocol):
    def apply_guardrail(self, **kwargs: object) -> object: ...


class GuardrailPolicyClientFactory(Protocol):
    def __call__(
        self,
        configuration: GuardrailPolicyConfiguration,
        counter: GuardrailOperationCounter,
    ) -> GuardrailPolicyClient: ...


@dataclass(frozen=True, slots=True)
class LiveConfiguration:
    fixture_sha256: str
    bedrock: BedrockReasoningConfig
    guardrail_policy_version: str


@dataclass(frozen=True, slots=True)
class GuardrailPolicyConfiguration:
    guardrail_id: str
    guardrail_version: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class GuardrailPolicyCase:
    case_id: str
    source: str
    policy_category: str
    expected_action: str
    content_parts: tuple[str, ...]
    expected_detector_kind: str | None = None
    expected_detector: str | None = None

    @property
    def content(self) -> str:
        """Join a synthetic canary only at the ephemeral request boundary."""
        return "".join(self.content_parts)


class GuardrailOperationCounter:
    """Fail closed if a policy case is repeated or the fixed budget is exceeded."""

    def __init__(self) -> None:
        self._case_ids: set[str] = set()
        self._total = 0

    def record(self, case_id: str) -> None:
        if case_id in self._case_ids:
            raise LiveEvidenceError("guardrail_policy_budget_exceeded")
        self._case_ids.add(case_id)
        self._total += 1
        if self._total > _MAX_POLICY_CASES:
            raise LiveEvidenceError("guardrail_policy_budget_exceeded")

    @property
    def total(self) -> int:
        return self._total


class OperationCounter:
    """Enforces and reports only the approved operation ceilings."""

    _ROLE_LIMITS: ClassVar[dict[str, int]] = {
        "get_foundation_model": 1,
        "get_foundation_model_availability": 1,
        "get_inference_profile": 1,
        "converse": 1,
    }

    def __init__(self) -> None:
        self._logging = 0
        self._roles = {
            role: {operation: 0 for operation in self._ROLE_LIMITS}
            for role in ("primary", "fallback")
        }

    def record(self, role: str | None, operation: str) -> None:
        if role is None:
            if operation != "get_model_invocation_logging_configuration":
                raise LiveEvidenceError("live_evidence_operation_not_approved")
            self._logging += 1
            if self._logging > 1:
                raise LiveEvidenceError("live_evidence_budget_exceeded")
            return
        if role not in self._roles or operation not in self._ROLE_LIMITS:
            raise LiveEvidenceError("live_evidence_operation_not_approved")
        self._roles[role][operation] += 1
        if self._roles[role][operation] > self._ROLE_LIMITS[operation]:
            raise LiveEvidenceError("live_evidence_budget_exceeded")

    def snapshot(self) -> dict[str, object]:
        return {
            "get_model_invocation_logging_configuration": self._logging,
            "roles": {
                role: dict(counts) for role, counts in self._roles.items()
            },
        }


class _EvidenceClock:
    runtime_id = "pa73-live-evidence-runner"

    def __init__(self, now_utc: Callable[[], datetime]) -> None:
        self._now_utc = now_utc

    def now_utc(self, operation_id: str) -> datetime:
        del operation_id
        return self._now_utc()

    def monotonic_ms(self, operation_id: str) -> int:
        del operation_id
        return time.monotonic_ns() // 1_000_000


class _CountingClient:
    _METHODS: ClassVar[dict[str, str]] = {
        "get_foundation_model": "get_foundation_model",
        "get_foundation_model_availability": "get_foundation_model_availability",
        "get_inference_profile": "get_inference_profile",
        "converse": "converse",
        "get_model_invocation_logging_configuration": (
            "get_model_invocation_logging_configuration"
        ),
    }

    def __init__(
        self,
        client: object,
        config: BedrockReasoningConfig,
        counter: OperationCounter,
    ) -> None:
        self._client = client
        self._config = config
        self._counter = counter

    def _role(self, method: str, kwargs: Mapping[str, object]) -> str | None:
        if method == "get_model_invocation_logging_configuration":
            return None
        keys = {
            "get_foundation_model": "modelIdentifier",
            "get_foundation_model_availability": "modelId",
            "get_inference_profile": "inferenceProfileIdentifier",
            "converse": "modelId",
        }
        identifier = kwargs.get(keys[method])
        for role in ("primary", "fallback"):
            if identifier in {
                self._config.base_model_ids[role],
                self._config.profile_model_ids[role],
            }:
                return role
        raise LiveEvidenceError("live_evidence_model_binding_invalid")

    def __getattr__(self, name: str) -> object:
        target = getattr(self._client, name)
        if name not in self._METHODS or not callable(target):
            return target

        def counted(**kwargs: object) -> object:
            role = self._role(name, kwargs)
            self._counter.record(role, self._METHODS[name])
            return target(**kwargs)

        return counted


class _CountingSession:
    def __init__(
        self,
        session: object,
        config: BedrockReasoningConfig,
        counter: OperationCounter,
    ) -> None:
        self._session = session
        self._config = config
        self._counter = counter

    def client(
        self, service_name: str, *, region_name: str, config: object
    ) -> object:
        factory = getattr(self._session, "client", None)
        if not callable(factory):
            raise LiveEvidenceError("live_evidence_sdk_unavailable")
        client = factory(service_name, region_name=region_name, config=config)
        return _CountingClient(client, self._config, self._counter)


def _required(getenv: EnvReader, name: str) -> str:
    value = getenv(name, "")
    if not isinstance(value, str) or not value or value != value.strip():
        raise LiveEvidenceError("live_evidence_configuration_invalid")
    return value


def _valid_guardrail_id(value: str) -> bool:
    return _GUARDRAIL_ID_PATTERN.fullmatch(value) is not None


def _configuration(getenv: EnvReader) -> LiveConfiguration:
    values = {
        name: _required(getenv, name)
        for name in (
            "PA73_AWS_REGION",
            "PA73_AWS_PROFILE",
            "PA73_AWS_CREDENTIAL_MODE",
            "PA73_PRIMARY_BASE_MODEL_ID",
            "PA73_PRIMARY_PROFILE_MODEL_ID",
            "PA73_FALLBACK_BASE_MODEL_ID",
            "PA73_FALLBACK_PROFILE_MODEL_ID",
            "PA73_GUARDRAIL_ID",
            "PA73_GUARDRAIL_VERSION",
            "PA73_GUARDRAIL_POLICY_VERSION",
            "PA73_APPROVED_FIXTURE_SHA256",
            "PA73_MAX_TOKENS",
            "PA73_TEMPERATURE",
            "PA73_ALLOWED_REGIONS",
            "PA73_GUARDRAIL_APPROVED",
        )
    }
    expected = {
        "PA73_AWS_REGION": REGION,
        "PA73_AWS_PROFILE": PROFILE,
        "PA73_AWS_CREDENTIAL_MODE": "profile",
        "PA73_PRIMARY_BASE_MODEL_ID": PRIMARY_BASE_MODEL,
        "PA73_PRIMARY_PROFILE_MODEL_ID": PRIMARY_PROFILE_MODEL,
        "PA73_FALLBACK_BASE_MODEL_ID": FALLBACK_BASE_MODEL,
        "PA73_FALLBACK_PROFILE_MODEL_ID": FALLBACK_PROFILE_MODEL,
        "PA73_GUARDRAIL_POLICY_VERSION": GUARDRAIL_POLICY_VERSION,
        "PA73_MAX_TOKENS": str(MAX_TOKENS),
        "PA73_TEMPERATURE": str(TEMPERATURE),
        "PA73_ALLOWED_REGIONS": REGION,
        "PA73_GUARDRAIL_APPROVED": "1",
    }
    if any(values[name] != expected_value for name, expected_value in expected.items()):
        raise LiveEvidenceError("live_evidence_configuration_invalid")
    if not _valid_guardrail_id(values["PA73_GUARDRAIL_ID"]):
        raise LiveEvidenceError("live_evidence_configuration_invalid")
    fixture_hash = values["PA73_APPROVED_FIXTURE_SHA256"]
    if _HASH_PATTERN.fullmatch(fixture_hash) is None:
        raise LiveEvidenceError("live_evidence_configuration_invalid")
    guardrail_version = values["PA73_GUARDRAIL_VERSION"]
    if _NUMERIC_VERSION.fullmatch(guardrail_version) is None:
        raise LiveEvidenceError("live_evidence_configuration_invalid")
    try:
        max_tokens = int(values["PA73_MAX_TOKENS"])
        temperature = float(values["PA73_TEMPERATURE"])
    except ValueError:
        raise LiveEvidenceError("live_evidence_configuration_invalid") from None
    policy = values["PA73_GUARDRAIL_POLICY_VERSION"]
    try:
        bedrock = BedrockReasoningConfig(
            region=REGION,
            profile_name=PROFILE,
            max_tokens=max_tokens,
            temperature=temperature,
            base_model_ids={
                "primary": PRIMARY_BASE_MODEL,
                "fallback": FALLBACK_BASE_MODEL,
            },
            profile_model_ids={
                "primary": PRIMARY_PROFILE_MODEL,
                "fallback": FALLBACK_PROFILE_MODEL,
            },
            guardrails={
                policy: GuardrailBinding(
                    values["PA73_GUARDRAIL_ID"], guardrail_version
                )
            },
            approved_regions=frozenset({REGION}),
            guardrail_approved=True,
        )
    except (TypeError, ValueError):
        raise LiveEvidenceError("live_evidence_configuration_invalid") from None
    return LiveConfiguration(fixture_hash, bedrock, policy)


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _exact_mapping(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError
    return value


def _texts(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError
    return tuple(value)


def _context_from_fixture(raw: bytes, expected_sha256: str) -> ReasoningContextDTO:
    try:
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= _MAX_FIXTURE_BYTES:
            raise ValueError
        actual = "sha256:" + hashlib.sha256(raw).hexdigest()
        if not hmac.compare_digest(actual, expected_sha256):
            raise ValueError
        wrapper = _exact_mapping(
            json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs),
            {"synthetic_non_production_fixture", "reasoning_context"},
        )
        if wrapper["synthetic_non_production_fixture"] is not True:
            raise ValueError
        context_value = _exact_mapping(
            wrapper["reasoning_context"],
            {
                "schema_version",
                "question",
                "evidence_refs",
                "analysis_refs",
                "contradictions",
                "limitations",
                "omissions",
            },
        )
        raw_evidence = context_value["evidence_refs"]
        raw_analyses = context_value["analysis_refs"]
        raw_contradictions = context_value["contradictions"]
        raw_omissions = context_value["omissions"]
        if (
            not isinstance(raw_evidence, list)
            or not isinstance(raw_analyses, list)
            or not isinstance(raw_contradictions, list)
            or not isinstance(raw_omissions, list)
        ):
            raise TypeError
        evidence = tuple(
            EvidenceRefDTO(
                _text(item["evidence_id"]),
                _text(item["assessment_id"]),
                _text(item["excerpt"]),
                _text(item["stance"]),
                _text(item["confidence"]),
            )
            for raw_item in raw_evidence
            for item in (
                _exact_mapping(
                    raw_item,
                    {
                        "evidence_id",
                        "assessment_id",
                        "excerpt",
                        "stance",
                        "confidence",
                    },
                ),
            )
        )
        analyses = tuple(
            AnalysisRefDTO(
                _text(item["analysis_id"]),
                _text(item["analysis_version"]),
                _text(item["summary"]),
                _texts(item["source_refs"]),
            )
            for raw_item in raw_analyses
            for item in (
                _exact_mapping(
                    raw_item,
                    {"analysis_id", "analysis_version", "summary", "source_refs"},
                ),
            )
        )
        contradictions = tuple(
            ContradictionDTO(
                _text(item["contradiction_id"]),
                _texts(item["evidence_refs"]),
                _text(item["severity"]),
                _text(item["summary"]),
            )
            for raw_item in raw_contradictions
            for item in (
                _exact_mapping(
                    raw_item,
                    {"contradiction_id", "evidence_refs", "severity", "summary"},
                ),
            )
        )
        omissions = tuple(
            OmissionDTO(
                _text(item["kind"]),
                item["count"],  # type: ignore[arg-type]
                _text(item["reason"]),
            )
            for raw_item in raw_omissions
            for item in (
                _exact_mapping(raw_item, {"kind", "count", "reason"}),
            )
        )
        context = ReasoningContextDTO(
            _text(context_value["question"]),
            evidence,
            analyses,
            contradictions,
            _texts(context_value["limitations"]),
            omissions,
            _text(context_value["schema_version"]),
        )
        if context.to_wire() != context_value or not evidence or not analyses:
            raise ValueError
        return context
    except Exception:  # noqa: BLE001 - fixture path/content/details stay private
        raise LiveEvidenceError("live_evidence_fixture_invalid") from None


def _policy_required(getenv: EnvReader, name: str) -> str:
    value = getenv(name, "")
    if not isinstance(value, str) or not value or value != value.strip():
        raise LiveEvidenceError("guardrail_policy_configuration_invalid")
    return value


def _policy_configuration(getenv: EnvReader) -> GuardrailPolicyConfiguration:
    values = {
        name: _policy_required(getenv, name)
        for name in (
            "PA73_GUARDRAIL_POLICY_AWS_REGION",
            "PA73_GUARDRAIL_POLICY_AWS_PROFILE",
            "PA73_GUARDRAIL_POLICY_AWS_CREDENTIAL_MODE",
            "PA73_GUARDRAIL_ID",
            "PA73_GUARDRAIL_VERSION",
            "PA73_GUARDRAIL_POLICY_VERSION",
            "PA73_GUARDRAIL_POLICY_APPROVED",
        )
    }
    expected = {
        "PA73_GUARDRAIL_POLICY_AWS_REGION": REGION,
        "PA73_GUARDRAIL_POLICY_AWS_PROFILE": PROFILE,
        "PA73_GUARDRAIL_POLICY_AWS_CREDENTIAL_MODE": "profile",
        "PA73_GUARDRAIL_POLICY_VERSION": GUARDRAIL_POLICY_VERSION,
        "PA73_GUARDRAIL_POLICY_APPROVED": "1",
    }
    if any(values[name] != value for name, value in expected.items()):
        raise LiveEvidenceError("guardrail_policy_configuration_invalid")
    if (
        not _valid_guardrail_id(values["PA73_GUARDRAIL_ID"])
        or values["PA73_GUARDRAIL_VERSION"] != "DRAFT"
    ):
        raise LiveEvidenceError("guardrail_policy_configuration_invalid")
    return GuardrailPolicyConfiguration(
        values["PA73_GUARDRAIL_ID"],
        values["PA73_GUARDRAIL_VERSION"],
        values["PA73_GUARDRAIL_POLICY_VERSION"],
    )


def _policy_cases_from_fixture(
    raw: bytes,
    expected_sha256: str = POLICY_FIXTURE_SHA256,
) -> tuple[GuardrailPolicyCase, ...]:
    try:
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= _MAX_FIXTURE_BYTES:
            raise ValueError
        actual = "sha256:" + hashlib.sha256(raw).hexdigest()
        if not hmac.compare_digest(actual, expected_sha256):
            raise ValueError
        wrapper = _exact_mapping(
            json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs),
            {"synthetic_non_production_fixture", "schema_version", "cases"},
        )
        if (
            wrapper["synthetic_non_production_fixture"] is not True
            or wrapper["schema_version"] != POLICY_FIXTURE_SCHEMA_VERSION
            or not isinstance(wrapper["cases"], list)
            or len(wrapper["cases"]) != _MAX_POLICY_CASES
        ):
            raise ValueError
        cases: list[GuardrailPolicyCase] = []
        seen_ids: set[str] = set()
        base_fields = {
            "case_id",
            "source",
            "policy_category",
            "expected_action",
            "content_parts",
        }
        for raw_case in wrapper["cases"]:
            if not isinstance(raw_case, dict):
                raise TypeError
            expected_action = _text(raw_case.get("expected_action"))
            detector_fields = (
                set()
                if expected_action == "NONE"
                else {"expected_detector_kind", "expected_detector"}
            )
            item = _exact_mapping(raw_case, base_fields | detector_fields)
            content_parts = _texts(item["content_parts"])
            policy_category = _text(item["policy_category"])
            expected_detector = _POLICY_EXPECTED_DETECTORS.get(policy_category)
            case = GuardrailPolicyCase(
                _text(item["case_id"]),
                _text(item["source"]),
                policy_category,
                expected_action,
                content_parts,
                (
                    _text(item["expected_detector_kind"])
                    if detector_fields
                    else None
                ),
                _text(item["expected_detector"]) if detector_fields else None,
            )
            expected_category_action = (
                "NONE"
                if expected_detector is None
                else (
                    "ANONYMIZED"
                    if policy_category == "ordinary_pii"
                    else "GUARDRAIL_INTERVENED"
                )
            )
            if (
                not case.case_id
                or case.case_id in seen_ids
                or case.source not in _POLICY_SOURCES
                or case.policy_category not in _POLICY_CATEGORIES
                or case.expected_action not in _POLICY_ACTIONS
                or case.expected_action != expected_category_action
                or (
                    (case.expected_detector_kind, case.expected_detector)
                    != expected_detector
                    if expected_detector is not None
                    else case.expected_detector_kind is not None
                    or case.expected_detector is not None
                )
                or not 1 <= len(content_parts) <= _MAX_POLICY_CONTENT_PARTS
                or any(
                    not part
                    or len(part) > _MAX_POLICY_CONTENT_PART_SCALARS
                    or any(ord(character) < 32 for character in part)
                    for part in content_parts
                )
                or not 1 <= len(case.content) <= _MAX_POLICY_CONTENT_SCALARS
                or (
                    policy_category in _SPLIT_SECRET_CATEGORIES
                    and (
                        len(content_parts) < 2
                        or any(
                            pattern.search(part) is not None
                            for part in content_parts
                            for pattern in _COMPLETE_SECRET_PATTERNS
                        )
                    )
                )
            ):
                raise ValueError
            seen_ids.add(case.case_id)
            cases.append(case)
        if {case.policy_category for case in cases} != _POLICY_CATEGORIES:
            raise ValueError
        return tuple(cases)
    except Exception:  # noqa: BLE001 - fixture details stay private
        raise LiveEvidenceError("guardrail_policy_fixture_invalid") from None


def _assessment_entries(
    response: Mapping[object, object],
    *,
    policy_name: str,
    entry_name: str,
) -> tuple[Mapping[object, object], ...]:
    assessments = response.get("assessments")
    if not isinstance(assessments, list) or not assessments:
        raise LiveEvidenceError("guardrail_policy_result_invalid")
    entries: list[Mapping[object, object]] = []
    for assessment in assessments:
        if not isinstance(assessment, Mapping) or not assessment:
            raise LiveEvidenceError("guardrail_policy_result_invalid")
        if not set(assessment).issubset(
            {
                "appliedGuardrailDetails",
                "automatedReasoningPolicy",
                "contentPolicy",
                "contextualGroundingPolicy",
                "invocationMetrics",
                "sensitiveInformationPolicy",
                "topicPolicy",
                "wordPolicy",
            }
        ):
            raise LiveEvidenceError("guardrail_policy_result_invalid")
        policy = assessment.get(policy_name)
        if policy is None:
            continue
        if not isinstance(policy, Mapping):
            raise LiveEvidenceError("guardrail_policy_result_invalid")
        allowed_entries = (
            {"filters"}
            if policy_name == "contentPolicy"
            else {"piiEntities", "regexes"}
        )
        if not set(policy) or not set(policy).issubset(allowed_entries):
            raise LiveEvidenceError("guardrail_policy_result_invalid")
        raw_entries = policy.get(entry_name)
        if raw_entries is None:
            continue
        if not isinstance(raw_entries, list) or not raw_entries:
            raise LiveEvidenceError("guardrail_policy_result_invalid")
        for entry in raw_entries:
            if not isinstance(entry, Mapping):
                raise LiveEvidenceError("guardrail_policy_result_invalid")
            entries.append(entry)
    if not entries:
        raise LiveEvidenceError("guardrail_policy_result_invalid")
    return tuple(entries)


def _matching_content_filter(
    response: Mapping[object, object], expected_detector: str
) -> bool:
    matched = False
    for entry in _assessment_entries(
        response, policy_name="contentPolicy", entry_name="filters"
    ):
        detector = entry.get("type")
        if (
            detector not in _CONTENT_FILTER_TYPES
            or not isinstance(entry.get("confidence"), str)
            or not isinstance(entry.get("filterStrength"), str)
            or entry.get("action") != "BLOCKED"
            or entry.get("detected") is not True
        ):
            raise LiveEvidenceError("guardrail_policy_result_invalid")
        matched = matched or detector == expected_detector
    return matched


def _matching_pii_entity(
    response: Mapping[object, object], expected_detector: str, expected_action: str
) -> bool:
    matched = False
    for entry in _assessment_entries(
        response,
        policy_name="sensitiveInformationPolicy",
        entry_name="piiEntities",
    ):
        detector = entry.get("type")
        if (
            detector not in _PII_ENTITY_TYPES
            or entry.get("action") not in {"BLOCKED", "ANONYMIZED"}
            or not isinstance(entry.get("match"), str)
            or not entry.get("match")
        ):
            raise LiveEvidenceError("guardrail_policy_result_invalid")
        if detector == expected_detector:
            if entry.get("action") != expected_action:
                raise LiveEvidenceError("guardrail_policy_result_invalid")
            matched = True
    return matched


def _matching_regex(
    response: Mapping[object, object], expected_detector: str
) -> bool:
    matched = False
    for entry in _assessment_entries(
        response,
        policy_name="sensitiveInformationPolicy",
        entry_name="regexes",
    ):
        detector = entry.get("name")
        if (
            detector not in _REGEX_NAMES
            or entry.get("action") != "BLOCKED"
            or not isinstance(entry.get("regex"), str)
            or not entry.get("regex")
            or not isinstance(entry.get("match"), str)
            or not entry.get("match")
        ):
            raise LiveEvidenceError("guardrail_policy_result_invalid")
        matched = matched or detector == expected_detector
    return matched


def _policy_result_action(response: object, case: GuardrailPolicyCase) -> str:
    if not isinstance(response, Mapping):
        raise LiveEvidenceError("guardrail_policy_result_invalid")
    expected_top_level_action = (
        "NONE" if case.expected_action == "NONE" else "GUARDRAIL_INTERVENED"
    )
    if response.get("action") != expected_top_level_action:
        raise LiveEvidenceError("guardrail_policy_action_mismatch")
    if case.expected_action == "NONE":
        return "NONE"
    detector_kind = case.expected_detector_kind
    expected_detector = case.expected_detector
    if detector_kind is None or expected_detector is None:
        raise LiveEvidenceError("guardrail_policy_result_invalid")
    if detector_kind == "content_filter":
        matched = _matching_content_filter(response, expected_detector)
    elif detector_kind == "pii_entity":
        expected_assessment_action = (
            "ANONYMIZED" if case.expected_action == "ANONYMIZED" else "BLOCKED"
        )
        matched = _matching_pii_entity(
            response, expected_detector, expected_assessment_action
        )
    elif detector_kind == "regex":
        matched = _matching_regex(response, expected_detector)
    else:
        raise LiveEvidenceError("guardrail_policy_result_invalid")
    if not matched:
        raise LiveEvidenceError("guardrail_policy_detector_mismatch")
    if case.expected_action != "ANONYMIZED":
        return "GUARDRAIL_INTERVENED"
    outputs = response.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != 1:
        raise LiveEvidenceError("guardrail_policy_result_invalid")
    output = outputs[0]
    if not isinstance(output, Mapping) or set(output) != {"text"}:
        raise LiveEvidenceError("guardrail_policy_result_invalid")
    text = output.get("text")
    if not isinstance(text, str) or not text or text == case.content:
        raise LiveEvidenceError("guardrail_policy_result_invalid")
    return "ANONYMIZED"


def _policy_summary(
    *, passed: int, failed: int, executed: int, expected: int
) -> dict[str, object]:
    return {
        "schema_version": POLICY_FIXTURE_SCHEMA_VERSION,
        "fixture_sha256": POLICY_FIXTURE_SHA256,
        "passed": passed,
        "failed": failed,
        "executed": executed,
        "expected": expected,
        "policy_version": GUARDRAIL_POLICY_VERSION,
    }


def _production_guardrail_policy_client(
    configuration: GuardrailPolicyConfiguration,
    counter: GuardrailOperationCounter,
) -> GuardrailPolicyClient:
    del counter
    try:
        boto3_module = importlib.import_module("boto3")
        config_module = importlib.import_module("botocore.config")
        session_factory = boto3_module.Session  # type: ignore[attr-defined]
        config_factory = config_module.Config  # type: ignore[attr-defined]
        session = session_factory(profile_name=PROFILE)
        sdk_config = config_factory(
            connect_timeout=3.0,
            read_timeout=3.0,
            retries={"total_max_attempts": 1, "mode": "standard"},
        )
        client = session.client(
            "bedrock-runtime", region_name=REGION, config=sdk_config
        )
        if not callable(getattr(client, "apply_guardrail", None)):
            raise TypeError
    except Exception:  # noqa: BLE001 - SDK/profile/client details stay private
        raise LiveEvidenceError("guardrail_policy_sdk_unavailable") from None
    if (
        configuration.policy_version != GUARDRAIL_POLICY_VERSION
        or not configuration.guardrail_id
        or configuration.guardrail_version != "DRAFT"
    ):
        raise LiveEvidenceError("guardrail_policy_configuration_invalid")
    return cast(GuardrailPolicyClient, client)


def run_guardrail_policy_tests(
    *,
    getenv: EnvReader | None = None,
    fixture_reader: Callable[[Path], bytes] | None = None,
    client_factory: GuardrailPolicyClientFactory | None = None,
) -> dict[str, object]:
    """Run the separate, bounded ApplyGuardrail fixture sequence."""
    if getenv is None:
        def environment(name: str, default: str | None = None) -> str | None:
            return os.getenv(name, default)
    else:
        environment = getenv
    if environment("PA73_GUARDRAIL_POLICY_TEST", None) != "1":
        raise LiveEvidenceError("guardrail_policy_not_enabled")

    configuration = _policy_configuration(environment)
    reader = fixture_reader or _read_fixture
    cases = _policy_cases_from_fixture(reader(POLICY_FIXTURE_PATH))
    counter = GuardrailOperationCounter()
    factory = client_factory or _production_guardrail_policy_client
    client = factory(configuration, counter)
    if not callable(getattr(client, "apply_guardrail", None)):
        raise LiveEvidenceError("guardrail_policy_dependency_invalid")

    passed = 0
    for case in cases:
        try:
            counter.record(case.case_id)
            response = client.apply_guardrail(
                guardrailIdentifier=configuration.guardrail_id,
                guardrailVersion=configuration.guardrail_version,
                source=case.source,
                content=[{"text": {"text": case.content}}],
            )
            actual = _policy_result_action(response, case)
            if actual != case.expected_action:
                raise LiveEvidenceError("guardrail_policy_action_mismatch")
        except Exception:  # noqa: BLE001 - all provider detail is discarded
            return _policy_summary(
                passed=passed,
                failed=1,
                executed=counter.total,
                expected=len(cases),
            )
        passed += 1
    return _policy_summary(
        passed=passed,
        failed=0,
        executed=counter.total,
        expected=len(cases),
    )


def _read_fixture(path: Path) -> bytes:
    try:
        with path.open("rb") as fixture:
            raw = fixture.read(_MAX_FIXTURE_BYTES + 1)
        if len(raw) > _MAX_FIXTURE_BYTES:
            raise ValueError
        return raw
    except Exception:  # noqa: BLE001 - file-system details stay private
        raise LiveEvidenceError("live_evidence_fixture_invalid") from None


def _logging_disabled(response: object) -> bool:
    try:
        if not isinstance(response, Mapping):
            return False
        logging_config = response.get("loggingConfig")
        if not isinstance(logging_config, Mapping):
            return False
        enabled = logging_config.get("textDataDeliveryEnabled")
        return enabled is False
    except Exception:  # noqa: BLE001 - unknown response shapes fail closed
        return False


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _deadline(operation_id: str, now: datetime, budget_ms: int) -> DeadlineDTO:
    sent = _format_utc(now)
    deadline = _format_utc(now + timedelta(milliseconds=budget_ms + 5_000))
    return DeadlineDTO(SCHEMA_VERSION, operation_id, deadline, budget_ms, sent, 1_000)


def _production_boundaries(
    config: BedrockReasoningConfig,
    counter: OperationCounter,
) -> tuple[Callable[[], object], ExplicitLiveBedrockClient]:
    try:
        boto3_module = importlib.import_module("boto3")
        config_module = importlib.import_module("botocore.config")
        session_factory = boto3_module.Session  # type: ignore[attr-defined]
        config_factory = config_module.Config  # type: ignore[attr-defined]
        session = session_factory(profile_name=config.profile_name)
        counted_session = _CountingSession(session, config, counter)
        sdk_config = config_factory(
            connect_timeout=3.0,
            read_timeout=3.0,
            retries={"total_max_attempts": 1, "mode": "standard"},
        )
        logging_client = counted_session.client(
            "bedrock", region_name=config.region, config=sdk_config
        )
        logging_call = (
            logging_client.get_model_invocation_logging_configuration  # type: ignore[attr-defined]
        )
        if not callable(logging_call):
            raise TypeError
        invoker = Boto3BedrockReasoningInvoker(
            counted_session,
            config_factory,
            config,
        )
        client = ExplicitLiveBedrockClient(invoker, enabled=True)
    except Exception:  # noqa: BLE001 - dependency/profile/client details stay private
        raise LiveEvidenceError("live_evidence_sdk_unavailable") from None
    return logging_call, client


def _role_status(role: str) -> dict[str, object]:
    return {
        "role": role,
        "lifecycle": False,
        "health": False,
        "generate": False,
        "error": "not_run",
    }


def _summary(
    configuration: LiveConfiguration,
    roles: list[dict[str, object]],
    counter: OperationCounter,
    completed_at: datetime,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_sha256": configuration.fixture_sha256,
        "region": REGION,
        "guardrail_policy_version": configuration.guardrail_policy_version,
        "guardrail_numeric_version": next(
            iter(configuration.bedrock.guardrails.values())
        ).version,
        "roles": roles,
        "operation_counts": counter.snapshot(),
        "retry_count": 0,
        "completed_at_utc": _format_utc(completed_at),
    }


def _request(
    role: ModelRole,
    context: ReasoningContextDTO,
    policy: str,
    now: datetime,
) -> GenerateRequestDTO:
    operation = f"OP-PA73-LIVE-{role.upper()}-GENERATE"
    return GenerateRequestDTO(
        operation,
        "TASK-PA73-LIVE-EVIDENCE",
        "EXEC-PA73-LIVE-EVIDENCE",
        role,
        context,
        context.context_hash(),
        SCHEMA_VERSION,
        policy,
        _deadline(operation, now, 60_000),
    )


def _citations_are_bounded(
    result: ReasoningResultDTO,
    context: ReasoningContextDTO,
) -> bool:
    evidence_ids = {item.evidence_id for item in context.evidence_refs}
    analysis_ids = {item.analysis_id for item in context.analysis_refs}
    return all(
        set(fact.evidence_refs) <= evidence_ids
        and set(fact.analysis_refs) <= analysis_ids
        for fact in result.facts
    )


def run_live_evidence(
    *,
    getenv: EnvReader | None = None,
    fixture_reader: Callable[[Path], bytes] | None = None,
    logging_probe_factory: (
        Callable[[BedrockReasoningConfig, OperationCounter], Callable[[], object]]
        | None
    ) = None,
    client_factory: ReasoningClientFactory | None = None,
    now_utc: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Run the bounded sequence; all external boundaries are injectable for tests."""
    if getenv is None:
        def environment(name: str, default: str | None = None) -> str | None:
            return os.getenv(name, default)
    else:
        environment = getenv
    if environment("PA73_BEDROCK_LIVE_INTEGRATION", None) != "1":
        raise LiveEvidenceError("live_evidence_not_enabled")

    configuration = _configuration(environment)
    reader = fixture_reader or _read_fixture
    context = _context_from_fixture(
        reader(FIXTURE_PATH), configuration.fixture_sha256
    )
    if (logging_probe_factory is None) != (client_factory is None):
        raise LiveEvidenceError("live_evidence_dependency_invalid")

    counter = OperationCounter()
    if logging_probe_factory is None:
        logging_probe, client = _production_boundaries(
            configuration.bedrock, counter
        )
    else:
        assert client_factory is not None
        logging_probe = logging_probe_factory(configuration.bedrock, counter)
        client = client_factory(configuration.bedrock, counter)
        if not isinstance(client, ExplicitLiveBedrockClient):
            raise LiveEvidenceError("live_evidence_dependency_invalid")

    clock_now = now_utc or (lambda: datetime.now(UTC))
    roles = [_role_status("primary"), _role_status("fallback")]
    model_roles: tuple[ModelRole, ModelRole] = ("primary", "fallback")
    try:
        logging_response = logging_probe()
    except Exception:  # noqa: BLE001 - all external detail is discarded
        roles[0]["error"] = "logging_preflight_failed"
        return _summary(configuration, roles, counter, clock_now())
    if not _logging_disabled(logging_response):
        roles[0]["error"] = "logging_not_disabled"
        return _summary(configuration, roles, counter, clock_now())

    provider = BedrockReasoningProvider(
        client,
        clock=_EvidenceClock(clock_now),
        primary_model_version=PRIMARY_BASE_MODEL,
        fallback_model_version=FALLBACK_BASE_MODEL,
    )
    for index, role in enumerate(model_roles):
        status = roles[index]
        try:
            lifecycle = client.lifecycle_preflight(
                model_role=role,
                timeout_ms=10_000,
                cancelled=lambda: False,
            )
        except Exception:  # noqa: BLE001 - all external detail is discarded
            status["error"] = "lifecycle_failed"
            return _summary(configuration, roles, counter, clock_now())
        if lifecycle is not True:
            status["error"] = "lifecycle_failed"
            return _summary(configuration, roles, counter, clock_now())
        status["lifecycle"] = True

        health_operation = f"OP-PA73-LIVE-{role.upper()}-HEALTH"
        health_request = ReasoningHealthCheckRequestDTO(
            health_operation,
            role,
            _deadline(health_operation, clock_now(), 3_000),
        )
        health = provider.health_check(health_request)
        if not isinstance(health, ProviderHealthDTO) or health.status != "healthy":
            status["error"] = "health_failed"
            return _summary(configuration, roles, counter, clock_now())
        status["health"] = True

        result = provider.generate(
            _request(role, context, configuration.guardrail_policy_version, clock_now())
        )
        if (
            isinstance(result, ErrorResultDTO)
            or not isinstance(result, ReasoningResultDTO)
            or not provider.is_publishable(result)
            or result.provider.model_role != role
            or not _citations_are_bounded(result, context)
        ):
            status["error"] = "generate_failed"
            return _summary(configuration, roles, counter, clock_now())
        status["generate"] = True
        status["error"] = None

    return _summary(configuration, roles, counter, clock_now())


def main() -> int:
    try:
        summary = run_live_evidence()
        role_summaries = cast(list[dict[str, object]], summary["roles"])
        succeeded = all(
            role["lifecycle"] and role["health"] and role["generate"]
            for role in role_summaries
        )
    except LiveEvidenceError as error:
        summary = {
            "schema_version": SCHEMA_VERSION,
            "error": str(error),
            "retry_count": 0,
        }
        succeeded = False
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
