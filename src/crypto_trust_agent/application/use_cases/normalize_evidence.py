"""Deterministic RawRecord + extraction normalization into immutable Evidence."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass
from datetime import timedelta
from threading import RLock
from types import MappingProxyType
from typing import Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.evidence_extractor import ExtractionResultDTO
from crypto_trust_agent.application.dto.repositories import (
    AppendClaimLinksRequestDTO,
    AppendEvidenceRequestDTO,
    ClockReadRequestDTO,
    EvidenceClaimLinkDTO,
    EvidenceDTO,
)
from crypto_trust_agent.application.dto.source_collector import RawRecordDTO
from crypto_trust_agent.application.ports import Clock, EvidenceRepository
from crypto_trust_agent.domain.evidence import (
    ContentOffset,
    ContentReference,
    Evidence,
    EvidenceClaimLink,
    QueryProvenance,
    Stance,
)

IdentifierFactory = Callable[[str], str]
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ID_PATTERNS = {
    "EVID-": re.compile(r"^EVID-[A-Za-z0-9._:-]{1,123}$"),
    "LINK-": re.compile(r"^LINK-.+"),
    "CLAIM-": re.compile(r"^CLAIM-.+"),
    "OP-CLOCK-EVIDENCE-": re.compile(r"^OP-[A-Z0-9][A-Z0-9._:-]{0,126}$"),
    "OP-APPEND-EVIDENCE-": re.compile(r"^OP-[A-Z0-9][A-Z0-9._:-]{0,126}$"),
    "OP-APPEND-LINKS-": re.compile(r"^OP-[A-Z0-9][A-Z0-9._:-]{0,126}$"),
}


class EvidenceNormalizationRejected(RuntimeError):
    """Safe application-level rejection; never contains source content."""

    def __init__(self, code: str) -> None:
        if not _SAFE_CODE.fullmatch(code):
            raise ValueError("invalid safe rejection code")
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class NormalizeEvidenceCommand:
    operation_id: str
    task_id: str
    execution_id: str
    raw_record_id: str
    raw_content_hash: str
    raw_record: RawRecordDTO
    extraction: ExtractionResultDTO
    claim_stances: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_stances", MappingProxyType(dict(self.claim_stances)))


@dataclass(frozen=True, slots=True)
class NormalizedEvidenceItem:
    extracted_claim_id: str
    evidence: EvidenceDTO
    link: EvidenceClaimLinkDTO


@dataclass(frozen=True, slots=True)
class NormalizeEvidenceResult:
    operation_id: str
    items: tuple[NormalizedEvidenceItem, ...]


def canonicalize_source_url(value: str) -> str:
    """Pure canonicalization: HTTPS, normalized host/path, sorted query, no fragment."""

    if not isinstance(value, str) or len(value) > 2_048:
        raise EvidenceNormalizationRejected("invalid_source_url")
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise EvidenceNormalizationRejected("invalid_source_url")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port
    except (UnicodeError, ValueError) as error:
        raise EvidenceNormalizationRejected("invalid_source_url") from error
    netloc = hostname if port in (None, 443) else f"{hostname}:{port}"
    path = posixpath.normpath(parsed.path or "/")
    if not path.startswith("/"):
        path = "/" + path
    if parsed.path.endswith("/") and not path.endswith("/"):
        path += "/"
    if path == "/":
        path = ""
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    return urlunsplit(("https", netloc, path, query, ""))


class NormalizeEvidenceUseCase:
    """Validate lineage/content, build immutable entities, then append through the Port."""

    def __init__(
        self,
        evidence_repository: EvidenceRepository,
        clock: Clock,
        identifier_factory: IdentifierFactory,
    ) -> None:
        self._evidence = evidence_repository
        self._clock = clock
        self._new_id = identifier_factory
        self._lock = RLock()
        self._replays: dict[str, tuple[str, NormalizeEvidenceResult]] = {}

    def execute(self, command: NormalizeEvidenceCommand) -> NormalizeEvidenceResult:
        identity = self._identity(command)
        with self._lock:
            replay = self._replays.get(command.operation_id)
            if replay is not None:
                if replay[0] != identity:
                    raise EvidenceNormalizationRejected("operation_payload_conflict")
                return replay[1]

            self._validate_lineage(command)
            now = self._read_now()
            normalized = self._normalize(command, now)
            for item in normalized:
                operation_id = self._id("OP-APPEND-EVIDENCE-")
                saved = self._evidence.append_evidence(
                    AppendEvidenceRequestDTO(
                        operation_id=operation_id,
                        expected_task_id=command.task_id,
                        evidence=item.evidence,
                        deadline=self._deadline(operation_id, now),
                    )
                )
                if isinstance(saved, ErrorResultDTO):
                    raise EvidenceNormalizationRejected(saved.error.code)
            links_operation = self._id("OP-APPEND-LINKS-")
            linked = self._evidence.append_claim_links(
                AppendClaimLinksRequestDTO(
                    operation_id=links_operation,
                    expected_task_id=command.task_id,
                    items=tuple(item.link for item in normalized),
                    deadline=self._deadline(links_operation, now),
                )
            )
            if isinstance(linked, ErrorResultDTO):
                raise EvidenceNormalizationRejected(linked.error.code)
            result = NormalizeEvidenceResult(command.operation_id, tuple(normalized))
            self._replays[command.operation_id] = (identity, result)
            return result

    def _validate_lineage(self, command: NormalizeEvidenceCommand) -> None:
        raw = command.raw_record
        if not command.task_id.startswith("TASK-") or raw.task_id != command.task_id:
            raise EvidenceNormalizationRejected("task_lineage_mismatch")
        if not command.execution_id.startswith("EXEC-") or raw.execution_id != command.execution_id:
            raise EvidenceNormalizationRejected("execution_lineage_mismatch")
        if (
            not command.raw_record_id.startswith("RAW-")
            or raw.raw_record_id != command.raw_record_id
            or command.extraction.raw_record_id != command.raw_record_id
        ):
            raise EvidenceNormalizationRejected("raw_record_lineage_mismatch")
        if raw.content_hash != command.raw_content_hash:
            raise EvidenceNormalizationRejected("raw_content_hash_mismatch")
        actual_clean_hash = "sha256:" + hashlib.sha256(raw.clean_content.encode("utf-8")).hexdigest()
        if actual_clean_hash != raw.clean_content_hash:
            raise EvidenceNormalizationRejected("clean_content_hash_mismatch")
        if command.extraction.outcome != "valid":
            raise EvidenceNormalizationRejected("extraction_not_eligible")
        if not command.extraction.claims:
            raise EvidenceNormalizationRejected("extraction_has_no_claims")
        claim_ids = {claim.extracted_claim_id for claim in command.extraction.claims}
        if set(command.claim_stances) != claim_ids:
            raise EvidenceNormalizationRejected("claim_stance_mismatch")
        try:
            for stance in command.claim_stances.values():
                Stance(stance)
        except ValueError as error:
            raise EvidenceNormalizationRejected("invalid_claim_stance") from error

    def _normalize(self, command: NormalizeEvidenceCommand, now) -> list[NormalizedEvidenceItem]:
        raw = command.raw_record
        source_url = canonicalize_source_url(raw.canonical_url)
        provenance = QueryProvenance(
            collector=raw.query_provenance.collector,
            query=raw.query_provenance.query,
            parameters=raw.query_provenance.parameters,
            plan_job_id=raw.plan_job_id,
        )
        items: list[NormalizedEvidenceItem] = []
        for claim in command.extraction.claims:
            start = raw.clean_content.find(claim.quote)
            if start < 0:
                raise EvidenceNormalizationRejected("quote_not_in_clean_content")
            end = start + len(claim.quote)
            try:
                evidence = Evidence(
                    evidence_id=self._id("EVID-"),
                    task_id=command.task_id,
                    execution_id=command.execution_id,
                    raw_record_id=command.raw_record_id,
                    source_name=raw.source_name,
                    source_type=raw.source_type,
                    source_url=source_url,
                    published_at=raw.published_at,
                    fetched_at=raw.fetched_at,
                    content_reference=ContentReference(
                        kind="quote",
                        value=claim.quote,
                        offset=ContentOffset(start, end),
                        unit="unicode_scalar",
                    ),
                    raw_locator=raw.raw_locator,
                    raw_content_hash=raw.content_hash,
                    clean_content_hash=raw.clean_content_hash,
                    query_provenance=provenance,
                    validation_status="active",
                    created_at=now,
                )
                link = EvidenceClaimLink.for_evidence(
                    link_id=self._id("LINK-"),
                    task_id=command.task_id,
                    evidence=evidence,
                    claim_id=self._id("CLAIM-"),
                    stance=Stance(command.claim_stances[claim.extracted_claim_id]),
                    created_at=now,
                )
            except EvidenceNormalizationRejected:
                raise
            except (TypeError, ValueError) as error:
                raise EvidenceNormalizationRejected("invalid_generated_id") from error
            items.append(
                NormalizedEvidenceItem(
                    extracted_claim_id=claim.extracted_claim_id,
                    evidence=self._evidence_dto(evidence),
                    link=EvidenceClaimLinkDTO(
                        link.link_id,
                        link.task_id,
                        link.evidence_id,
                        link.claim_id,
                        link.stance.value,
                        link.created_at,
                        link.schema_version,
                    ),
                )
            )
        return items

    @staticmethod
    def _evidence_dto(evidence: Evidence) -> EvidenceDTO:
        reference = evidence.content_reference
        offset = None if reference.offset is None else {
            "start": reference.offset.start,
            "end": reference.offset.end,
        }
        return EvidenceDTO(
            evidence_id=evidence.evidence_id,
            task_id=evidence.task_id,
            execution_id=evidence.execution_id,
            raw_record_id=evidence.raw_record_id,
            source_name=evidence.source_name,
            source_type=evidence.source_type.value,
            source_url=evidence.source_url,
            published_at=evidence.published_at,
            fetched_at=evidence.fetched_at,
            content_reference={
                "kind": reference.kind.value,
                "value": reference.value,
                "offset": offset,
                "unit": None if reference.unit is None else reference.unit.value,
            },
            raw_locator=evidence.raw_locator,
            raw_content_hash=evidence.raw_content_hash,
            clean_content_hash=evidence.clean_content_hash,
            query_provenance={
                "collector": evidence.query_provenance.collector,
                "query": evidence.query_provenance.query,
                "parameters": evidence.query_provenance.parameters,
                "plan_job_id": evidence.query_provenance.plan_job_id,
            },
            validation_status=evidence.validation_status.value,
            created_at=evidence.created_at,
            schema_version=evidence.schema_version,
        )

    def _read_now(self):
        operation_id = self._id("OP-CLOCK-EVIDENCE-")
        result = self._clock.now_utc(ClockReadRequestDTO(operation_id))
        if isinstance(result, ErrorResultDTO):
            raise EvidenceNormalizationRejected(result.error.code)
        return result.utc

    def _id(self, prefix: str) -> str:
        try:
            value = self._new_id(prefix)
        except Exception as error:
            raise EvidenceNormalizationRejected("invalid_generated_id") from error
        pattern = _ID_PATTERNS[prefix]
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise EvidenceNormalizationRejected("invalid_generated_id")
        return value

    @staticmethod
    def _identity(command: NormalizeEvidenceCommand) -> str:
        value = {
            "task_id": command.task_id,
            "execution_id": command.execution_id,
            "raw_record_id": command.raw_record_id,
            "raw_content_hash": command.raw_content_hash,
            "raw_record": command.raw_record.to_wire(),
            "extraction": command.extraction.to_wire(),
            "claim_stances": dict(command.claim_stances),
        }
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _deadline(operation_id: str, now) -> DeadlineDTO:
        return DeadlineDTO(
            "1.0.0",
            operation_id,
            (now.as_datetime() + timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
            5_000,
            now,
            100,
        )


__all__ = (
    "EvidenceNormalizationRejected",
    "NormalizeEvidenceCommand",
    "NormalizeEvidenceResult",
    "NormalizeEvidenceUseCase",
    "NormalizedEvidenceItem",
    "canonicalize_source_url",
)
