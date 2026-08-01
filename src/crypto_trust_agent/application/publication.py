"""Canonical T62 artifact models, renderers, validation, and publication workflow."""

from __future__ import annotations

import base64
import csv
import hashlib
import html
import io
import json
import re
from dataclasses import dataclass, fields, replace
from threading import Event, Thread
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from crypto_trust_agent.application.dto.common import (
    DeadlineDTO,
    DeadlineExceededError,
    ErrorResultDTO,
    LocalDeadline,
    build_local_deadline,
)
from crypto_trust_agent.application.dto.reasoning import (
    AnalysisRefDTO,
    ConclusionDTO,
    ConfidenceComponentsDTO,
    ContradictionDTO,
    FactDTO,
    InferenceDTO,
)
from crypto_trust_agent.application.dto.repositories import (
    ArtifactDescriptorDTO,
    ArtifactManifestDTO,
    ArtifactPutRequestDTO,
    ClockReadRequestDTO,
    EvidenceAssessmentDTO,
    EvidenceClaimLinkDTO,
    EvidenceDTO,
    ExecutionEventDTO,
    PutManifestRequestDTO,
)
from crypto_trust_agent.application.ports.repositories import ArtifactRepository, Clock
from crypto_trust_agent.domain.primitives import CanonicalDecimal, ContractValidationError, UtcInstant

SCHEMA_VERSION = "1.0.0"
TRANSITION_DATE = "2026-05-31"
REPOSITORY_TIMEOUT_MS = 5_000
FINAL_WRITE_TARGET_MS = 20_000
FINAL_WRITE_BUFFER_MS = 5_000
FINAL_WRITE_HARD_LIMIT_MS = FINAL_WRITE_TARGET_MS + FINAL_WRITE_BUFFER_MS
_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_EVIDENCE_PATTERN = re.compile(r"^EVID-[A-Za-z0-9._:-]{1,123}$")
_TASK_PATTERN = re.compile(r"^TASK-[A-Za-z0-9._:-]{1,123}$")
_EXECUTION_PATTERN = re.compile(r"^EXEC-[A-Za-z0-9._:-]{1,123}$")
_RAW_RECORD_PATTERN = re.compile(r"^RAW-.+")
_CLAIM_PATTERN = re.compile(r"^CLAIM-.+")
_ASSESSMENT_PATTERN = re.compile(r"^ASSESS-.+")
_ASSET_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_FORBIDDEN_KEY = re.compile(
    r"authorization|jwt|token|secret|password|prompt|raw_content",
    re.IGNORECASE,
)
_FORBIDDEN_VALUE = re.compile(
    r"authorization\s*:|bearer\s+|jwt|token|secret|password|prompt|raw_content",
    re.IGNORECASE,
)
_DECIMAL_KEYS = {
    "confidence",
    "evidence_quality",
    "consistency",
    "coverage",
    "overall",
    "source_trust",
    "relevance",
    "freshness",
    "independence",
    "overall_confidence",
    "anomaly_score",
    "probability",
}


class PublicationValidationError(ValueError):
    """A fail-closed canonical output validation failure."""


