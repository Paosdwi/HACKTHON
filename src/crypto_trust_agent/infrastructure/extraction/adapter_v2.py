"""Nova 2 Lite EvidenceExtractor adapter bound to Core contract 2.0.0."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import hashlib
import json
from threading import Event, RLock
import time
from typing import Protocol

from crypto_trust_agent.application.dto.common import (
    DeadlineExceededError,
    ErrorResultDTO,
    LocalDeadline,
    PortErrorCategory,
    PortErrorDTO,
    build_local_deadline,
    map_unexpected_exception,
)
from crypto_trust_agent.application.dto.evidence_extractor import (
    ExtractedClaimDTO,
    ExtractionProviderDTO,
    ExtractionResultDTO,
    ExtractorHealthCheckRequestDTO,
    ExtractRequestDTO,
    InlineContentInputDTO,
    LocatorContentInputDTO,
    UsageDTO,
)
from crypto_trust_agent.application.dto.evidence_extractor_v2 import (
    REPAIR_AUTHORIZATION_RULESET_VERSION,
    REPAIR_CONTENT_MAX_UTF8_BYTES,
    REPAIR_V2_ERROR_CODES,
    RepairAuthorizationInputDTO,
    RepairRequestV2DTO,
    build_repair_authorization_hash,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO
from crypto_trust_agent.infrastructure.extraction.adapter import (
    NovaClient,
    NovaLiteEvidenceExtractor,
    NullEventSink,
    ProviderFailure,
)

CONTRACT_VERSION_V2 = "2.0.0"
PROVIDER_VERSION_V2 = "nova2-lite-extractor-adapter-2.0.0"
DEFAULT_MODEL_VERSION_V2 = "nova-2-lite"

_FORBIDDEN_PROVIDER_FIELDS = frozenset({
    "task_id",
    "execution_id",
    "raw_record_id",
    "evidence_id",
    "link_id",
    "assessment_id",
    "fetched_at",
    "published_at",
    "raw_locator",
    "content_hash",
    "clean_content_hash",
    "operation_id",
    "started_at",
    "finished_at",
    "repair_authorization_hash",
})
_CLAIM_FIELDS = frozenset({
    "text",
    "quote",
    "related_assets",
    "event_type",
    "sentiment",
    "relevance",
})
_ROOT_FIELDS = frozenset({
    "claims",
    "validation_errors",
    "usage",
    "invocation_id",
})


class AuthoritativeContentResolver(Protocol):
    """Resolve an immutable opaque locator without granting arbitrary URL access."""

    non_production: bool

    def resolve(
        self,
        *,
        locator: str,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> str: ...


class ProviderOutputError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _contains_forbidden_field(value: object) -> bool:
    if isinstance(value, Mapping):
        return bool(_FORBIDDEN_PROVIDER_FIELDS.intersection(value)) or any(
            _contains_forbidden_field(item) for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_field(item) for item in value)
    return False


def _category(code: str) -> PortErrorCategory:
    if code in {"extractor_timeout", "deadline_exceeded"}:
        return PortErrorCategory.TIMEOUT
    if code == "extractor_rate_limited":
        return PortErrorCategory.RATE_LIMITED
    if code in {"extractor_unavailable", "repair_content_unavailable"}:
        return PortErrorCategory.UNAVAILABLE
    if code == "repair_content_hash_mismatch":
        return PortErrorCategory.INTEGRITY
    if code in {
        "invalid_extraction_schema",
        "repair_asset_scope_violation",
        "repair_event_taxonomy_violation",
    }:
        return PortErrorCategory.INVALID_PROVIDER_OUTPUT
    if code == "unexpected_provider_error":
        return PortErrorCategory.UNEXPECTED
    return PortErrorCategory.VALIDATION


class NovaLiteEvidenceExtractorV2:
    """Authoritative v2 repair binding with v1-compatible extract and health."""

    contract_version = CONTRACT_VERSION_V2
    supported_contract_versions = (CONTRACT_VERSION_V2,)
    provider_version = PROVIDER_VERSION_V2
    hidden_retries = 0
    max_attempts = 1

    def __init__(
        self,
        client: NovaClient,
        *,
        content_resolver: AuthoritativeContentResolver,
        model_version: str = DEFAULT_MODEL_VERSION_V2,
        now_utc: Callable[[], datetime] | None = None,
        monotonic_ms: Callable[[], int] | None = None,
        runtime_id: str = "nova-extractor-v2-runtime",
        cancelled: Callable[[], bool] | None = None,
        event_sink: object | None = None,
    ) -> None:
        if client is None:
            raise ValueError("Nova client must be explicitly configured")
        if content_resolver is None:
            raise ValueError("authoritative content resolver must be configured")
        self._client = client
        self._resolver = content_resolver
        self._model_version = model_version
        self._now = now_utc or (lambda: datetime.now(UTC))
        self._monotonic_ms = monotonic_ms or (
            lambda: time.monotonic_ns() // 1_000_000
        )
        self._runtime_id = runtime_id
        self._cancelled = cancelled or (lambda: False)
        self._events = event_sink or NullEventSink()
        self._v1 = NovaLiteEvidenceExtractor(
            client,
            model_version=model_version,
            now_utc=self._now,
            monotonic_ms=self._monotonic_ms,
            runtime_id=runtime_id,
            cancelled=self._cancelled,
            event_sink=self._events,
        )
        self.non_production = bool(
            getattr(client, "non_production", False)
            or getattr(content_resolver, "non_production", False)
        )
        self._completed_repairs: dict[
            tuple[str, str], ExtractionResultDTO | ErrorResultDTO
        ] = {}
        self._operation_authorizations: dict[str, str] = {}
        self._inflight_repairs: dict[tuple[str, str], Event] = {}
        self._lock = RLock()
        self.provider_invocation_count = 0
        self.locator_resolution_count = 0

    def _error(
        self,
        operation_id: str,
        code: str,
        *,
        category: PortErrorCategory | None = None,
        details: dict[str, str | int | bool | None] | None = None,
    ) -> ErrorResultDTO:
        retryable = code in {
            "extractor_rate_limited",
            "extractor_timeout",
            "extractor_unavailable",
        }
        return ErrorResultDTO(
            PortErrorDTO(
                schema_version="1.0.0",
                code=code,
                category=category or _category(code),
                retryable=retryable,
                safe_message="Evidence repair request failed.",
                provider="nova_lite",
                operation_id=operation_id,
                details=details or {},
                occurred_at=_utc_text(self._now()),
            )
        )

    def _conflict(self, operation_id: str) -> ErrorResultDTO:
        return self._error(
            operation_id,
            "invalid_extraction_schema",
            category=PortErrorCategory.CONFLICT,
            details={"reason_code": "payload_conflict"},
        )

    def _expected_authorization_hash(self, request: RepairRequestV2DTO) -> str:
        return build_repair_authorization_hash(
            RepairAuthorizationInputDTO(
                authorization_ruleset_version=(
                    REPAIR_AUTHORIZATION_RULESET_VERSION
                ),
                raw_record_id=request.raw_record_id,
                raw_content_hash=request.raw_content_hash,
                context_hash=request.context_hash,
                clean_content_hash=request.clean_content_hash,
                assets=request.assets,
                allowed_event_taxonomy=request.allowed_event_taxonomy,
                output_schema_version=request.output_schema_version,
                guardrail_policy_version=request.guardrail_policy_version,
            )
        )

    def _local_deadline(
        self,
        request: RepairRequestV2DTO,
        *,
        entered_at: datetime,
        entered_monotonic_ms: int,
    ) -> LocalDeadline | ErrorResultDTO:
        try:
            return build_local_deadline(
                request.deadline,
                provider_timeout_ms=20_000,
                now_utc=entered_at,
                now_monotonic_ms=entered_monotonic_ms,
                runtime_id=self._runtime_id,
            )
        except DeadlineExceededError:
            return self._error(request.operation_id, "deadline_exceeded")

    def _remaining_ms(self, local_deadline: LocalDeadline) -> int:
        return max(
            0,
            local_deadline.deadline_monotonic_ms - self._monotonic_ms(),
        )

    def _deadline_error(
        self,
        local_deadline: LocalDeadline,
        operation_id: str,
    ) -> ErrorResultDTO | None:
        if self._remaining_ms(local_deadline) <= 0:
            return self._error(operation_id, "deadline_exceeded")
        return None

    def _resolve_content(
        self,
        request: RepairRequestV2DTO,
        local_deadline: LocalDeadline,
    ) -> str | ErrorResultDTO:
        if isinstance(request.content, InlineContentInputDTO):
            return request.content.clean_content
        if not isinstance(request.content, LocatorContentInputDTO):
            return self._error(
                request.operation_id, "invalid_extraction_schema"
            )

        remaining_ms = self._remaining_ms(local_deadline)
        if remaining_ms <= 0:
            return self._error(request.operation_id, "deadline_exceeded")
        with self._lock:
            self.locator_resolution_count += 1
        try:
            content = self._resolver.resolve(
                locator=request.content.locator,
                timeout_ms=remaining_ms,
                cancelled=self._cancelled,
            )
        except TimeoutError:
            return self._error(request.operation_id, "deadline_exceeded")
        except BaseException:
            return self._error(
                request.operation_id, "repair_content_unavailable"
            )
        expired = self._deadline_error(local_deadline, request.operation_id)
        if expired is not None:
            return expired
        if not isinstance(content, str) or not content:
            return self._error(
                request.operation_id, "repair_content_unavailable"
            )
        return content

    def _provider_payload(
        self,
        request: RepairRequestV2DTO,
        clean_content: str,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": CONTRACT_VERSION_V2,
            "mode": "repair",
            "output_schema_version": request.output_schema_version,
            "guardrail_policy_version": request.guardrail_policy_version,
            "authoritative_content": clean_content,
            "assets": list(request.assets),
            "allowed_event_taxonomy": list(
                request.allowed_event_taxonomy
            ),
            "validation_feedback": [
                {
                    "path": item.path,
                    "code": item.code,
                    "safe_message": item.safe_message,
                }
                for item in request.validator_errors
            ],
            "constraints": {
                "max_claims": 200,
                "max_claim_text_scalars": 2_000,
                "max_quote_scalars": 4_096,
                "allowed_sentiment": [
                    "negative",
                    "neutral",
                    "positive",
                    "mixed",
                ],
                "allowed_relevance": ["low", "medium", "high"],
                "forbidden_system_fields": sorted(
                    _FORBIDDEN_PROVIDER_FIELDS
                ),
            },
        }
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return payload

    def _map_provider_result(
        self,
        request: RepairRequestV2DTO,
        clean_content: str,
        raw: object,
        started_at: datetime,
    ) -> ExtractionResultDTO | ErrorResultDTO:
        if isinstance(raw, ErrorResultDTO) and self.non_production:
            code = raw.error.code
            if code in REPAIR_V2_ERROR_CODES:
                return self._error(request.operation_id, code)
            return self._error(
                request.operation_id, "unexpected_provider_error"
            )
        if isinstance(raw, ExtractionResultDTO) and self.non_production:
            return raw
        if (
            not isinstance(raw, Mapping)
            or set(raw) != _ROOT_FIELDS
            or _contains_forbidden_field(raw)
        ):
            raise ProviderOutputError("invalid_extraction_schema")
        if not isinstance(raw["invocation_id"], str):
            raise ProviderOutputError("invalid_extraction_schema")

        provider_errors = raw["validation_errors"]
        if not isinstance(provider_errors, list) or len(provider_errors) > 100:
            raise ProviderOutputError("invalid_extraction_schema")
        for item in provider_errors:
            if (
                not isinstance(item, Mapping)
                or set(item) != {"path", "code", "safe_message"}
                or not all(
                    isinstance(item[field], str)
                    for field in ("path", "code", "safe_message")
                )
            ):
                raise ProviderOutputError("invalid_extraction_schema")
        if provider_errors:
            raise ProviderOutputError("invalid_extraction_schema")

        raw_claims = raw["claims"]
        if not isinstance(raw_claims, list) or not 1 <= len(raw_claims) <= 200:
            raise ProviderOutputError("invalid_extraction_schema")
        claims: list[ExtractedClaimDTO] = []
        for index, item in enumerate(raw_claims):
            if not isinstance(item, Mapping) or set(item) != _CLAIM_FIELDS:
                raise ProviderOutputError("invalid_extraction_schema")
            if not all(
                isinstance(item[field], str)
                for field in (
                    "text",
                    "quote",
                    "event_type",
                    "sentiment",
                    "relevance",
                )
            ):
                raise ProviderOutputError("invalid_extraction_schema")
            related = item["related_assets"]
            if (
                not isinstance(related, list)
                or not related
                or not all(isinstance(asset, str) for asset in related)
            ):
                raise ProviderOutputError("invalid_extraction_schema")
            if not set(related).issubset(request.assets):
                raise ProviderOutputError(
                    "repair_asset_scope_violation"
                )
            if item["event_type"] not in request.allowed_event_taxonomy:
                raise ProviderOutputError(
                    "repair_event_taxonomy_violation"
                )
            if item["quote"] not in clean_content:
                raise ProviderOutputError("invalid_extraction_schema")
            claim_digest = hashlib.sha256(
                f"{request.operation_id}:{index}".encode("utf-8")
            ).hexdigest()[:24].upper()
            claims.append(
                ExtractedClaimDTO(
                    f"XCL-{claim_digest}",
                    item["text"],
                    item["quote"],
                    tuple(related),
                    item["event_type"],
                    item["sentiment"],
                    item["relevance"],
                )
            )

        usage_value = raw["usage"]
        if (
            not isinstance(usage_value, Mapping)
            or set(usage_value) != {"input_units", "output_units"}
        ):
            raise ProviderOutputError("invalid_extraction_schema")
        usage = UsageDTO(
            usage_value["input_units"],
            usage_value["output_units"],
        )
        return ExtractionResultDTO(
            outcome="valid",
            raw_record_id=request.raw_record_id,
            provider=ExtractionProviderDTO(
                "nova_lite",
                self._model_version,
                raw["invocation_id"],
            ),
            claims=tuple(claims),
            validation_errors=(),
            usage=usage,
            started_at=_utc_text(started_at),
            finished_at=_utc_text(self._now()),
        )

    def _validate_result(
        self,
        request: RepairRequestV2DTO,
        clean_content: str,
        result: ExtractionResultDTO | ErrorResultDTO,
    ) -> ExtractionResultDTO | ErrorResultDTO:
        if isinstance(result, ErrorResultDTO):
            return result
        if (
            result.raw_record_id != request.raw_record_id
            or result.outcome != "valid"
            or not result.claims
            or result.validation_errors
        ):
            return self._error(
                request.operation_id, "invalid_extraction_schema"
            )
        for claim in result.claims:
            if not set(claim.related_assets).issubset(request.assets):
                return self._error(
                    request.operation_id,
                    "repair_asset_scope_violation",
                )
            if claim.event_type not in request.allowed_event_taxonomy:
                return self._error(
                    request.operation_id,
                    "repair_event_taxonomy_violation",
                )
            if claim.quote not in clean_content:
                return self._error(
                    request.operation_id, "invalid_extraction_schema"
                )
        return result

    def _emit(
        self,
        request: RepairRequestV2DTO,
        result: ExtractionResultDTO | ErrorResultDTO,
        started_ms: int,
    ) -> None:
        code = result.error.code if isinstance(result, ErrorResultDTO) else None
        event = {
            "schema_version": "1.0.0",
            "step": "extract_evidence",
            "adapter": "nova_lite",
            "status": "failed" if code else "valid",
            "duration_ms": max(0, self._monotonic_ms() - started_ms),
            "retry_count": 0,
            "operation_id": request.operation_id,
            "safe_parameters": {
                "contract_version": CONTRACT_VERSION_V2,
                "model_version": self._model_version,
            },
            "result_summary": {"error_code": code},
        }
        try:
            self._events.emit(event)
        except Exception:
            return

    def _complete(
        self,
        identity: tuple[str, str],
        request: RepairRequestV2DTO,
        result: ExtractionResultDTO | ErrorResultDTO,
        started_ms: int,
    ) -> ExtractionResultDTO | ErrorResultDTO:
        should_emit = False
        with self._lock:
            completed = self._completed_repairs.get(identity)
            if completed is None:
                self._completed_repairs[identity] = result
                completed = result
                should_emit = True
            inflight = self._inflight_repairs.pop(identity, None)
            if inflight is not None:
                inflight.set()
        if should_emit:
            self._emit(request, completed, started_ms)
        return completed

    def _await_inflight(
        self,
        identity: tuple[str, str],
        event: Event,
        local_deadline: LocalDeadline,
        operation_id: str,
    ) -> ExtractionResultDTO | ErrorResultDTO:
        remaining_ms = self._remaining_ms(local_deadline)
        if remaining_ms <= 0:
            return self._error(operation_id, "deadline_exceeded")
        completed_before_deadline = event.wait(remaining_ms / 1_000)
        with self._lock:
            completed = self._completed_repairs.get(identity)
        if (
            not completed_before_deadline
            or self._remaining_ms(local_deadline) <= 0
        ):
            return self._error(operation_id, "deadline_exceeded")
        if completed is not None:
            return completed
        return self._error(operation_id, "deadline_exceeded")

    def _execute_fresh_repair(
        self,
        request: RepairRequestV2DTO,
        local_deadline: LocalDeadline,
        started_at: datetime,
    ) -> ExtractionResultDTO | ErrorResultDTO:
        if self._cancelled():
            return self._error(request.operation_id, "extractor_timeout")

        clean_content = self._resolve_content(request, local_deadline)
        if isinstance(clean_content, ErrorResultDTO):
            return clean_content

        encoded_content = clean_content.encode("utf-8")
        if len(encoded_content) > REPAIR_CONTENT_MAX_UTF8_BYTES:
            return self._error(request.operation_id, "input_too_large")
        actual_hash = "sha256:" + hashlib.sha256(encoded_content).hexdigest()
        if actual_hash != request.clean_content_hash:
            return self._error(
                request.operation_id,
                "repair_content_hash_mismatch",
            )

        expired = self._deadline_error(local_deadline, request.operation_id)
        if expired is not None:
            return expired
        if self._cancelled():
            return self._error(request.operation_id, "extractor_timeout")

        try:
            payload = self._provider_payload(request, clean_content)
            timeout_ms = self._remaining_ms(local_deadline)
            if timeout_ms <= 0:
                raise DeadlineExceededError(
                    "repair deadline exhausted before provider"
                )
            with self._lock:
                self.provider_invocation_count += 1
            raw = self._client.invoke(
                operation_id=request.operation_id,
                payload=payload,
                timeout_ms=timeout_ms,
                cancelled=self._cancelled,
            )
            expired = self._deadline_error(
                local_deadline, request.operation_id
            )
            if expired is not None:
                return expired
            if self._cancelled():
                return self._error(
                    request.operation_id, "extractor_timeout"
                )
            mapped = self._map_provider_result(
                request,
                clean_content,
                raw,
                started_at,
            )
            return self._validate_result(request, clean_content, mapped)
        except DeadlineExceededError:
            return self._error(request.operation_id, "deadline_exceeded")
        except TimeoutError:
            return self._error(request.operation_id, "extractor_timeout")
        except ProviderFailure as failure:
            code = (
                failure.code
                if failure.code in REPAIR_V2_ERROR_CODES
                else "unexpected_provider_error"
            )
            return self._error(request.operation_id, code)
        except ProviderOutputError as failure:
            code = (
                failure.code
                if failure.code in REPAIR_V2_ERROR_CODES
                else "invalid_extraction_schema"
            )
            return self._error(request.operation_id, code)
        except (TypeError, ValueError):
            return self._error(
                request.operation_id, "invalid_extraction_schema"
            )
        except BaseException as exception:
            return map_unexpected_exception(
                exception,
                provider="nova_lite",
                operation_id=request.operation_id,
                occurred_at=self._now(),
            ).as_result()

    def extract(
        self, request: ExtractRequestDTO
    ) -> ExtractionResultDTO | ErrorResultDTO:
        return self._v1.extract(request)

    def repair(
        self, request: RepairRequestV2DTO
    ) -> ExtractionResultDTO | ErrorResultDTO:
        entered_at = self._now()
        entered_monotonic_ms = self._monotonic_ms()
        if not isinstance(request, RepairRequestV2DTO):
            operation_id = getattr(request, "operation_id", "OP-INVALID-V2")
            return self._error(operation_id, "invalid_extraction_schema")

        expected_hash = self._expected_authorization_hash(request)
        if request.repair_authorization_hash != expected_hash:
            return self._error(
                request.operation_id, "invalid_extraction_schema"
            )

        identity = (
            request.operation_id,
            request.repair_authorization_hash,
        )
        with self._lock:
            completed = self._completed_repairs.get(identity)
            if completed is not None:
                return completed

            prior_hash = self._operation_authorizations.get(
                request.operation_id
            )
            if (
                prior_hash is not None
                and prior_hash != request.repair_authorization_hash
            ):
                return self._conflict(request.operation_id)

            inflight = self._inflight_repairs.get(identity)
            is_leader = inflight is None
            if is_leader:
                inflight = Event()
                self._inflight_repairs[identity] = inflight
                self._operation_authorizations[request.operation_id] = (
                    request.repair_authorization_hash
                )

        assert inflight is not None
        if not is_leader:
            try:
                local_deadline = self._local_deadline(
                    request,
                    entered_at=entered_at,
                    entered_monotonic_ms=entered_monotonic_ms,
                )
            except BaseException as exception:
                return map_unexpected_exception(
                    exception,
                    provider="nova_lite",
                    operation_id=request.operation_id,
                    occurred_at=self._now(),
                ).as_result()
            if isinstance(local_deadline, ErrorResultDTO):
                return local_deadline
            return self._await_inflight(
                identity,
                inflight,
                local_deadline,
                request.operation_id,
            )

        try:
            local_deadline = self._local_deadline(
                request,
                entered_at=entered_at,
                entered_monotonic_ms=entered_monotonic_ms,
            )
            if isinstance(local_deadline, ErrorResultDTO):
                result = local_deadline
            else:
                result = self._execute_fresh_repair(
                    request,
                    local_deadline,
                    entered_at,
                )
        except BaseException as exception:
            result = map_unexpected_exception(
                exception,
                provider="nova_lite",
                operation_id=request.operation_id,
                occurred_at=self._now(),
            ).as_result()
        return self._complete(
            identity,
            request,
            result,
            entered_monotonic_ms,
        )

    def health_check(
        self,
        request: ExtractorHealthCheckRequestDTO,
    ) -> ProviderHealthDTO | ErrorResultDTO:
        return self._v1.health_check(request)


__all__ = (
    "AuthoritativeContentResolver",
    "CONTRACT_VERSION_V2",
    "DEFAULT_MODEL_VERSION_V2",
    "NovaLiteEvidenceExtractorV2",
    "PROVIDER_VERSION_V2",
)
