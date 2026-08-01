"""Immutable Evidence model、append-only assessment 與 AnalysisResult。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping
from urllib.parse import urlsplit

from crypto_trust_agent.domain.errors import (
    AssessmentSequenceConflict,
    LineageViolation,
)
from crypto_trust_agent.domain.primitives import CanonicalDecimal, UtcInstant


_ID_PATTERNS = {
    "evidence_id": re.compile(r"^EVID-[A-Za-z0-9._:-]{1,123}$"),
    "task_id": re.compile(r"^TASK-[A-Za-z0-9._:-]{1,123}$"),
    "execution_id": re.compile(r"^EXEC-[A-Za-z0-9._:-]{1,123}$"),
    "raw_record_id": re.compile(r"^RAW-.+"),
    "link_id": re.compile(r"^LINK-.+"),
    "claim_id": re.compile(r"^CLAIM-.+"),
    "assessment_id": re.compile(r"^ASSESS-.+"),
    "analysis_id": re.compile(r"^AN-.+"),
}
_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_GROUP_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _require_id(name: str, value: str) -> None:
    if not isinstance(value, str) or not _ID_PATTERNS[name].fullmatch(value):
        raise LineageViolation(f"invalid {name}")


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_PATTERN.fullmatch(value):
        raise LineageViolation(f"invalid {name}")


def _as_utc(value: str | UtcInstant | None) -> UtcInstant | None:
    if value is None or isinstance(value, UtcInstant):
        return value
    return UtcInstant(value)


class SourceType(str, Enum):
    MARKET = "market"
    NEWS = "news"
    OFFICIAL = "official"
    ON_CHAIN = "on_chain"
    SOCIAL = "social"
    MACRO = "macro"
    DATASET = "dataset"


class ValidationStatus(str, Enum):
    ACTIVE = "active"
    QUARANTINED = "quarantined"


class Stance(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class ContentKind(str, Enum):
    QUOTE = "quote"
    METRIC = "metric"
    DOCUMENT_SECTION = "document_section"


class ContentUnit(str, Enum):
    UNICODE_SCALAR = "unicode_scalar"
    UTF8_BYTE = "utf8_byte"


class ContradictionSeverity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProducerKind(str, Enum):
    MODEL = "model"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


@dataclass(frozen=True, slots=True)
class ContentOffset:
    start: int
    end: int

    def __post_init__(self) -> None:
        if type(self.start) is not int or type(self.end) is not int:
            raise ValueError("content offset must use integers")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("content offset must be a non-empty half-open range")


@dataclass(frozen=True, slots=True)
class ContentReference:
    kind: ContentKind | str
    value: str
    offset: ContentOffset | None
    unit: ContentUnit | str | None

    def __post_init__(self) -> None:
        try:
            kind = ContentKind(self.kind)
            unit = None if self.unit is None else ContentUnit(self.unit)
        except ValueError as error:
            raise ValueError("invalid content reference enum") from error
        if not isinstance(self.value, str) or not 1 <= len(self.value) <= 4096:
            raise ValueError("content reference value must contain 1..4096 characters")
        if (self.offset is None) != (unit is None):
            raise ValueError("content reference unit is required exactly when offset exists")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "unit", unit)


@dataclass(frozen=True, slots=True)
class QueryProvenance:
    collector: str
    query: str
    parameters: Mapping[str, str | int | bool | None]
    plan_job_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.collector, str) or not 1 <= len(self.collector) <= 128:
            raise ValueError("invalid collector")
        if not isinstance(self.query, str) or not 1 <= len(self.query) <= 2000:
            raise ValueError("invalid query")
        if not isinstance(self.plan_job_id, str) or not 1 <= len(self.plan_job_id) <= 128:
            raise ValueError("invalid plan_job_id")
        values = dict(self.parameters)
        if len(values) > 64 or any(
            type(value) not in (str, int, bool, type(None)) for value in values.values()
        ):
            raise ValueError("invalid query parameters")
        object.__setattr__(self, "parameters", MappingProxyType(values))


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    task_id: str
    execution_id: str
    raw_record_id: str
    source_name: str
    source_type: SourceType | str
    source_url: str | None
    published_at: UtcInstant | str | None
    fetched_at: UtcInstant | str
    content_reference: ContentReference
    raw_locator: str
    raw_content_hash: str
    clean_content_hash: str
    query_provenance: QueryProvenance
    validation_status: ValidationStatus | str
    created_at: UtcInstant | str
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise ValueError("unsupported Evidence schema_version")
        for name in ("evidence_id", "task_id", "execution_id", "raw_record_id"):
            _require_id(name, getattr(self, name))
        try:
            source_type = SourceType(self.source_type)
            status = ValidationStatus(self.validation_status)
        except ValueError as error:
            raise ValueError("invalid Evidence enum") from error
        if not isinstance(self.source_name, str) or not 1 <= len(self.source_name) <= 256:
            raise ValueError("invalid source_name")
        if self.source_url is None:
            if source_type is not SourceType.DATASET:
                raise LineageViolation("non-dataset Evidence requires an HTTPS source URL")
        else:
            parsed_url = urlsplit(self.source_url)
            if parsed_url.scheme != "https" or not parsed_url.netloc or len(self.source_url) > 2048:
                raise LineageViolation("source_url must be HTTPS")
        parsed_locator = urlsplit(self.raw_locator)
        if not self.raw_locator or not parsed_locator.scheme or len(self.raw_locator) > 4096:
            raise LineageViolation("raw_locator must be an opaque URI")
        _require_hash(self.raw_content_hash, "raw_content_hash")
        _require_hash(self.clean_content_hash, "clean_content_hash")
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "validation_status", status)
        object.__setattr__(self, "published_at", _as_utc(self.published_at))
        object.__setattr__(self, "fetched_at", _as_utc(self.fetched_at))
        object.__setattr__(self, "created_at", _as_utc(self.created_at))


@dataclass(frozen=True, slots=True)
class EvidenceClaimLink:
    link_id: str
    task_id: str
    evidence_id: str
    claim_id: str
    stance: Stance | str
    created_at: UtcInstant | str
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise ValueError("unsupported link schema_version")
        for name in ("link_id", "task_id", "evidence_id", "claim_id"):
            _require_id(name, getattr(self, name))
        try:
            stance = Stance(self.stance)
        except ValueError as error:
            raise ValueError("invalid claim stance") from error
        object.__setattr__(self, "stance", stance)
        object.__setattr__(self, "created_at", _as_utc(self.created_at))

    @classmethod
    def for_evidence(
        cls,
        *,
        link_id: str,
        task_id: str,
        evidence: Evidence,
        claim_id: str,
        stance: Stance,
        created_at: str | UtcInstant,
    ) -> EvidenceClaimLink:
        if evidence.task_id != task_id:
            raise LineageViolation("claim link and Evidence must share task_id")
        if evidence.validation_status is ValidationStatus.QUARANTINED:
            raise LineageViolation("quarantined Evidence cannot be linked")
        return cls(
            link_id=link_id,
            task_id=task_id,
            evidence_id=evidence.evidence_id,
            claim_id=claim_id,
            stance=stance,
            created_at=created_at,
        )


@dataclass(frozen=True, slots=True)
class EvidenceAssessment:
    assessment_id: str
    task_id: str
    evidence_id: str
    assessment_sequence: int
    assessment_version: str
    ruleset_version: str
    source_trust: CanonicalDecimal | str
    relevance: CanonicalDecimal | str
    freshness: CanonicalDecimal | str
    independence: CanonicalDecimal | str
    independence_group: str
    consistency: CanonicalDecimal | str
    overall_confidence: CanonicalDecimal | str
    contradiction_severity: ContradictionSeverity | str
    computed_at: UtcInstant | str
    limitations: tuple[str, ...]
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise ValueError("unsupported assessment schema_version")
        for name in ("assessment_id", "task_id", "evidence_id"):
            _require_id(name, getattr(self, name))
        if type(self.assessment_sequence) is not int or self.assessment_sequence < 1:
            raise ValueError("assessment_sequence must be positive")
        if not _VERSION_PATTERN.fullmatch(self.assessment_version):
            raise ValueError("invalid assessment_version")
        if not isinstance(self.ruleset_version, str) or not 1 <= len(self.ruleset_version) <= 64:
            raise ValueError("invalid ruleset_version")
        if not _GROUP_PATTERN.fullmatch(self.independence_group):
            raise ValueError("invalid independence_group")
        decimal_names = (
            "source_trust",
            "relevance",
            "freshness",
            "independence",
            "consistency",
            "overall_confidence",
        )
        for name in decimal_names:
            current = getattr(self, name)
            value = current if isinstance(current, CanonicalDecimal) else CanonicalDecimal(current)
            value.require_probability()
            object.__setattr__(self, name, value)
        try:
            severity = ContradictionSeverity(self.contradiction_severity)
        except ValueError as error:
            raise ValueError("invalid contradiction severity") from error
        limitations = tuple(self.limitations)
        if len(limitations) > 50 or any(
            not isinstance(item, str) or not 1 <= len(item) <= 512
            for item in limitations
        ):
            raise ValueError("invalid limitations")
        object.__setattr__(self, "contradiction_severity", severity)
        object.__setattr__(self, "computed_at", _as_utc(self.computed_at))
        object.__setattr__(self, "limitations", limitations)


def append_assessment(
    history: Iterable[EvidenceAssessment],
    assessment: EvidenceAssessment,
) -> tuple[EvidenceAssessment, ...]:
    existing = tuple(history)
    if existing:
        if any(
            item.task_id != assessment.task_id
            or item.evidence_id != assessment.evidence_id
            for item in existing
        ):
            raise LineageViolation("assessment history must be task/evidence scoped")
        if assessment.assessment_sequence <= max(
            item.assessment_sequence for item in existing
        ):
            raise AssessmentSequenceConflict(
                "assessment_sequence must monotonically increase"
            )
        if any(item.assessment_id == assessment.assessment_id for item in existing):
            raise AssessmentSequenceConflict("assessment_id already exists")
    return (*existing, assessment)


def select_latest_assessments(
    assessments: Iterable[EvidenceAssessment],
) -> Mapping[str, EvidenceAssessment]:
    latest: dict[str, EvidenceAssessment] = {}
    seen_sequences: set[tuple[str, int]] = set()
    for assessment in assessments:
        key = (assessment.evidence_id, assessment.assessment_sequence)
        if key in seen_sequences:
            raise AssessmentSequenceConflict("duplicate assessment sequence")
        seen_sequences.add(key)
        current = latest.get(assessment.evidence_id)
        if current is None or assessment.assessment_sequence > current.assessment_sequence:
            latest[assessment.evidence_id] = assessment
    return MappingProxyType(latest)


@dataclass(frozen=True, slots=True)
class AnalysisQuality:
    status: str
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"valid", "degraded", "fallback"}:
            raise ValueError("invalid analysis quality status")
        limitations = tuple(self.limitations)
        if len(limitations) > 50:
            raise ValueError("too many analysis limitations")
        object.__setattr__(self, "limitations", limitations)


@dataclass(frozen=True, slots=True)
class AnalysisProducer:
    kind: ProducerKind | str
    name: str
    version: str
    ruleset_version: str

    def __post_init__(self) -> None:
        try:
            kind = ProducerKind(self.kind)
        except ValueError as error:
            raise ValueError("invalid producer kind") from error
        if not self.name or not isinstance(self.version, str) or not 1 <= len(self.version) <= 128:
            raise ValueError("invalid producer identity/version")
        if not self.ruleset_version or len(self.ruleset_version) > 128:
            raise ValueError("invalid producer ruleset_version")
        object.__setattr__(self, "kind", kind)


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    analysis_id: str
    task_id: str
    execution_id: str
    analysis_type: str
    asset: str
    as_of: UtcInstant | str
    input_refs: tuple[str, ...]
    source_refs: tuple[str, ...]
    values: Mapping[str, CanonicalDecimal | str]
    quality: AnalysisQuality
    producer: AnalysisProducer
    computed_at: UtcInstant | str
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise ValueError("unsupported AnalysisResult schema_version")
        for name in ("analysis_id", "task_id", "execution_id"):
            _require_id(name, getattr(self, name))
        if not _SAFE_NAME_PATTERN.fullmatch(self.analysis_type):
            raise ValueError("invalid analysis_type")
        if not re.fullmatch(r"^[A-Z0-9][A-Z0-9._-]{0,31}$", self.asset):
            raise ValueError("invalid asset")
        input_refs = tuple(self.input_refs)
        source_refs = tuple(self.source_refs)
        if not input_refs or not source_refs or any(not ref for ref in (*input_refs, *source_refs)):
            raise LineageViolation("AnalysisResult requires input and source refs")
        values: dict[str, CanonicalDecimal] = {}
        for name, raw in self.values.items():
            if not _SAFE_NAME_PATTERN.fullmatch(name):
                raise ValueError("invalid analysis value name")
            values[name] = raw if isinstance(raw, CanonicalDecimal) else CanonicalDecimal(raw)
        if not values:
            raise ValueError("AnalysisResult requires values")
        object.__setattr__(self, "as_of", _as_utc(self.as_of))
        object.__setattr__(self, "computed_at", _as_utc(self.computed_at))
        object.__setattr__(self, "input_refs", input_refs)
        object.__setattr__(self, "source_refs", source_refs)
        object.__setattr__(self, "values", MappingProxyType(values))
