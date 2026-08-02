"""Nova 2 Lite EvidenceExtractor adapter bound to the Core 1.0.0 Port."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from threading import RLock
import time
from typing import Protocol

from crypto_trust_agent.application.dto.common import (
    DeadlineExceededError,
    ErrorResultDTO,
    PortErrorCategory,
    PortErrorDTO,
    build_local_deadline,
    map_unexpected_exception,
)
from crypto_trust_agent.application.dto.evidence_extractor import (
    EXTRACT_ERROR_CODES,
    REPAIR_ERROR_CODES,
    ExtractRequestDTO,
    ExtractedClaimDTO,
    ExtractionProviderDTO,
    ExtractionResultDTO,
    ExtractorHealthCheckRequestDTO,
    InlineContentInputDTO,
    RepairRequestDTO,
    UsageDTO,
    ValidationErrorDTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO

CONTRACT_VERSION = "1.0.0"
PROVIDER_VERSION = "nova2-lite-extractor-adapter-1.0.0"
DEFAULT_MODEL_VERSION = "nova-2-lite"
_FORBIDDEN_PROVIDER_FIELDS = frozenset({
    "task_id", "execution_id", "raw_record_id", "evidence_id", "link_id",
    "assessment_id", "fetched_at", "published_at", "raw_locator",
    "content_hash", "clean_content_hash", "operation_id", "started_at", "finished_at",
})
_CLAIM_FIELDS = frozenset({"text", "quote", "related_assets", "event_type", "sentiment", "relevance"})
_ROOT_FIELDS = frozenset({"claims", "validation_errors", "usage", "invocation_id"})


class NovaClient(Protocol):
    non_production: bool

    def invoke(self, *, operation_id: str, payload: Mapping[str, object], timeout_ms: int,
               cancelled: Callable[[], bool]) -> object: ...

    def probe(self, *, timeout_ms: int, cancelled: Callable[[], bool]) -> bool: ...


class ProviderFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class StubNovaClient:
    """Deterministic no-network default used unless live wiring is explicit."""

    non_production = True

    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], object] = {}
        self.calls: list[dict[str, object]] = []

    def configure(self, mode: str, operation_id: str, response: object) -> None:
        self.responses[(mode, operation_id)] = response

    def invoke(self, *, operation_id: str, payload: Mapping[str, object], timeout_ms: int,
               cancelled: Callable[[], bool]) -> object:
        if cancelled():
            raise ProviderFailure("extractor_timeout", retryable=True)
        copied = deepcopy(dict(payload))
        self.calls.append({"operation_id": operation_id, "payload": copied, "timeout_ms": timeout_ms})
        mode = str(payload["mode"])
        configured = self.responses.get((mode, operation_id))
        if configured is not None:
            if isinstance(configured, BaseException):
                raise configured
            if isinstance(configured, str):
                raise ProviderFailure(
                    configured,
                    retryable=configured in {"extractor_timeout", "extractor_rate_limited", "extractor_unavailable"},
                )
            return configured
        if mode == "repair":
            return {
                "claims": [],
                "validation_errors": [{"path": "/claims", "code": "repair_not_configured", "safe_message": "Repair did not produce a valid claim."}],
                "usage": {"input_units": None, "output_units": None},
                "invocation_id": f"STUB-{operation_id[3:]}",
            }
        content = payload["content"]
        assert isinstance(content, Mapping)
        quote = str(content.get("clean_content", "Content available through validated locator."))[:4096]
        return {
            "claims": [{
                "text": "Deterministic non-production extracted claim.", "quote": quote,
                "related_assets": [payload["assets"][0]], "event_type": payload["allowed_event_taxonomy"][0],
                "sentiment": "neutral", "relevance": "high",
            }],
            "validation_errors": [], "usage": {"input_units": None, "output_units": None},
            "invocation_id": f"STUB-{operation_id[3:]}",
        }

    def probe(self, *, timeout_ms: int, cancelled: Callable[[], bool]) -> bool:
        return timeout_ms > 0 and not cancelled()


class NullEventSink:
    def emit(self, event: Mapping[str, object]) -> None:
        del event


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _category(code: str) -> PortErrorCategory:
    if code in {"extractor_timeout", "deadline_exceeded"}:
        return PortErrorCategory.TIMEOUT
    if code == "extractor_rate_limited":
        return PortErrorCategory.RATE_LIMITED
    if code == "extractor_unavailable":
        return PortErrorCategory.UNAVAILABLE
    if code == "raw_record_not_safe":
        return PortErrorCategory.UNSAFE_SOURCE
    if code == "invalid_extraction_schema":
        return PortErrorCategory.INVALID_PROVIDER_OUTPUT
    if code == "unexpected_provider_error":
        return PortErrorCategory.UNEXPECTED
    return PortErrorCategory.VALIDATION


def _contains_forbidden_field(value: object) -> bool:
    if isinstance(value, Mapping):
        return bool(_FORBIDDEN_PROVIDER_FIELDS.intersection(value)) or any(_contains_forbidden_field(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_field(item) for item in value)
    return False


def _safe_validation_errors(value: object) -> tuple[ValidationErrorDTO, ...]:
    """Validate provider shape, then replace all provider-authored diagnostics locally."""
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("invalid validation errors")
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"path", "code", "safe_message"}:
            raise ValueError("invalid validation error")
        if not all(isinstance(item[field], str) for field in ("path", "code", "safe_message")):
            raise ValueError("validation error values must be strings")
    if not value:
        return ()
    return (
        ValidationErrorDTO(
            "/claims", "invalid_provider_output",
            "Provider output did not satisfy extraction validation.",
        ),
    )


class NovaLiteEvidenceExtractor:
    """Strict mapper; Core remains the sole owner of orchestration and repair choice."""

    contract_version = CONTRACT_VERSION
    provider_version = PROVIDER_VERSION
    hidden_retries = 0
    max_attempts = 1

    def __init__(
        self, client: NovaClient | None = None, *, model_version: str = DEFAULT_MODEL_VERSION,
        now_utc: Callable[[], datetime] | None = None, monotonic_ms: Callable[[], int] | None = None,
        runtime_id: str = "nova-extractor-runtime", cancelled: Callable[[], bool] | None = None,
        event_sink: object | None = None,
    ) -> None:
        self._client = client or StubNovaClient()
        self.non_production = bool(getattr(self._client, "non_production", False))
        self.model_version = model_version
        self._now = now_utc or (lambda: datetime.now(UTC))
        self._monotonic_ms = monotonic_ms or (lambda: time.monotonic_ns() // 1_000_000)
        self._runtime_id = runtime_id
        self._cancelled = cancelled or (lambda: False)
        self._events = event_sink or NullEventSink()
        self._extract_replay: dict[str, tuple[ExtractRequestDTO, ExtractionResultDTO | ErrorResultDTO]] = {}
        self._repair_replay: dict[str, tuple[RepairRequestDTO, ExtractionResultDTO | ErrorResultDTO]] = {}
        self._repair_identity: dict[tuple[str, str, str], str] = {}
        self._lock = RLock()
        self.invocation_count = 0

    def configure_extract(self, operation_id: str, response: object) -> None:
        if not isinstance(self._client, StubNovaClient):
            raise RuntimeError("configuration is available only for the non-production stub")
        self._client.configure("extract", operation_id, response)

    def configure_repair(self, operation_id: str, response: object) -> None:
        if not isinstance(self._client, StubNovaClient):
            raise RuntimeError("configuration is available only for the non-production stub")
        self._client.configure("repair", operation_id, response)

    def _deadline(self, request: object, provider_timeout_ms: int) -> int:
        return build_local_deadline(
            request.deadline, provider_timeout_ms=provider_timeout_ms, now_utc=self._now(),
            now_monotonic_ms=self._monotonic_ms(), runtime_id=self._runtime_id,
        ).effective_timeout_ms

    def _error(self, operation_id: str, code: str, *, retryable: bool = False) -> ErrorResultDTO:
        return ErrorResultDTO(PortErrorDTO(
            schema_version=CONTRACT_VERSION, code=code, category=_category(code), retryable=retryable,
            safe_message="Evidence extraction request failed.", provider="nova_lite",
            operation_id=operation_id, details={}, occurred_at=_utc_text(self._now()),
        ))

    def _payload(self, request: ExtractRequestDTO | RepairRequestDTO, mode: str) -> dict[str, object]:
        base: dict[str, object] = {
            "schema_version": CONTRACT_VERSION, "mode": mode,
            "output_schema_version": request.output_schema_version,
            "guardrail_policy_version": request.guardrail_policy_version,
            "constraints": {
                "max_claims": 200, "max_claim_text_scalars": 2000, "max_quote_scalars": 4096,
                "allowed_sentiment": ["negative", "neutral", "positive", "mixed"],
                "allowed_relevance": ["low", "medium", "high"],
                "forbidden_system_fields": sorted(_FORBIDDEN_PROVIDER_FIELDS),
            },
        }
        if isinstance(request, ExtractRequestDTO):
            base.update({
                "content": request.content.to_wire(), "assets": list(request.assets),
                "allowed_event_taxonomy": list(request.allowed_event_taxonomy),
            })
        else:
            base.update({
                "original_claims": [{
                    "text": claim.text, "quote": claim.quote, "related_assets": list(claim.related_assets),
                    "event_type": claim.event_type, "sentiment": claim.sentiment, "relevance": claim.relevance,
                } for claim in request.original_result.claims],
                "validator_errors": [item.to_wire() for item in request.validator_errors],
            })
        encoded = json.dumps(base, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 1_100_000:
            raise ProviderFailure("input_too_large")
        return base

    def _map_result(self, request: ExtractRequestDTO | RepairRequestDTO, raw: object,
                    started_at: datetime, *, repair: bool) -> ExtractionResultDTO:
        if isinstance(raw, ExtractionResultDTO) and self.non_production:
            return raw
        if not isinstance(raw, Mapping) or set(raw) != _ROOT_FIELDS or _contains_forbidden_field(raw):
            raise ValueError("provider output shape is invalid")
        if not isinstance(raw["invocation_id"], str):
            raise ValueError("provider invocation id is invalid")
        claims_value = raw["claims"]
        if not isinstance(claims_value, list) or len(claims_value) > 200:
            raise ValueError("provider claims are invalid")
        assets: set[str] = set()
        taxonomy: set[str] = set()
        if isinstance(request, ExtractRequestDTO):
            assets, taxonomy = set(request.assets), set(request.allowed_event_taxonomy)
        claims: list[ExtractedClaimDTO] = []
        for index, item in enumerate(claims_value):
            if not isinstance(item, Mapping) or set(item) != _CLAIM_FIELDS:
                raise ValueError("provider claim shape is invalid")
            if not all(isinstance(item[field], str) for field in ("text", "quote", "event_type", "sentiment", "relevance")):
                raise ValueError("provider claim text fields are invalid")
            related = tuple(item["related_assets"]) if isinstance(item["related_assets"], list) else ()
            if not related or not all(isinstance(asset, str) for asset in related):
                raise ValueError("provider related assets are invalid")
            event_type = item["event_type"]
            if assets and not set(related).issubset(assets):
                raise ValueError("provider asset is outside approved scope")
            if taxonomy and event_type not in taxonomy:
                raise ValueError("provider event type is outside approved scope")
            quote = item["quote"]
            if isinstance(request, ExtractRequestDTO) and isinstance(request.content, InlineContentInputDTO) and quote not in request.content.clean_content:
                raise ValueError("provider quote is not grounded in cleaned content")
            claim_digest = hashlib.sha256(f"{request.operation_id}:{index}".encode()).hexdigest()[:24].upper()
            claims.append(ExtractedClaimDTO(
                f"XCL-{claim_digest}", item["text"], quote, related, event_type,
                item["sentiment"], item["relevance"],
            ))
        errors = _safe_validation_errors(raw["validation_errors"])
        usage_value = raw["usage"]
        if not isinstance(usage_value, Mapping) or set(usage_value) != {"input_units", "output_units"}:
            raise ValueError("provider usage is invalid")
        usage = UsageDTO(usage_value["input_units"], usage_value["output_units"])
        outcome = "valid" if claims and not errors else ("quarantined" if repair else "invalid")
        if outcome != "valid" and not errors:
            errors = (ValidationErrorDTO("/claims", "invalid_provider_output", "Provider output did not contain valid claims."),)
        return ExtractionResultDTO(
            outcome, request.raw_record_id, ExtractionProviderDTO("nova_lite", self.model_version, raw["invocation_id"]),
            tuple(claims) if outcome == "valid" else (), errors, usage,
            _utc_text(started_at), _utc_text(self._now()),
        )

    @staticmethod
    def _valid_semantics(request: ExtractRequestDTO | RepairRequestDTO, result: ExtractionResultDTO) -> bool:
        if result.raw_record_id != request.raw_record_id:
            return False
        if result.outcome == "valid" and (not result.claims or result.validation_errors):
            return False
        if result.outcome != "valid" and not result.validation_errors:
            return False
        if isinstance(request, ExtractRequestDTO):
            return all(
                set(claim.related_assets).issubset(request.assets)
                and claim.event_type in request.allowed_event_taxonomy
                for claim in result.claims
            )
        return True

    def _quarantine(self, request: RepairRequestDTO, started_at: datetime, code: str = "invalid_provider_output") -> ExtractionResultDTO:
        return ExtractionResultDTO(
            "quarantined", request.raw_record_id,
            ExtractionProviderDTO("nova_lite", self.model_version, f"QUAR-{request.operation_id[3:]}"), (),
            (ValidationErrorDTO("/claims", code, "Repair output remained invalid and was quarantined."),),
            UsageDTO(None, None), _utc_text(started_at), _utc_text(self._now()),
        )

    def _emit(self, request: object, status: str, started_ms: int, code: str | None) -> None:
        event = {
            "schema_version": CONTRACT_VERSION, "step": "extract_evidence",
            "adapter": "nova_lite", "status": status,
            "duration_ms": max(0, self._monotonic_ms() - started_ms), "retry_count": 0,
            "operation_id": request.operation_id,
            "safe_parameters": {"schema_version": CONTRACT_VERSION, "model_version": self.model_version},
            "result_summary": {"error_code": code},
        }
        try:
            self._events.emit(event)
        except Exception:
            return

    def _invoke(self, request: ExtractRequestDTO | RepairRequestDTO, *, repair: bool) -> ExtractionResultDTO | ErrorResultDTO:
        started_at = self._now()
        started_ms = self._monotonic_ms()
        mode = "repair" if repair else "extract"
        timeout_limit = 20_000 if repair else 60_000
        allowed_codes = REPAIR_ERROR_CODES if repair else EXTRACT_ERROR_CODES
        try:
            timeout_ms = self._deadline(request, timeout_limit)
            if self._cancelled():
                raise ProviderFailure("extractor_timeout", retryable=True)
            payload = self._payload(request, mode)
            self.invocation_count += 1
            raw = self._client.invoke(
                operation_id=request.operation_id, payload=payload, timeout_ms=timeout_ms,
                cancelled=self._cancelled,
            )
            if self._cancelled():
                raise ProviderFailure("extractor_timeout", retryable=True)
            result = self._map_result(request, raw, started_at, repair=repair)
            # RepairRequestDTO 1.0.0 carries no authoritative content, asset set,
            # or taxonomy. Provider-authored repair claims therefore cannot be
            # grounded at this boundary and must never be promoted to valid.
            if repair and (result.claims or result.outcome == "valid"):
                quarantine = self._quarantine(request, started_at, "repair_scope_unavailable")
                self._emit(request, "quarantined", started_ms, "repair_scope_unavailable")
                return quarantine
            if not self._valid_semantics(request, result):
                raise ValueError("provider result violates extraction semantics")
            self._emit(request, result.outcome, started_ms, None)
            return result
        except DeadlineExceededError:
            result = self._error(request.operation_id, "deadline_exceeded")
        except TimeoutError:
            result = self._error(request.operation_id, "extractor_timeout", retryable=True)
        except ProviderFailure as failure:
            code = failure.code if failure.code in allowed_codes else "unexpected_provider_error"
            result = self._error(request.operation_id, code, retryable=failure.retryable)
        except (TypeError, ValueError):
            if repair:
                quarantine = self._quarantine(request, started_at)
                self._emit(request, "quarantined", started_ms, "invalid_extraction_schema")
                return quarantine
            result = self._error(request.operation_id, "invalid_extraction_schema")
        except BaseException as exception:
            result = map_unexpected_exception(
                exception, provider="nova_lite", operation_id=request.operation_id, occurred_at=self._now(),
            ).as_result()
        self._emit(request, "failed", started_ms, result.error.code)
        return result

    def extract(self, request: ExtractRequestDTO) -> ExtractionResultDTO | ErrorResultDTO:
        with self._lock:
            completed = self._extract_replay.get(request.operation_id)
            if completed is not None:
                return completed[1] if completed[0] == request else self._error(request.operation_id, "invalid_extraction_schema")
            result = self._invoke(request, repair=False)
            self._extract_replay[request.operation_id] = (request, result)
            return result

    def repair(self, request: RepairRequestDTO) -> ExtractionResultDTO | ErrorResultDTO:
        with self._lock:
            completed = self._repair_replay.get(request.operation_id)
            if completed is not None:
                return completed[1] if completed[0] == request else self._error(request.operation_id, "invalid_extraction_schema")
            identity = (request.raw_record_id, request.raw_content_hash, request.context_hash)
            prior = self._repair_identity.get(identity)
            if prior is not None and prior != request.operation_id:
                return self._error(request.operation_id, "invalid_extraction_schema")
            self._repair_identity[identity] = request.operation_id
            result = self._invoke(request, repair=True)
            self._repair_replay[request.operation_id] = (request, result)
            return result

    def health_check(self, request: ExtractorHealthCheckRequestDTO) -> ProviderHealthDTO | ErrorResultDTO:
        started = self._monotonic_ms()
        try:
            timeout_ms = self._deadline(request, 3_000)
            healthy = self._client.probe(timeout_ms=timeout_ms, cancelled=self._cancelled)
            if self._cancelled():
                raise ProviderFailure("extractor_timeout", retryable=True)
            return ProviderHealthDTO(
                provider="nova_lite", capability="evidence_extraction",
                status="healthy" if healthy else "unhealthy", checked_at=_utc_text(self._now()),
                latency_ms=min(30_000, max(0, self._monotonic_ms() - started)),
                safe_reason_code=None if healthy else "extractor_unavailable", expires_at=None,
            )
        except DeadlineExceededError:
            return self._error(request.operation_id, "deadline_exceeded")
        except TimeoutError:
            return self._error(request.operation_id, "extractor_timeout", retryable=True)
        except ProviderFailure as failure:
            allowed = {
                "extractor_timeout", "extractor_unavailable",
                "deadline_exceeded", "unexpected_provider_error",
            }
            code = failure.code if failure.code in allowed else "unexpected_provider_error"
            return self._error(request.operation_id, code, retryable=failure.retryable)
        except BaseException as exception:
            return map_unexpected_exception(
                exception, provider="nova_lite", operation_id=request.operation_id, occurred_at=self._now(),
            ).as_result()


__all__ = (
    "CONTRACT_VERSION", "DEFAULT_MODEL_VERSION", "NovaClient", "NovaLiteEvidenceExtractor",
    "NullEventSink", "PROVIDER_VERSION", "ProviderFailure", "StubNovaClient",
)