class PublicationError(RuntimeError):
    """A safe publication boundary failure."""

    def __init__(self, code: str, safe_message: str = "Artifact publication failed") -> None:
        self.code = code
        super().__init__(f"{code}: {safe_message}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PublicationValidationError(message)


def _bounded_text(value: str, maximum: int, name: str) -> None:
    _require(isinstance(value, str) and 1 <= len(value) <= maximum, f"invalid {name}")


def _as_utc(value: str | UtcInstant) -> UtcInstant:
    try:
        return value if isinstance(value, UtcInstant) else UtcInstant(value)
    except (TypeError, ValueError) as error:
        raise PublicationValidationError("invalid UTC timestamp") from error


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _wire(value: Any) -> Any:
    if hasattr(value, "to_wire"):
        return value.to_wire()
    if isinstance(value, UtcInstant):
        return str(value)
    if isinstance(value, CanonicalDecimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        _wire(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _sanitize(value: Any) -> Any:
    """Remove forbidden keys and redact suspicious scalar values recursively."""

    if isinstance(value, Mapping):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if not _FORBIDDEN_KEY.search(str(key))
        }
    if isinstance(value, (tuple, list)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str) and _FORBIDDEN_VALUE.search(value):
        return "[REDACTED]"
    if type(value) in (str, int, bool, type(None)):
        return value
    return str(value)


@dataclass(frozen=True, slots=True)
class CitedStatementDTO:
    statement: str
    evidence_refs: tuple[str, ...]
    analysis_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _bounded_text(self.statement, 2_000, "cited statement")
        evidence = tuple(self.evidence_refs)
        analyses = tuple(self.analysis_refs)
        _require(bool(evidence or analyses), "cited statement requires evidence or analysis")
        _require(
            len(evidence) == len(set(evidence))
            and all(isinstance(item, str) and item.startswith("EVID-") for item in evidence),
            "invalid cited evidence_refs",
        )
        _require(
            len(analyses) == len(set(analyses))
            and all(isinstance(item, str) and item.startswith("ANALYSIS-") for item in analyses),
            "invalid cited analysis_refs",
        )
        object.__setattr__(self, "evidence_refs", evidence)
        object.__setattr__(self, "analysis_refs", analyses)

    def to_wire(self) -> dict[str, object]:
        return {
            "statement": self.statement,
            "evidence_refs": list(self.evidence_refs),
            "analysis_refs": list(self.analysis_refs),
        }


@dataclass(frozen=True, slots=True)
class KeyEvidenceDTO:
    evidence_id: str | None
    analysis_id: str | None
    explanation: str

    def __post_init__(self) -> None:
        _require((self.evidence_id is None) != (self.analysis_id is None), "key evidence requires exactly one reference")
        if self.evidence_id is not None:
            _require(self.evidence_id.startswith("EVID-"), "invalid key evidence_id")
        if self.analysis_id is not None:
            _require(self.analysis_id.startswith("ANALYSIS-"), "invalid key analysis_id")
        _bounded_text(self.explanation, 2_000, "key evidence explanation")

    def to_wire(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "analysis_id": self.analysis_id,
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class MarketDataProvenanceDTO:
    official_dataset: bool
    live_extension: bool
    transition_date: str | None

    def __post_init__(self) -> None:
        _require(type(self.official_dataset) is bool, "official_dataset must be boolean")
        _require(type(self.live_extension) is bool, "live_extension must be boolean")
        if self.live_extension:
            _require(
                self.transition_date == TRANSITION_DATE,
                f"transition_date must be {TRANSITION_DATE}",
            )
        else:
            _require(self.transition_date is None, "transition_date requires live extension")

    def to_wire(self) -> dict[str, object]:
        return {
            "official_dataset": self.official_dataset,
            "live_extension": self.live_extension,
            "transition_date": self.transition_date,
        }


@dataclass(frozen=True, slots=True)
class RendererFailureDTO:
    artifact_type: str
    format: str
    reason_code: str = "renderer_failed"

    def __post_init__(self) -> None:
        allowed = {
            ("markdown_report", "markdown"),
            ("html_report", "html"),
            ("csv_evidence", "csv"),
        }
        _require((self.artifact_type, self.format) in allowed, "invalid renderer failure artifact")
        _require(self.reason_code == "renderer_failed", "invalid renderer failure reason")

    def to_wire(self) -> dict[str, str]:
        return {
            "artifact_type": self.artifact_type,
            "format": self.format,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class FinalReportDTO:
    task_id: str
    execution_id: str
    assets: tuple[str, ...]
    question: str
    market_judgment: CitedStatementDTO
    facts: tuple[FactDTO, ...]
    analyses: tuple[AnalysisRefDTO, ...]
    inferences: tuple[InferenceDTO, ...]
    conclusions: tuple[ConclusionDTO, ...]
    key_evidence: tuple[KeyEvidenceDTO, ...]
    supporting_evidence_ids: tuple[str, ...]
    counter_evidence_ids: tuple[str, ...]
    contradictions: tuple[ContradictionDTO, ...]
    confidence_components: ConfidenceComponentsDTO
    limitations: tuple[str, ...]
    watchpoints: tuple[str, ...]
    source_consistency: CitedStatementDTO
    market_data_provenance: MarketDataProvenanceDTO
    generated_at: str | UtcInstant
    publication_outcome: str = "complete"
    renderer_failures: tuple[RendererFailureDTO, ...] = ()
    disclaimer: str = "This report is informational and is not investment advice."
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported final report schema_version")
        _require(self.task_id.startswith("TASK-"), "invalid report task_id")
        _require(self.execution_id.startswith("EXEC-"), "invalid report execution_id")
        assets = tuple(self.assets)
        _require(bool(assets) and len(assets) == len(set(assets)) and all(_ASSET_PATTERN.fullmatch(item) for item in assets), "invalid report assets")
        _bounded_text(self.question, 2_000, "question")
        for name, values, item_type, maximum in (
            ("facts", self.facts, FactDTO, 200),
            ("analyses", self.analyses, AnalysisRefDTO, 32),
            ("inferences", self.inferences, InferenceDTO, 100),
            ("conclusions", self.conclusions, ConclusionDTO, 50),
            ("key_evidence", self.key_evidence, KeyEvidenceDTO, 120),
            ("contradictions", self.contradictions, ContradictionDTO, 64),
        ):
            items = tuple(values)
            _require(len(items) <= maximum and all(isinstance(item, item_type) for item in items), f"invalid {name}")
            object.__setattr__(self, name, items)
        _require(bool(self.facts) and bool(self.conclusions), "report requires facts and conclusions")
        _require(isinstance(self.market_judgment, CitedStatementDTO), "invalid market_judgment")
        _require(isinstance(self.source_consistency, CitedStatementDTO), "invalid source_consistency")
        _require(isinstance(self.confidence_components, ConfidenceComponentsDTO), "invalid confidence components")
        _require(isinstance(self.market_data_provenance, MarketDataProvenanceDTO), "invalid market data provenance")
        for name in ("supporting_evidence_ids", "counter_evidence_ids"):
            refs = tuple(getattr(self, name))
            _require(len(refs) == len(set(refs)) and all(item.startswith("EVID-") for item in refs), f"invalid {name}")
            object.__setattr__(self, name, refs)
        _require(not set(self.supporting_evidence_ids) & set(self.counter_evidence_ids), "evidence cannot support and contradict simultaneously")
        for name in ("limitations", "watchpoints"):
            values = tuple(getattr(self, name))
            _require(len(values) <= 50 and all(isinstance(item, str) and 1 <= len(item) <= 512 for item in values), f"invalid {name}")
            object.__setattr__(self, name, values)
        failures = tuple(self.renderer_failures)
        _require(all(isinstance(item, RendererFailureDTO) for item in failures), "invalid renderer_failures")
        _require(self.publication_outcome in {"complete", "partial"}, "invalid report publication outcome")
        _require((self.publication_outcome == "partial") == bool(failures), "report outcome does not match renderer failures")
        _bounded_text(self.disclaimer, 512, "disclaimer")
        object.__setattr__(self, "assets", assets)
        object.__setattr__(self, "renderer_failures", failures)
        object.__setattr__(self, "generated_at", _as_utc(self.generated_at))

    def with_renderer_failures(self, failures: Sequence[RendererFailureDTO]) -> FinalReportDTO:
        normalized = tuple(failures)
        return replace(
            self,
            publication_outcome="partial" if normalized else "complete",
            renderer_failures=normalized,
        )

    def to_wire(self) -> dict[str, object]:
        provenance = self.market_data_provenance.to_wire()
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "assets": list(self.assets),
            "question": self.question,
            "market_judgment": self.market_judgment.to_wire(),
            "facts": [_wire(item) for item in self.facts],
            "analyses": [_wire(item) for item in self.analyses],
            "inferences": [_wire(item) for item in self.inferences],
            "conclusions": [_wire(item) for item in self.conclusions],
            "key_evidence": [_wire(item) for item in self.key_evidence],
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "counter_evidence_ids": list(self.counter_evidence_ids),
            "contradictions": [_wire(item) for item in self.contradictions],
            "confidence_components": _wire(self.confidence_components),
            "limitations": list(self.limitations),
            "watchpoints": list(self.watchpoints),
            "source_consistency": self.source_consistency.to_wire(),
            "market_data_provenance": provenance,
            "official_dataset": provenance["official_dataset"],
            "live_extension": provenance["live_extension"],
            "transition_date": provenance["transition_date"],
            "publication_outcome": self.publication_outcome,
            "renderer_failures": [_wire(item) for item in self.renderer_failures],
            "disclaimer": self.disclaimer,
            "generated_at": str(self.generated_at),
        }

    def canonical_json(self) -> bytes:
        return _canonical_json(self)


@dataclass(frozen=True, slots=True)
class EvidenceListEntryDTO:
    evidence_id: str
    source: str
    source_type: str
    source_url: str | None
    source_locator: str
    fetched_at: str | UtcInstant
    content_reference: Mapping[str, object]
    related_claims: tuple[Mapping[str, str], ...]
    content_hash: str
    lineage: Mapping[str, object]
    assessment_id: str
    assessment_version: str
    assessment_sequence: int
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported evidence entry schema_version")
        _require(
            isinstance(self.evidence_id, str) and _EVIDENCE_PATTERN.fullmatch(self.evidence_id) is not None,
            "invalid evidence_id",
        )
        _bounded_text(self.source, 256, "source")
        _bounded_text(self.source_type, 64, "source_type")
        if self.source_url is not None:
            parsed_url = urlsplit(self.source_url)
            _require(
                parsed_url.scheme == "https" and bool(parsed_url.netloc) and len(self.source_url) <= 2_048,
                "source_url must be HTTPS",
            )
        _bounded_text(self.source_locator, 4_096, "source_locator")
        _require(bool(urlsplit(self.source_locator).scheme), "source_locator must be traceable")
        fetched_at = _as_utc(self.fetched_at)
        _require(isinstance(self.content_reference, Mapping) and bool(self.content_reference), "content_reference is required")

        related_claims = tuple(self.related_claims)
        _require(bool(related_claims), "at least one related claim is required")
        for related_claim in related_claims:
            _require(isinstance(related_claim, Mapping), "invalid related claim")
            claim_id = related_claim.get("claim_id")
            stance = related_claim.get("stance")
            _require(
                isinstance(claim_id, str) and _CLAIM_PATTERN.fullmatch(claim_id) is not None,
                "invalid related claim_id",
            )
            _require(stance in {"supports", "contradicts", "context"}, "invalid related claim stance")

        _require(isinstance(self.content_hash, str) and _HASH_PATTERN.fullmatch(self.content_hash) is not None, "invalid content_hash")
        _require(isinstance(self.lineage, Mapping), "invalid lineage")
        required_lineage = {
            "task_id", "execution_id", "raw_record_id", "raw_locator",
            "raw_content_hash", "clean_content_hash", "query_provenance",
        }
        _require(required_lineage <= set(self.lineage), "Evidence lineage is incomplete")
        _require(_TASK_PATTERN.fullmatch(str(self.lineage["task_id"])) is not None, "invalid lineage task_id")
        _require(_EXECUTION_PATTERN.fullmatch(str(self.lineage["execution_id"])) is not None, "invalid lineage execution_id")
        _require(_RAW_RECORD_PATTERN.fullmatch(str(self.lineage["raw_record_id"])) is not None, "invalid lineage raw_record_id")
        raw_locator = str(self.lineage["raw_locator"])
        _require(bool(raw_locator) and bool(urlsplit(raw_locator).scheme) and len(raw_locator) <= 4_096, "invalid lineage raw_locator")
        for name in ("raw_content_hash", "clean_content_hash"):
            _require(_HASH_PATTERN.fullmatch(str(self.lineage[name])) is not None, f"invalid lineage {name}")
        _require(
            isinstance(self.lineage["query_provenance"], Mapping) and bool(self.lineage["query_provenance"]),
            "invalid lineage query_provenance",
        )
        _require(
            isinstance(self.assessment_id, str) and _ASSESSMENT_PATTERN.fullmatch(self.assessment_id) is not None,
            "invalid assessment_id",
        )
        _require(
            isinstance(self.assessment_version, str) and _VERSION_PATTERN.fullmatch(self.assessment_version) is not None,
            "invalid assessment_version",
        )
        _require(type(self.assessment_sequence) is int and self.assessment_sequence >= 1, "invalid assessment_sequence")
        object.__setattr__(self, "fetched_at", fetched_at)
        object.__setattr__(self, "content_reference", _freeze(self.content_reference))
        object.__setattr__(self, "related_claims", tuple(_freeze(item) for item in related_claims))
        object.__setattr__(self, "lineage", _freeze(self.lineage))

    def to_wire(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "evidence_id": self.evidence_id,
            "source": self.source,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "source_locator": self.source_locator,
            "fetched_at": str(self.fetched_at),
            "content_reference": _wire(self.content_reference),
            "related_claims": _wire(self.related_claims),
            "content_hash": self.content_hash,
            "lineage": _wire(self.lineage),
            "assessment_id": self.assessment_id,
            "assessment_version": self.assessment_version,
            "assessment_sequence": self.assessment_sequence,
        }


@dataclass(frozen=True, slots=True)
class EvidenceListDTO:
    task_id: str
    execution_id: str
    items: tuple[EvidenceListEntryDTO, ...]
    generated_at: str | UtcInstant
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported evidence list schema_version")
        _require(self.task_id.startswith("TASK-") and self.execution_id.startswith("EXEC-"), "invalid evidence list identity")
        items = tuple(self.items)
        _require(bool(items) and len(items) <= 200, "evidence list requires 1..200 items")
        _require(all(isinstance(item, EvidenceListEntryDTO) for item in items), "invalid evidence list item")
        _require(len({item.evidence_id for item in items}) == len(items), "duplicate evidence list item")
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "generated_at", _as_utc(self.generated_at))

    @classmethod
    def from_records(
        cls,
        *,
        task_id: str,
        execution_id: str,
        evidence: Sequence[EvidenceDTO],
        claim_links: Sequence[EvidenceClaimLinkDTO],
        assessments: Sequence[EvidenceAssessmentDTO],
        generated_at: str | UtcInstant,
    ) -> EvidenceListDTO:
        evidence_by_id: dict[str, EvidenceDTO] = {}
        for item in evidence:
            _require(item.task_id == task_id, "Evidence task_id lineage mismatch")
            _require(item.execution_id == execution_id, "Evidence execution_id lineage mismatch")
            _require(item.validation_status == "active", "quarantined Evidence cannot be published")
            _require(bool(item.raw_record_id and item.raw_locator and item.raw_content_hash and item.clean_content_hash), "Evidence lineage is incomplete")
            _require(item.evidence_id not in evidence_by_id, "duplicate Evidence")
            evidence_by_id[item.evidence_id] = item
        links_by_evidence: dict[str, list[EvidenceClaimLinkDTO]] = {key: [] for key in evidence_by_id}
        for link in claim_links:
            _require(link.task_id == task_id, "claim link task_id lineage mismatch")
            _require(link.evidence_id in evidence_by_id, "claim link references missing Evidence")
            links_by_evidence[link.evidence_id].append(link)
        assessments_by_evidence: dict[str, list[EvidenceAssessmentDTO]] = {key: [] for key in evidence_by_id}
        seen_assessment_ids: set[str] = set()
        seen_sequences: set[tuple[str, int]] = set()
        for item in assessments:
            _require(item.task_id == task_id, "assessment task_id lineage mismatch")
            _require(item.evidence_id in evidence_by_id, "assessment references missing Evidence")
            _require(item.assessment_id not in seen_assessment_ids, "duplicate assessment_id")
            sequence_key = (item.evidence_id, item.assessment_sequence)
            _require(sequence_key not in seen_sequences, "duplicate assessment sequence")
            seen_assessment_ids.add(item.assessment_id)
            seen_sequences.add(sequence_key)
            assessments_by_evidence[item.evidence_id].append(item)
        output: list[EvidenceListEntryDTO] = []
        for evidence_id in sorted(evidence_by_id):
            item = evidence_by_id[evidence_id]
            history = assessments_by_evidence[evidence_id]
            _require(bool(history), f"missing latest assessment for {evidence_id}")
            latest = max(history, key=lambda value: value.assessment_sequence)
            related = tuple(
                {
                    "claim_id": link.claim_id,
                    "stance": link.stance,
                }
                for link in sorted(links_by_evidence[evidence_id], key=lambda value: value.link_id)
            )
            _require(bool(related), f"missing related claim for {evidence_id}")
            lineage = {
                "task_id": item.task_id,
                "execution_id": item.execution_id,
                "raw_record_id": item.raw_record_id,
                "raw_locator": item.raw_locator,
                "raw_content_hash": item.raw_content_hash,
                "clean_content_hash": item.clean_content_hash,
                "query_provenance": _wire(item.query_provenance),
            }
            output.append(
                EvidenceListEntryDTO(
                    evidence_id=item.evidence_id,
                    source=item.source_name,
                    source_type=item.source_type,
                    source_url=item.source_url,
                    source_locator=item.source_url or item.raw_locator,
                    fetched_at=item.fetched_at,
                    content_reference=item.content_reference,
                    related_claims=related,
                    content_hash=item.clean_content_hash,
                    lineage=lineage,
                    assessment_id=latest.assessment_id,
                    assessment_version=latest.assessment_version,
                    assessment_sequence=latest.assessment_sequence,
                )
            )
        return cls(task_id, execution_id, tuple(output), generated_at)

    def to_wire(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "items": [item.to_wire() for item in self.items],
            "generated_at": str(self.generated_at),
        }

    def canonical_json(self) -> bytes:
        return _canonical_json(self)


@dataclass(frozen=True, slots=True)
class ExecutionLogEntryDTO:
    event_id: str
    timestamp: UtcInstant
    task_id: str
    execution_id: str
    step: str
    call_summary: Mapping[str, object]
    query_provenance: object
    source_fetch_result: object
    timeout_degradation: object
    reasoning_sequence: object
    artifact_generation: object
    assessment_versions: tuple[Mapping[str, str], ...]
    safe_error: object
    deadline_remaining_ms: int
    correlation: Mapping[str, object]
    schema_version: str = SCHEMA_VERSION

    def to_wire(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "timestamp": str(self.timestamp),
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "step": self.step,
            "call_summary": _sanitize(self.call_summary),
            "query_provenance": _sanitize(self.query_provenance),
            "source_fetch_result": _sanitize(self.source_fetch_result),
            "timeout_degradation": _sanitize(self.timeout_degradation),
            "reasoning_sequence": _sanitize(self.reasoning_sequence),
            "artifact_generation": _sanitize(self.artifact_generation),
            "assessment_versions": _sanitize(self.assessment_versions),
            "safe_error": _sanitize(self.safe_error),
            "deadline_remaining_ms": self.deadline_remaining_ms,
            "correlation": _sanitize(self.correlation),
        }


@dataclass(frozen=True, slots=True)
class ExecutionLogDTO:
    task_id: str
    execution_id: str
    entries: tuple[ExecutionLogEntryDTO, ...]
    generated_at: str | UtcInstant
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        _require(bool(entries), "execution log requires at least one entry")
        _require(all(item.task_id == self.task_id and item.execution_id == self.execution_id for item in entries), "execution log lineage mismatch")
        object.__setattr__(self, "entries", tuple(sorted(entries, key=lambda item: (item.timestamp.as_datetime(), item.event_id))))
        object.__setattr__(self, "generated_at", _as_utc(self.generated_at))

    @classmethod
    def from_events(
        cls,
        events: Sequence[ExecutionEventDTO],
        *,
        details_by_event: Mapping[str, Mapping[str, object]] | None = None,
        generated_at: str | UtcInstant,
    ) -> ExecutionLogDTO:
        _require(bool(events), "execution log requires events")
        task_id = events[0].task_id
        execution_id = events[0].execution_id
        details = details_by_event or {}
        entries: list[ExecutionLogEntryDTO] = []
        for event in events:
            _require(event.task_id == task_id and event.execution_id == execution_id, "event lineage mismatch")
            extra = dict(details.get(event.event_id, {}))
            timeout = extra.get("timeout_degradation")
            if timeout is None and (event.status == "degraded" or (event.error is not None and event.error.category == "timeout")):
                timeout = {"status": event.status, "error_code": None if event.error is None else event.error.code}
            source_result = extra.get("source_fetch_result")
            if source_result is None and event.step.startswith("collect"):
                source_result = event.result_summary
            reasoning = extra.get("reasoning_sequence")
            if reasoning is None and "reasoning" in event.step:
                reasoning = event.result_summary
            artifact = extra.get("artifact_generation")
            if artifact is None and ("artifact" in event.step or event.step == "publishing"):
                artifact = event.result_summary
            versions = tuple(_sanitize(extra.get("assessment_versions", ())))
            entries.append(
                ExecutionLogEntryDTO(
                    event_id=event.event_id,
                    timestamp=event.timestamp,
                    task_id=event.task_id,
                    execution_id=event.execution_id,
                    step=event.step,
                    call_summary={
                        "tool": event.tool,
                        "status": event.status,
                        "duration_ms": event.duration_ms,
                        "retry_count": event.retry_count,
                        "parameters": event.sanitized_parameters,
                        "result": event.result_summary,
                    },
                    query_provenance=extra.get("query_provenance"),
                    source_fetch_result=source_result,
                    timeout_degradation=timeout,
                    reasoning_sequence=reasoning,
                    artifact_generation=artifact,
                    assessment_versions=versions,
                    safe_error=None if event.error is None else event.error.to_wire(),
                    deadline_remaining_ms=event.deadline_remaining_ms,
                    correlation=event.correlation,
                )
            )
        return cls(task_id, execution_id, tuple(entries), generated_at)

    def with_renderer_failures(self, failures: Sequence[RendererFailureDTO]) -> ExecutionLogDTO:
        synthetic: list[ExecutionLogEntryDTO] = []
        for index, failure in enumerate(failures, start=1):
            synthetic.append(
                ExecutionLogEntryDTO(
                    event_id=f"EVT-PUBLICATION-RENDERER-{index}",
                    timestamp=self.generated_at,
                    task_id=self.task_id,
                    execution_id=self.execution_id,
                    step="artifact_generation",
                    call_summary={"tool": f"{failure.format}_renderer", "status": "degraded", "duration_ms": 0, "retry_count": 0},
                    query_provenance=None,
                    source_fetch_result=None,
                    timeout_degradation={"status": "degraded", "reason_code": failure.reason_code},
                    reasoning_sequence=None,
                    artifact_generation={"artifact_type": failure.artifact_type, "format": failure.format, "outcome": "missing"},
                    assessment_versions=(),
                    safe_error={"code": "renderer_failed", "category": "unavailable", "retryable": False, "safe_message": "Optional renderer failed"},
                    deadline_remaining_ms=0,
                    correlation={"operation_id": "OP-PUBLICATION-RENDERER", "causation_event_id": None},
                )
            )
        return replace(self, entries=(*self.entries, *synthetic))

    def with_artifact_generation(
        self,
        *,
        available: Sequence[tuple[str, str]],
        failures: Sequence[RendererFailureDTO],
    ) -> ExecutionLogDTO:
        entry = ExecutionLogEntryDTO(
            event_id="EVT-PUBLICATION-ARTIFACTS",
            timestamp=self.generated_at,
            task_id=self.task_id,
            execution_id=self.execution_id,
            step="artifact_generation",
            call_summary={
                "tool": "canonical_artifact_generator",
                "status": "degraded" if failures else "completed",
                "duration_ms": 0,
                "retry_count": 0,
            },
            query_provenance=None,
            source_fetch_result=None,
            timeout_degradation=None if not failures else {"status": "degraded", "reason_code": "renderer_failed"},
            reasoning_sequence=None,
            artifact_generation={
                "available": [f"{artifact_type}/{format_}" for artifact_type, format_ in available],
                "missing": [f"{item.artifact_type}/{item.format}" for item in failures],
                "manifest_last": True,
            },
            assessment_versions=(),
            safe_error=None,
            deadline_remaining_ms=0,
            correlation={"operation_id": "OP-PUBLICATION-ARTIFACTS", "causation_event_id": None},
        )
        return replace(self, entries=(*self.entries, entry))

    def assessment_version_map(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for entry in self.entries:
            for item in entry.assessment_versions:
                assessment_id = str(item.get("assessment_id", ""))
                version = str(item.get("assessment_version", ""))
                if assessment_id:
                    if assessment_id in result and result[assessment_id] != version:
                        raise PublicationValidationError("assessment_id has inconsistent assessment_version")
                    result[assessment_id] = version
        return result

    def canonical_jsonl(self) -> bytes:
        return b"".join(_canonical_json(item) + b"\n" for item in self.entries)


def validate_canonical_decimal_wire(value: Any, *, _key: str | None = None) -> None:
    """Reject binary floats and non-canonical decimal strings in numeric wire fields."""

    if isinstance(value, float):
        raise PublicationValidationError("binary float is forbidden on canonical wire")
    if isinstance(value, Mapping):
        for key, item in value.items():
            validate_canonical_decimal_wire(item, _key=str(key))
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            validate_canonical_decimal_wire(item, _key=_key)
        return
    decimal_key = _key in _DECIMAL_KEYS or (_key is not None and _key.endswith("_probability"))
    if decimal_key:
        if not isinstance(value, str):
            raise PublicationValidationError(f"{_key} must be a canonical decimal string")
        try:
            decimal = CanonicalDecimal(value)
            decimal.require_probability()
        except (ContractValidationError, TypeError, ValueError) as error:
            raise PublicationValidationError(f"invalid canonical decimal for {_key}") from error


def _validate_output_schema(report: FinalReportDTO, evidence_list: EvidenceListDTO, execution_log: ExecutionLogDTO) -> None:
    report_wire = report.to_wire()
    evidence_wire = evidence_list.to_wire()
    required_report = {
        "schema_version", "task_id", "execution_id", "assets", "question", "market_judgment",
        "facts", "analyses", "inferences", "conclusions", "key_evidence",
        "supporting_evidence_ids", "counter_evidence_ids", "contradictions",
        "confidence_components", "limitations", "watchpoints", "source_consistency",
        "official_dataset", "live_extension", "transition_date", "publication_outcome",
        "renderer_failures", "generated_at",
    }
    _require(required_report <= set(report_wire), "final report output schema is incomplete")
    _require(set(evidence_wire) == {"schema_version", "task_id", "execution_id", "items", "generated_at"}, "evidence list output schema is invalid")
    for item in evidence_wire["items"]:
        required_evidence = {
            "schema_version", "evidence_id", "source", "source_type", "source_url",
            "source_locator", "fetched_at", "content_reference", "related_claims",
            "content_hash", "lineage", "assessment_id", "assessment_version",
            "assessment_sequence",
        }
        _require(required_evidence == set(item), "evidence item output schema is invalid")
    for entry in execution_log.entries:
        wire = entry.to_wire()
        _require(
            {
                "schema_version", "event_id", "timestamp", "task_id", "execution_id", "step",
                "call_summary", "query_provenance", "source_fetch_result", "timeout_degradation",
                "reasoning_sequence", "artifact_generation", "assessment_versions", "safe_error",
                "deadline_remaining_ms", "correlation",
            }
            == set(wire),
            "execution log output schema is invalid",
        )


def validate_publication(
    report: FinalReportDTO,
    evidence_list: EvidenceListDTO,
    execution_log: ExecutionLogDTO,
) -> None:
    """Run all T62 pre-publication validation gates and fail closed."""

    _require(report.task_id == evidence_list.task_id == execution_log.task_id, "publication task_id mismatch")
    _require(report.execution_id == evidence_list.execution_id == execution_log.execution_id, "publication execution_id mismatch")
    _validate_output_schema(report, evidence_list, execution_log)

    evidence_ids = {item.evidence_id for item in evidence_list.items}
    analysis_ids = {item.analysis_id for item in report.analyses}
    fact_ids = {item.fact_id for item in report.facts}
    inference_ids = {item.inference_id for item in report.inferences}

    def validate_refs(evidence_refs: Sequence[str], analysis_refs: Sequence[str]) -> None:
        missing_evidence = set(evidence_refs) - evidence_ids
        missing_analysis = set(analysis_refs) - analysis_ids
        _require(not missing_evidence, f"unresolved evidence citation: {sorted(missing_evidence)}")
        _require(not missing_analysis, f"unresolved analysis citation: {sorted(missing_analysis)}")

    validate_refs(report.market_judgment.evidence_refs, report.market_judgment.analysis_refs)
    validate_refs(report.source_consistency.evidence_refs, report.source_consistency.analysis_refs)
    for fact in report.facts:
        validate_refs(fact.evidence_refs, fact.analysis_refs)
    for inference in report.inferences:
        _require(set(inference.fact_refs) <= fact_ids, "inference citation graph is unresolved")
    for conclusion in report.conclusions:
        _require(set(conclusion.fact_refs) <= fact_ids, "conclusion fact citation graph is unresolved")
        _require(set(conclusion.inference_refs) <= inference_ids, "conclusion inference citation graph is unresolved")
        traced_facts = set(conclusion.fact_refs)
        traced_facts.update(
            fact_ref
            for inference in report.inferences
            if inference.inference_id in conclusion.inference_refs
            for fact_ref in inference.fact_refs
        )
        _require(bool(traced_facts), "conclusion is not traceable")
        _require(
            all(
                bool(fact.evidence_refs or fact.analysis_refs)
                for fact in report.facts
                if fact.fact_id in traced_facts
            ),
            "conclusion is not traceable to Evidence or Analysis",
        )
    validate_refs(report.supporting_evidence_ids, ())
    validate_refs(report.counter_evidence_ids, ())
    for item in report.key_evidence:
        validate_refs(() if item.evidence_id is None else (item.evidence_id,), () if item.analysis_id is None else (item.analysis_id,))
    for contradiction in report.contradictions:
        validate_refs(contradiction.evidence_refs, ())

    for item in evidence_list.items:
        lineage = item.lineage
        _require(lineage.get("task_id") == report.task_id, "Evidence lineage task_id mismatch")
        _require(lineage.get("execution_id") == report.execution_id, "Evidence lineage execution_id mismatch")
        _require(
            bool(lineage.get("raw_record_id") and lineage.get("raw_locator") and lineage.get("query_provenance")),
            "Evidence lineage is incomplete",
        )
        _require(_HASH_PATTERN.fullmatch(str(item.content_hash)) is not None, "Evidence content_hash is invalid")

    logged_assessments = execution_log.assessment_version_map()
    for item in evidence_list.items:
        _require(item.assessment_id in logged_assessments, f"assessment_id missing from execution log: {item.assessment_id}")
        _require(logged_assessments[item.assessment_id] == item.assessment_version, "assessment_version mismatch")

    validate_canonical_decimal_wire(report.to_wire())
    validate_canonical_decimal_wire(evidence_list.to_wire())
    for entry in execution_log.entries:
        validate_canonical_decimal_wire(entry.to_wire())


def _citation_suffix(evidence_refs: Sequence[str], analysis_refs: Sequence[str]) -> str:
    references = [*evidence_refs, *analysis_refs]
    return "" if not references else " [" + ", ".join(references) + "]"


def render_markdown(report: FinalReportDTO) -> bytes:
    lines = [
        "# CryptoTrust Final Report",
        "",
        f"**Assets:** {', '.join(report.assets)}",
        f"**Question:** {report.question}",
        f"**Publication outcome:** {report.publication_outcome}",
        "",
        "## Market judgment",
        report.market_judgment.statement + _citation_suffix(report.market_judgment.evidence_refs, report.market_judgment.analysis_refs),
        "",
        "## Confirmed facts",
    ]
    lines.extend(f"- {item.statement}{_citation_suffix(item.evidence_refs, item.analysis_refs)}" for item in report.facts)
    lines.extend(["", "## Inferences"])
    lines.extend(f"- {item.statement} (confidence {item.confidence}) [facts: {', '.join(item.fact_refs)}]" for item in report.inferences)
    lines.extend(["", "## Final conclusions"])
    lines.extend(f"- {item.statement} (confidence {item.confidence})" for item in report.conclusions)
    lines.extend(["", "## Key evidence"])
    lines.extend(f"- {item.evidence_id or item.analysis_id}: {item.explanation}" for item in report.key_evidence)
    lines.extend(["", "## Supporting evidence", ", ".join(report.supporting_evidence_ids) or "None"])
    lines.extend(["", "## Counter evidence", ", ".join(report.counter_evidence_ids) or "None"])
    lines.extend(["", "## Contradictions"])
    lines.extend(f"- {item.summary} ({item.severity}): {', '.join(item.evidence_refs)}" for item in report.contradictions)
    lines.extend(["", "## Confidence", json.dumps(report.confidence_components.to_wire(), sort_keys=True)])
    lines.extend(["", "## Source consistency", report.source_consistency.statement])
    lines.extend(["", "## Limitations"])
    lines.extend(f"- {item}" for item in report.limitations)
    lines.extend(["", "## Watchpoints"])
    lines.extend(f"- {item}" for item in report.watchpoints)
    lines.extend([
        "",
        "## Market data provenance",
        f"- official_dataset: {str(report.market_data_provenance.official_dataset).lower()}",
        f"- live_extension: {str(report.market_data_provenance.live_extension).lower()}",
        f"- transition_date: {report.market_data_provenance.transition_date or 'null'}",
        "",
        "## Renderer failures",
    ])
    lines.extend(f"- {item.artifact_type}/{item.format}: {item.reason_code}" for item in report.renderer_failures)
    if not report.renderer_failures:
        lines.append("None")
    lines.extend(["", report.disclaimer, ""])
    return "\n".join(lines).encode("utf-8")


def render_html(report: FinalReportDTO) -> bytes:
    markdown = render_markdown(report).decode("utf-8")
    escaped = html.escape(markdown)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>CryptoTrust Final Report</title>"
        "</head><body><pre>" + escaped + "</pre></body></html>"
    ).encode("utf-8")


def render_evidence_csv(evidence_list: EvidenceListDTO) -> bytes:
    stream = io.StringIO(newline="")
    fieldnames = [
        "evidence_id", "source", "source_url", "source_locator", "fetched_at",
        "content_reference", "related_claims", "content_hash", "lineage",
        "schema_version", "assessment_id", "assessment_version", "assessment_sequence",
    ]
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for item in evidence_list.items:
        wire = item.to_wire()
        writer.writerow(
            {
                **{key: wire[key] for key in fieldnames if key not in {"content_reference", "related_claims", "lineage"}},
                "content_reference": json.dumps(wire["content_reference"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "related_claims": json.dumps(wire["related_claims"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "lineage": json.dumps(wire["lineage"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            }
        )
    return stream.getvalue().encode("utf-8")


Renderer = Callable[[Any], bytes]


@dataclass(frozen=True, slots=True)
class PublicationRenderers:
    markdown: Renderer = render_markdown
    html: Renderer = render_html
    csv: Renderer = render_evidence_csv


@dataclass(frozen=True, slots=True)
class ArtifactPublicationRequest:
    final_report: FinalReportDTO
    evidence_list: EvidenceListDTO
    execution_log: ExecutionLogDTO
    deadline: DeadlineDTO


@dataclass(frozen=True, slots=True)
class ArtifactPublicationResult:
    final_report: FinalReportDTO
    evidence_list: EvidenceListDTO
    execution_log: ExecutionLogDTO
    manifest: ArtifactManifestDTO
    descriptors: tuple[ArtifactDescriptorDTO, ...]
    manifest_descriptor: ArtifactDescriptorDTO
    manifest_descriptor_content_base64: str


class ArtifactPublicationService:
    """Validate, render, store all artifacts, then publish the manifest last."""

    def __init__(
        self,
        repository: ArtifactRepository,
        clock: Clock,
        *,
        renderers: PublicationRenderers | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._renderers = renderers or PublicationRenderers()

    def publish(self, request: ArtifactPublicationRequest) -> ArtifactPublicationResult:
        failures: dict[tuple[str, str], RendererFailureDTO] = {}
        optional_content: dict[tuple[str, str], tuple[str, bytes]] = {}
        final_report = request.final_report
        execution_log = request.execution_log

        renderer_specs = (
            ("markdown_report", "markdown", "text/markdown", self._renderers.markdown, "report"),
            ("html_report", "html", "text/html", self._renderers.html, "report"),
            ("csv_evidence", "csv", "text/csv", self._renderers.csv, "evidence"),
        )
        while True:
            ordered_failures = tuple(failures[key] for key in sorted(failures))
            final_report = request.final_report.with_renderer_failures(ordered_failures)
            execution_log = request.execution_log.with_renderer_failures(ordered_failures)
            optional_content = {}
            new_failure = False
            for artifact_type, format_, mime_type, renderer, source in renderer_specs:
                key = (artifact_type, format_)
                if key in failures:
                    continue
                try:
                    value = final_report if source == "report" else request.evidence_list
                    content = renderer(value)
                    if not isinstance(content, bytes):
                        raise TypeError("renderer must return bytes")
                    optional_content[key] = (mime_type, content)
                except Exception:
                    failures[key] = RendererFailureDTO(artifact_type, format_)
                    new_failure = True
            if not new_failure:
                break

        ordered_failures = tuple(failures[key] for key in sorted(failures))
        final_report = request.final_report.with_renderer_failures(ordered_failures)
        available_pairs: list[tuple[str, str]] = [("final_report", "json")]
        for key in (("markdown_report", "markdown"), ("html_report", "html")):
            if key in optional_content:
                available_pairs.append(key)
        available_pairs.append(("evidence_list", "json"))
        if ("csv_evidence", "csv") in optional_content:
            available_pairs.append(("csv_evidence", "csv"))
        available_pairs.append(("execution_log", "jsonl"))
        execution_log = (
            request.execution_log
            .with_renderer_failures(ordered_failures)
            .with_artifact_generation(available=available_pairs, failures=ordered_failures)
        )
        validate_publication(final_report, request.evidence_list, execution_log)

        artifact_payloads: list[tuple[str, str, str, bytes, UtcInstant]] = [
            ("final_report", "json", "application/json", final_report.canonical_json(), final_report.generated_at),
        ]
        for key in (("markdown_report", "markdown"), ("html_report", "html")):
            if key in optional_content:
                mime_type, content = optional_content[key]
                artifact_payloads.append((key[0], key[1], mime_type, content, final_report.generated_at))
        artifact_payloads.append(("evidence_list", "json", "application/json", request.evidence_list.canonical_json(), request.evidence_list.generated_at))
        csv_key = ("csv_evidence", "csv")
        if csv_key in optional_content:
            mime_type, content = optional_content[csv_key]
            artifact_payloads.append((csv_key[0], csv_key[1], mime_type, content, request.evidence_list.generated_at))
        artifact_payloads.append(("execution_log", "jsonl", "application/x-ndjson", execution_log.canonical_jsonl(), execution_log.generated_at))

        final_write_deadline = self._start_final_write_deadline(request.deadline)
        descriptors: list[ArtifactDescriptorDTO] = []
        for index, (artifact_type, format_, mime_type, content, generated_at) in enumerate(artifact_payloads, start=1):
            operation_id = self._operation_id(request.deadline.operation_id, f"ART{index}")
            call_deadline, local_deadline = self._repository_call_deadline(
                request.deadline,
                operation_id,
                final_write_deadline,
            )
            put_request = ArtifactPutRequestDTO(
                operation_id=operation_id,
                task_id=final_report.task_id,
                execution_id=final_report.execution_id,
                artifact_type=artifact_type,
                format=format_,
                mime_type=mime_type,
                content_schema_version=SCHEMA_VERSION,
                sha256=_sha256(content),
                size_bytes=len(content),
                generated_at=generated_at,
                content_base64=base64.b64encode(content).decode("ascii"),
                deadline=call_deadline,
            )
            response = self._invoke_repository(
                lambda put_request=put_request: self._repository.put(put_request),
                local_deadline,
                thread_name=f"artifact-put-{artifact_type}-{format_}",
            )
            descriptors.append(self._descriptor_or_raise(response))

        available = tuple(
            {
                "artifact_type": item.artifact_type,
                "format": item.format,
                "mime_type": item.mime_type,
                "content_schema_version": item.content_schema_version,
                "generated_at": str(item.generated_at),
                "artifact_id": item.artifact_id,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
            }
            for item in descriptors
        )
        missing = tuple(item.to_wire() for item in ordered_failures)
        latest_generated_at = max(
            (item.generated_at for item in descriptors),
            key=lambda value: value.as_datetime(),
        )
        manifest = ArtifactManifestDTO(
            task_id=final_report.task_id,
            execution_id=final_report.execution_id,
            publication_outcome="partial" if missing else "complete",
            available=available,
            missing=missing,
            generated_at=latest_generated_at,
        )
        manifest_content = _canonical_json(manifest)
        manifest_content_base64 = base64.b64encode(manifest_content).decode("ascii")
        manifest_operation = self._operation_id(request.deadline.operation_id, "MANIFEST")
        manifest_deadline, manifest_local_deadline = self._repository_call_deadline(
            request.deadline,
            manifest_operation,
            final_write_deadline,
        )
        manifest_request = PutManifestRequestDTO(
            operation_id=manifest_operation,
            manifest=manifest,
            sha256=_sha256(manifest_content),
            size_bytes=len(manifest_content),
            content_base64=manifest_content_base64,
            deadline=manifest_deadline,
        )
        manifest_response = self._invoke_repository(
            lambda: self._repository.put_manifest(manifest_request),
            manifest_local_deadline,
            thread_name="artifact-put-manifest",
        )
        manifest_descriptor = self._descriptor_or_raise(manifest_response)
        return ArtifactPublicationResult(
            final_report=final_report,
            evidence_list=request.evidence_list,
            execution_log=execution_log,
            manifest=manifest,
            descriptors=tuple(descriptors),
            manifest_descriptor=manifest_descriptor,
            manifest_descriptor_content_base64=manifest_content_base64,
        )

    @staticmethod
    def _operation_id(base: str, suffix: str) -> str:
        candidate = f"{base}:{suffix}"
        if len(candidate) <= 127:
            return candidate
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:12].upper()
        return f"OP-PUB-{digest}:{suffix}"

    def _clock_snapshot(self, base_operation_id: str, suffix: str):
        try:
            monotonic = self._clock.monotonic_ms(
                ClockReadRequestDTO(self._operation_id(base_operation_id, f"CLOCK:{suffix}:MONO"))
            )
            if isinstance(monotonic, ErrorResultDTO):
                raise PublicationError(monotonic.error.code)
            utc = self._clock.now_utc(
                ClockReadRequestDTO(self._operation_id(base_operation_id, f"CLOCK:{suffix}:UTC"))
            )
            if isinstance(utc, ErrorResultDTO):
                raise PublicationError(utc.error.code)
            return monotonic, utc
        except PublicationError:
            raise
        except BaseException:
            raise PublicationError("unexpected_provider_error") from None

    def _start_final_write_deadline(self, source: DeadlineDTO) -> LocalDeadline:
        monotonic, utc = self._clock_snapshot(source.operation_id, "FINAL")
        try:
            return build_local_deadline(
                source,
                provider_timeout_ms=FINAL_WRITE_HARD_LIMIT_MS,
                now_utc=utc.utc.as_datetime(),
                now_monotonic_ms=monotonic.monotonic_ms,
                runtime_id=monotonic.runtime_id,
            )
        except DeadlineExceededError:
            raise PublicationError("deadline_exceeded") from None
        except BaseException:
            raise PublicationError("unexpected_provider_error") from None

    def _repository_call_deadline(
        self,
        source: DeadlineDTO,
        operation_id: str,
        final_write_deadline: LocalDeadline,
    ) -> tuple[DeadlineDTO, LocalDeadline]:
        monotonic, utc = self._clock_snapshot(source.operation_id, operation_id.removeprefix("OP-"))
        if monotonic.runtime_id != final_write_deadline.runtime_id:
            raise PublicationError("unexpected_provider_error")
        final_remaining_ms = final_write_deadline.deadline_monotonic_ms - monotonic.monotonic_ms
        if final_remaining_ms <= 0:
            raise PublicationError("deadline_exceeded")
        budget_ms = min(
            REPOSITORY_TIMEOUT_MS,
            source.budget_ms,
            final_remaining_ms,
        )
        if budget_ms <= 0:
            raise PublicationError("deadline_exceeded")
        deadline = DeadlineDTO(
            schema_version=source.schema_version,
            operation_id=operation_id,
            deadline_at_utc=source.deadline_at_utc,
            budget_ms=budget_ms,
            sent_at_utc=utc.utc,
            safety_margin_ms=source.safety_margin_ms,
        )
        try:
            local = build_local_deadline(
                deadline,
                provider_timeout_ms=REPOSITORY_TIMEOUT_MS,
                now_utc=utc.utc.as_datetime(),
                now_monotonic_ms=monotonic.monotonic_ms,
                runtime_id=monotonic.runtime_id,
            )
        except DeadlineExceededError:
            raise PublicationError("deadline_exceeded") from None
        except BaseException:
            raise PublicationError("unexpected_provider_error") from None
        return deadline, local

    @staticmethod
    def _invoke_repository(
        call: Callable[[], object],
        local_deadline: LocalDeadline,
        *,
        thread_name: str,
    ) -> object:
        completed = Event()
        payload: list[tuple[bool, object | None]] = []

        def invoke() -> None:
            try:
                payload.append((True, call()))
            except BaseException:
                payload.append((False, None))
            finally:
                completed.set()

        Thread(target=invoke, name=thread_name, daemon=True).start()
        if not completed.wait(local_deadline.effective_timeout_ms / 1_000):
            raise PublicationError("deadline_exceeded")
        succeeded, result = payload[0]
        if not succeeded:
            raise PublicationError("unexpected_provider_error")
        return result

    @staticmethod
    def _descriptor_or_raise(response: object) -> ArtifactDescriptorDTO:
        if isinstance(response, ErrorResultDTO):
            raise PublicationError(response.error.code)
        if not isinstance(response, ArtifactDescriptorDTO):
            raise PublicationError("unexpected_provider_error")
        return response


__all__ = (
    "ArtifactPublicationRequest",
    "ArtifactPublicationResult",
    "ArtifactPublicationService",
    "CitedStatementDTO",
    "EvidenceListDTO",
    "EvidenceListEntryDTO",
    "ExecutionLogDTO",
    "ExecutionLogEntryDTO",
    "FinalReportDTO",
    "KeyEvidenceDTO",
    "MarketDataProvenanceDTO",
    "PublicationError",
    "PublicationRenderers",
    "PublicationValidationError",
    "RendererFailureDTO",
    "render_evidence_csv",
    "render_html",
    "render_markdown",
    "validate_canonical_decimal_wire",
    "validate_publication",
)
