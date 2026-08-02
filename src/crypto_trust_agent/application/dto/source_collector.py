"""Frozen SourceCollector boundary DTOs for contract version 1.0.0."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlsplit

from crypto_trust_agent.application.dto.common import DeadlineDTO
from crypto_trust_agent.domain.primitives import ContractValidationError, UtcInstant

SCHEMA_VERSION = "1.0.0"
COLLECT_SECURITY_POLICY_VERSION = "collector-security-1.0.0"
MAX_URL_CHARS = 2_048
MAX_RAW_BYTES = 5_242_880
MAX_CLEAN_BYTES = 1_048_576
MAX_REDIRECTS = 3
SOURCE_CATEGORIES = ("market", "news", "official", "on_chain", "social", "macro")
COLLECT_ERROR_CODES = (
    "unsupported_category", "collector_not_configured", "source_not_allowlisted",
    "dns_ip_rejected", "ssrf_blocked", "robots_disallowed", "payload_too_large",
    "redirect_limit_exceeded", "source_rate_limited", "fetch_timeout",
    "invalid_source_schema", "collector_unavailable", "deadline_exceeded",
    "unexpected_provider_error",
)
COLLECTOR_HEALTH_ERROR_CODES = (
    "collector_not_configured", "fetch_timeout", "collector_unavailable",
    "deadline_exceeded", "unexpected_provider_error",
)
CAPABILITIES_ERROR_CODES = (
    "collector_not_configured", "collector_unavailable", "deadline_exceeded",
    "unexpected_provider_error",
)

_OPERATION = re.compile(r"^OP-[A-Z0-9][A-Z0-9._:-]{0,126}$")
_TASK = re.compile(r"^TASK-[A-Za-z0-9._:-]{1,123}$")
_EXECUTION = re.compile(r"^EXEC-[A-Za-z0-9._:-]{1,123}$")
_JOB = re.compile(r"^JOB-")
_RAW = re.compile(r"^RAW-")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_SCALAR_TYPES = (str, int, bool, type(None))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractValidationError(message)


def _bounded_text(value: str, minimum: int, maximum: int, name: str) -> None:
    _require(isinstance(value, str) and minimum <= len(value) <= maximum, f"invalid {name}")


def _identifier(value: str, pattern: re.Pattern[str], name: str) -> None:
    _require(isinstance(value, str) and pattern.fullmatch(value) is not None, f"invalid {name}")


def _prefix_identifier(value: str, pattern: re.Pattern[str], name: str) -> None:
    _require(isinstance(value, str) and pattern.match(value) is not None, f"invalid {name}")


def _utc(value: str | UtcInstant | None) -> UtcInstant | None:
    return value if value is None or isinstance(value, UtcInstant) else UtcInstant(value)


def _https_uri(value: str, name: str) -> None:
    _require(isinstance(value, str) and len(value) <= MAX_URL_CHARS and value.startswith("https://"), f"invalid {name}")
    parsed = urlsplit(value)
    _require(bool(parsed.netloc and parsed.hostname), f"invalid {name}")


def _opaque_uri(value: str, name: str) -> None:
    _require(isinstance(value, str) and len(value) <= 4_096, f"invalid {name}")
    parsed = urlsplit(value)
    _require(bool(parsed.scheme), f"invalid {name}")


def _assets(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    frozen = tuple(values)
    _require(1 <= len(frozen) <= 100 and len(set(frozen)) == len(frozen), "invalid assets")
    for value in frozen:
        _require(isinstance(value, str) and re.fullmatch(r"^[A-Z0-9][A-Z0-9._-]{0,31}$", value) is not None, "invalid asset")
    return frozen


def _wire(value: Any) -> Any:
    if hasattr(value, "to_wire"):
        return value.to_wire()
    if isinstance(value, UtcInstant):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    return value


class WireDTO:
    def to_wire(self) -> dict[str, object]:
        return {field.name: _wire(getattr(self, field.name)) for field in fields(self)}


@dataclass(frozen=True, slots=True)
class RangeDTO(WireDTO):
    start: str | UtcInstant
    end: str | UtcInstant

    def __post_init__(self) -> None:
        start = _utc(self.start)
        end = _utc(self.end)
        _require(start is not None and end is not None and start.as_datetime() <= end.as_datetime(), "invalid reporting_range")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


@dataclass(frozen=True, slots=True)
class QueryProvenanceDTO(WireDTO):
    collector: str
    query: str
    parameters: Mapping[str, str | int | bool | None]

    def __post_init__(self) -> None:
        _bounded_text(self.collector, 1, 128, "collector")
        _bounded_text(self.query, 1, 2_000, "query")
        _require(isinstance(self.parameters, Mapping) and len(self.parameters) <= 64, "invalid parameters")
        frozen = dict(self.parameters)
        _require(all(isinstance(key, str) and type(value) in _SCALAR_TYPES for key, value in frozen.items()), "invalid parameters")
        object.__setattr__(self, "parameters", MappingProxyType(frozen))


@dataclass(frozen=True, slots=True)
class SecurityResultDTO(WireDTO):
    allowlist_version: str
    url_allowed: bool
    dns_ip_validated: bool
    robots_allowed: bool
    redirect_count: int

    def __post_init__(self) -> None:
        _identifier(self.allowlist_version, _SEMVER, "allowlist_version")
        _require(self.url_allowed is True and self.dns_ip_validated is True and self.robots_allowed is True, "security checks must pass")
        _require(type(self.redirect_count) is int and 0 <= self.redirect_count <= MAX_REDIRECTS, "invalid redirect_count")


@dataclass(frozen=True, slots=True)
class RawRecordDTO(WireDTO):
    raw_record_id: str
    task_id: str
    execution_id: str
    plan_job_id: str
    source_name: str
    source_type: str
    source_url: str
    canonical_url: str
    http_status: int
    published_at: str | UtcInstant | None
    fetched_at: str | UtcInstant
    content_hash: str
    clean_content_hash: str
    raw_locator: str
    media_type: str
    clean_content: str
    query_provenance: QueryProvenanceDTO
    security: SecurityResultDTO
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _prefix_identifier(self.raw_record_id, _RAW, "raw_record_id")
        _identifier(self.task_id, _TASK, "task_id")
        _identifier(self.execution_id, _EXECUTION, "execution_id")
        _prefix_identifier(self.plan_job_id, _JOB, "plan_job_id")
        _bounded_text(self.source_name, 1, 256, "source_name")
        _require(self.source_type in SOURCE_CATEGORIES, "invalid source_type")
        _https_uri(self.source_url, "source_url")
        _https_uri(self.canonical_url, "canonical_url")
        _require(type(self.http_status) is int and 200 <= self.http_status <= 299, "invalid http_status")
        published = _utc(self.published_at)
        fetched = _utc(self.fetched_at)
        _identifier(self.content_hash, _HASH, "content_hash")
        _identifier(self.clean_content_hash, _HASH, "clean_content_hash")
        _opaque_uri(self.raw_locator, "raw_locator")
        _require(self.media_type in {"text/html", "text/plain", "application/json", "text/csv", "application/pdf"}, "invalid media_type")
        _require(isinstance(self.clean_content, str) and len(self.clean_content) <= MAX_CLEAN_BYTES and len(self.clean_content.encode("utf-8")) <= MAX_CLEAN_BYTES, "clean_content exceeds limit")
        _require(isinstance(self.query_provenance, QueryProvenanceDTO), "invalid query_provenance")
        _require(isinstance(self.security, SecurityResultDTO), "invalid security")
        object.__setattr__(self, "published_at", published)
        object.__setattr__(self, "fetched_at", fetched)


@dataclass(frozen=True, slots=True)
class CollectionIssueDTO(WireDTO):
    code: str
    retryable: bool
    safe_message: str

    def __post_init__(self) -> None:
        _identifier(self.code, _SAFE, "code")
        _require(type(self.retryable) is bool, "retryable must be boolean")
        _bounded_text(self.safe_message, 1, 512, "safe_message")


@dataclass(frozen=True, slots=True)
class CollectionResultDTO(WireDTO):
    operation_id: str
    outcome: str
    records: tuple[RawRecordDTO, ...]
    started_at: str | UtcInstant
    finished_at: str | UtcInstant
    duration_ms: int
    retry_count: int
    issues: tuple[CollectionIssueDTO, ...]
    limitations: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _identifier(self.operation_id, _OPERATION, "operation_id")
        _require(self.outcome in {"success", "skipped", "failed"}, "invalid collection outcome")
        records = tuple(self.records)
        issues = tuple(self.issues)
        limitations = tuple(self.limitations)
        _require(len(records) <= 500 and all(isinstance(item, RawRecordDTO) for item in records), "invalid records")
        _require(len(issues) <= 50 and all(isinstance(item, CollectionIssueDTO) for item in issues), "invalid issues")
        _require(len(limitations) <= 50, "invalid limitations")
        for limitation in limitations:
            _bounded_text(limitation, 1, 512, "limitation")
        started = _utc(self.started_at)
        finished = _utc(self.finished_at)
        _require(started is not None and finished is not None and started.as_datetime() <= finished.as_datetime(), "invalid result timestamps")
        _require(type(self.duration_ms) is int and 0 <= self.duration_ms <= 30_000, "invalid duration_ms")
        _require(self.retry_count == 0, "retry_count must be zero")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "issues", issues)
        object.__setattr__(self, "limitations", limitations)
        object.__setattr__(self, "started_at", started)
        object.__setattr__(self, "finished_at", finished)


@dataclass(frozen=True, slots=True)
class CollectRequestDTO(WireDTO):
    operation_id: str
    task_id: str
    execution_id: str
    plan_job_id: str
    source_category: str
    collection_mode: str
    requirement: str
    assets: tuple[str, ...]
    approved_query: str
    approved_urls: tuple[str, ...]
    reporting_range: RangeDTO
    priority: int
    security_policy_version: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _identifier(self.operation_id, _OPERATION, "operation_id")
        _identifier(self.task_id, _TASK, "task_id")
        _identifier(self.execution_id, _EXECUTION, "execution_id")
        _prefix_identifier(self.plan_job_id, _JOB, "plan_job_id")
        _require(self.source_category in SOURCE_CATEGORIES, "invalid source_category")
        _require(self.collection_mode in {"static", "playwright"}, "invalid collection_mode")
        _require(self.requirement in {"required", "required_if_available", "optional"}, "invalid requirement")
        object.__setattr__(self, "assets", _assets(self.assets))
        _bounded_text(self.approved_query, 1, 2_000, "approved_query")
        urls = tuple(self.approved_urls)
        _require(len(urls) <= 100 and len(set(urls)) == len(urls), "invalid approved_urls")
        for url in urls:
            _https_uri(url, "approved_url")
        object.__setattr__(self, "approved_urls", urls)
        _require(isinstance(self.reporting_range, RangeDTO), "invalid reporting_range")
        _require(type(self.priority) is int and 1 <= self.priority <= 100, "invalid priority")
        _require(self.security_policy_version == COLLECT_SECURITY_POLICY_VERSION, "invalid security_policy_version")
        _require(isinstance(self.deadline, DeadlineDTO) and self.deadline.operation_id == self.operation_id, "deadline operation_id mismatch")


@dataclass(frozen=True, slots=True)
class CollectorHealthCheckRequestDTO(WireDTO):
    operation_id: str
    provider: str
    deadline: DeadlineDTO
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _identifier(self.operation_id, _OPERATION, "operation_id")
        _identifier(self.provider, _SAFE, "provider")
        _require(isinstance(self.deadline, DeadlineDTO) and self.deadline.operation_id == self.operation_id, "deadline operation_id mismatch")


@dataclass(frozen=True, slots=True)
class CapabilitiesRequestDTO(CollectorHealthCheckRequestDTO):
    pass


@dataclass(frozen=True, slots=True)
class CollectorCapabilitiesDTO(WireDTO):
    provider: str
    source_categories: tuple[str, ...]
    supports_static: bool
    supports_dynamic: bool
    security_policy_versions: tuple[str, ...]
    max_raw_bytes: int = MAX_RAW_BYTES
    max_clean_bytes: int = MAX_CLEAN_BYTES
    max_redirects: int = MAX_REDIRECTS
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported schema_version")
        _identifier(self.provider, _SAFE, "provider")
        categories = tuple(self.source_categories)
        policies = tuple(self.security_policy_versions)
        _require(categories and len(set(categories)) == len(categories) and all(item in SOURCE_CATEGORIES for item in categories), "invalid source_categories")
        _require(type(self.supports_static) is bool and type(self.supports_dynamic) is bool, "invalid support flags")
        _require(policies and len(set(policies)) == len(policies) and all(item == COLLECT_SECURITY_POLICY_VERSION for item in policies), "invalid security_policy_versions")
        _require((self.max_raw_bytes, self.max_clean_bytes, self.max_redirects) == (MAX_RAW_BYTES, MAX_CLEAN_BYTES, MAX_REDIRECTS), "invalid collector limits")
        object.__setattr__(self, "source_categories", categories)
        object.__setattr__(self, "security_policy_versions", policies)


__all__ = (
    "CAPABILITIES_ERROR_CODES", "COLLECTOR_HEALTH_ERROR_CODES", "COLLECT_ERROR_CODES",
    "CapabilitiesRequestDTO", "CollectionIssueDTO", "CollectionResultDTO",
    "CollectRequestDTO", "CollectorCapabilitiesDTO", "CollectorHealthCheckRequestDTO",
    "QueryProvenanceDTO", "RangeDTO", "RawRecordDTO", "SecurityResultDTO",
)
