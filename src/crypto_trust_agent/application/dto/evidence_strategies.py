"""Immutable DTOs for Core-internal evidence strategy boundaries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit

from crypto_trust_agent.domain.primitives import CanonicalDecimal, UtcInstant
from crypto_trust_agent.domain.trust import Contradiction

SCHEMA_VERSION = "1.0.0"
_COMPONENT_NAMES = (
    "source_trust",
    "relevance",
    "freshness",
    "independence",
    "consistency",
)


def _task(value: str) -> None:
    if not isinstance(value, str) or not value.startswith("TASK-"):
        raise ValueError("invalid task_id")


def _ruleset(value: str) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValueError("invalid ruleset_version")


def _ids(values: tuple[str, ...]) -> tuple[str, ...]:
    result = tuple(values)
    if not result or len(result) != len(set(result)) or any(not value for value in result):
        raise ValueError("IDs must be non-empty and unique")
    return result


@dataclass(frozen=True, slots=True)
class EvidenceStrategyItemDTO:
    evidence_id: str
    task_id: str
    canonical_url: str | None
    content_hash: str
    title: str
    event_entities: tuple[str, ...]
    event_time: str | UtcInstant
    stance: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION or not self.evidence_id.startswith("EVID-"):
            raise ValueError("invalid evidence strategy item")
        _task(self.task_id)
        if self.canonical_url is not None:
            parsed = urlsplit(self.canonical_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("canonical_url must be HTTPS")
        if not re.fullmatch(r"^sha256:[0-9a-f]{64}$", self.content_hash):
            raise ValueError("invalid content_hash")
        if not isinstance(self.title, str) or not self.title:
            raise ValueError("invalid title")
        entities = tuple(self.event_entities)
        if not entities or any(not isinstance(value, str) or not value for value in entities):
            raise ValueError("invalid event_entities")
        if self.stance not in {"supports", "contradicts", "context"}:
            raise ValueError("invalid stance")
        event_time = self.event_time if isinstance(self.event_time, UtcInstant) else UtcInstant(self.event_time)
        object.__setattr__(self, "event_entities", entities)
        object.__setattr__(self, "event_time", event_time)


@dataclass(frozen=True, slots=True)
class DuplicateGroupDTO:
    group_id: str
    member_ids: tuple[str, ...]
    representative_id: str

    def __post_init__(self) -> None:
        members = _ids(self.member_ids)
        if self.representative_id not in members:
            raise ValueError("representative must be a group member")
        object.__setattr__(self, "member_ids", members)


@dataclass(frozen=True, slots=True)
class DuplicateDetectionRequestDTO:
    task_id: str
    ruleset_version: str
    items: tuple[EvidenceStrategyItemDTO, ...]

    def __post_init__(self) -> None:
        _task(self.task_id)
        _ruleset(self.ruleset_version)
        items = tuple(self.items)
        if not items or any(item.task_id != self.task_id for item in items):
            raise ValueError("duplicate request must be task-scoped")
        if len({item.evidence_id for item in items}) != len(items):
            raise ValueError("duplicate evidence_id")
        object.__setattr__(self, "items", items)


@dataclass(frozen=True, slots=True)
class DuplicateDetectionResultDTO:
    ruleset_version: str
    groups: tuple[DuplicateGroupDTO, ...]
    retained_evidence_ids: tuple[str, ...]
    counter_evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _ruleset(self.ruleset_version)
        retained = _ids(self.retained_evidence_ids)
        counters = tuple(self.counter_evidence_ids)
        if any(value not in retained for value in counters):
            raise ValueError("counter evidence must remain retained")
        object.__setattr__(self, "groups", tuple(self.groups))
        object.__setattr__(self, "retained_evidence_ids", retained)
        object.__setattr__(self, "counter_evidence_ids", counters)


@dataclass(frozen=True, slots=True)
class IndependenceGroupDTO:
    group_id: str
    member_ids: tuple[str, ...]
    representative_id: str
    corroboration_count: int = 1

    def __post_init__(self) -> None:
        members = _ids(self.member_ids)
        if self.representative_id not in members or self.corroboration_count != 1:
            raise ValueError("an independence group contributes exactly one corroboration")
        object.__setattr__(self, "member_ids", members)


@dataclass(frozen=True, slots=True)
class IndependenceGroupingRequestDTO:
    task_id: str
    ruleset_version: str
    items: tuple[EvidenceStrategyItemDTO, ...]
    duplicate_groups: tuple[DuplicateGroupDTO, ...]

    def __post_init__(self) -> None:
        _task(self.task_id)
        _ruleset(self.ruleset_version)
        items = tuple(self.items)
        groups = tuple(self.duplicate_groups)
        if any(item.task_id != self.task_id for item in items):
            raise ValueError("independence request must be task-scoped")
        item_ids = {item.evidence_id for item in items}
        grouped_ids = [member for group in groups for member in group.member_ids]
        if item_ids != set(grouped_ids) or len(grouped_ids) != len(set(grouped_ids)):
            raise ValueError("duplicate groups must partition request items")
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "duplicate_groups", groups)


@dataclass(frozen=True, slots=True)
class IndependenceGroupingResultDTO:
    ruleset_version: str
    groups: tuple[IndependenceGroupDTO, ...]
    corroboration_count: int

    def __post_init__(self) -> None:
        groups = tuple(self.groups)
        if self.corroboration_count != len(groups):
            raise ValueError("corroboration count must equal independence group count")
        object.__setattr__(self, "groups", groups)


@dataclass(frozen=True, slots=True)
class TrustScoringRequestDTO:
    task_id: str
    ruleset_version: str
    items: tuple[EvidenceStrategyItemDTO, ...]
    independence_groups: tuple[IndependenceGroupDTO, ...]

    def __post_init__(self) -> None:
        _task(self.task_id)
        _ruleset(self.ruleset_version)
        items = tuple(self.items)
        groups = tuple(self.independence_groups)
        item_ids = {item.evidence_id for item in items}
        grouped_ids = {member for group in groups for member in group.member_ids}
        if any(item.task_id != self.task_id for item in items) or item_ids != grouped_ids:
            raise ValueError("trust request must be task-scoped and fully grouped")
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "independence_groups", groups)


@dataclass(frozen=True, slots=True)
class TrustComponentDTO:
    evidence_id: str
    source_trust: CanonicalDecimal | str
    relevance: CanonicalDecimal | str
    freshness: CanonicalDecimal | str
    independence: CanonicalDecimal | str
    consistency: CanonicalDecimal | str

    def __post_init__(self) -> None:
        if not self.evidence_id.startswith("EVID-"):
            raise ValueError("invalid evidence_id")
        for name in _COMPONENT_NAMES:
            value = getattr(self, name)
            decimal = value if isinstance(value, CanonicalDecimal) else CanonicalDecimal(value)
            object.__setattr__(self, name, decimal.require_probability())


@dataclass(frozen=True, slots=True)
class TrustScoringResultDTO:
    ruleset_version: str
    items: tuple[TrustComponentDTO, ...]

    def __post_init__(self) -> None:
        _ruleset(self.ruleset_version)
        items = tuple(self.items)
        if not items or len({item.evidence_id for item in items}) != len(items):
            raise ValueError("invalid trust result")
        object.__setattr__(self, "items", items)


@dataclass(frozen=True, slots=True)
class ContradictionCandidateDTO:
    ref_id: str
    task_id: str
    conflict_type: str
    conflict_key: str
    value: str

    def __post_init__(self) -> None:
        _task(self.task_id)
        if not self.ref_id or not self.conflict_key or not self.value:
            raise ValueError("invalid contradiction candidate")
        if self.conflict_type not in {"numeric", "temporal", "source", "narrative", "signal", "status"}:
            raise ValueError("invalid contradiction type")


@dataclass(frozen=True, slots=True)
class ContradictionDetectionRequestDTO:
    task_id: str
    ruleset_version: str
    candidates: tuple[ContradictionCandidateDTO, ...]

    def __post_init__(self) -> None:
        _task(self.task_id)
        _ruleset(self.ruleset_version)
        candidates = tuple(self.candidates)
        if any(item.task_id != self.task_id for item in candidates):
            raise ValueError("contradiction request must be task-scoped")
        object.__setattr__(self, "candidates", candidates)


@dataclass(frozen=True, slots=True)
class ContradictionDetectionResultDTO:
    ruleset_version: str
    items: tuple[Contradiction, ...]

    def __post_init__(self) -> None:
        _ruleset(self.ruleset_version)
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True, slots=True)
class ConfidenceCompositionRequestDTO:
    task_id: str
    ruleset_version: str
    trust_items: tuple[TrustComponentDTO, ...]
    contradiction_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _task(self.task_id)
        _ruleset(self.ruleset_version)
        trust_items = tuple(self.trust_items)
        if not trust_items:
            raise ValueError("confidence requires trust components")
        object.__setattr__(self, "trust_items", trust_items)
        object.__setattr__(self, "contradiction_ids", tuple(self.contradiction_ids))


@dataclass(frozen=True, slots=True)
class ConfidenceCompositionResultDTO:
    ruleset_version: str
    score: CanonicalDecimal | str
    component_scores: Mapping[str, CanonicalDecimal | str]
    contradiction_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _ruleset(self.ruleset_version)
        score = self.score if isinstance(self.score, CanonicalDecimal) else CanonicalDecimal(self.score)
        components = {
            name: value if isinstance(value, CanonicalDecimal) else CanonicalDecimal(value)
            for name, value in self.component_scores.items()
        }
        if set(components) != set(_COMPONENT_NAMES):
            raise ValueError("all five confidence components must be visible")
        score.require_probability()
        for value in components.values():
            value.require_probability()
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "component_scores", MappingProxyType(components))
        object.__setattr__(self, "contradiction_ids", tuple(self.contradiction_ids))


__all__ = tuple(name for name in globals() if name.endswith("DTO"))
