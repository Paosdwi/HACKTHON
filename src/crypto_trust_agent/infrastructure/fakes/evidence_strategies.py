"""Transparent deterministic strategy fakes; never production policy."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_EVEN

from crypto_trust_agent.application.dto.evidence_strategies import (
    ConfidenceCompositionRequestDTO,
    ConfidenceCompositionResultDTO,
    ContradictionDetectionRequestDTO,
    ContradictionDetectionResultDTO,
    DuplicateDetectionRequestDTO,
    DuplicateDetectionResultDTO,
    DuplicateGroupDTO,
    IndependenceGroupDTO,
    IndependenceGroupingRequestDTO,
    IndependenceGroupingResultDTO,
    TrustComponentDTO,
    TrustScoringRequestDTO,
    TrustScoringResultDTO,
)
from crypto_trust_agent.domain.trust import Contradiction

_COMPONENT_NAMES = (
    "source_trust",
    "relevance",
    "freshness",
    "independence",
    "consistency",
)


def _canonical(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


class _FakeStrategy:
    non_production = True

    def __init__(self, ruleset_version: str) -> None:
        if not isinstance(ruleset_version, str) or not ruleset_version.startswith("fake-"):
            raise ValueError("fake strategy ruleset must be explicitly named fake-*")
        self.ruleset_version = ruleset_version

    def _validate_ruleset(self, value: str) -> None:
        if value != self.ruleset_version:
            raise ValueError("strategy ruleset mismatch")


class FakeDuplicateDetectionStrategy(_FakeStrategy):
    """Exact URL/hash union only; no production similarity threshold or allowlist."""

    def detect(self, request: DuplicateDetectionRequestDTO) -> DuplicateDetectionResultDTO:
        self._validate_ruleset(request.ruleset_version)
        parent = {item.evidence_id: item.evidence_id for item in request.items}

        def find(value: str) -> str:
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        def union(left: str, right: str) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[max(left_root, right_root)] = min(left_root, right_root)

        ordered = sorted(request.items, key=lambda item: item.evidence_id)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                same_url = left.canonical_url is not None and left.canonical_url == right.canonical_url
                if same_url or left.content_hash == right.content_hash:
                    union(left.evidence_id, right.evidence_id)
        grouped: dict[str, list[str]] = defaultdict(list)
        for item in ordered:
            grouped[find(item.evidence_id)].append(item.evidence_id)
        groups = []
        for members in sorted((tuple(sorted(values)) for values in grouped.values())):
            digest = hashlib.sha256("|".join(members).encode("utf-8")).hexdigest()[:16]
            groups.append(DuplicateGroupDTO(f"DUP-{digest}", members, members[0]))
        retained = tuple(item.evidence_id for item in ordered)
        counters = tuple(item.evidence_id for item in ordered if item.stance == "contradicts")
        return DuplicateDetectionResultDTO(
            self.ruleset_version, tuple(groups), retained, counters
        )


class FakeIndependenceGroupingStrategy(_FakeStrategy):
    """Uses duplicate partitions directly and an evidence-ID tie-break for fake tests."""

    def group(self, request: IndependenceGroupingRequestDTO) -> IndependenceGroupingResultDTO:
        self._validate_ruleset(request.ruleset_version)
        groups = tuple(
            IndependenceGroupDTO(
                f"IND-{group.group_id[4:]}",
                group.member_ids,
                min(group.member_ids),
                1,
            )
            for group in sorted(request.duplicate_groups, key=lambda item: item.member_ids)
        )
        return IndependenceGroupingResultDTO(self.ruleset_version, groups, len(groups))


class FakeTrustComponentStrategy(_FakeStrategy):
    """Fixed visible fake components; not a production trust/confidence formula."""

    def score(self, request: TrustScoringRequestDTO) -> TrustScoringResultDTO:
        self._validate_ruleset(request.ruleset_version)
        representatives = {group.representative_id for group in request.independence_groups}
        items = tuple(
            TrustComponentDTO(
                item.evidence_id,
                "0.6",
                "0.7",
                "0.8",
                "1" if item.evidence_id in representatives else "0",
                "0.9",
            )
            for item in sorted(request.items, key=lambda value: value.evidence_id)
        )
        return TrustScoringResultDTO(self.ruleset_version, items)


class FakeContradictionDetectionStrategy(_FakeStrategy):
    """Pairs differing explicit values; it does not infer semantic contradictions."""

    def detect(self, request: ContradictionDetectionRequestDTO) -> ContradictionDetectionResultDTO:
        self._validate_ruleset(request.ruleset_version)
        groups: dict[tuple[str, str], list[object]] = defaultdict(list)
        for candidate in request.candidates:
            groups[(candidate.conflict_type, candidate.conflict_key)].append(candidate)
        contradictions = []
        for (conflict_type, conflict_key), candidates in sorted(groups.items()):
            ordered = sorted(candidates, key=lambda item: item.ref_id)
            pair = next(
                (
                    (left, right)
                    for index, left in enumerate(ordered)
                    for right in ordered[index + 1 :]
                    if left.value != right.value
                ),
                None,
            )
            if pair is None:
                continue
            left, right = pair
            digest = hashlib.sha256(
                f"{conflict_type}|{conflict_key}|{left.ref_id}|{right.ref_id}".encode("utf-8")
            ).hexdigest()[:16]
            contradictions.append(
                Contradiction(
                    f"CON-{digest}",
                    request.task_id,
                    conflict_type,
                    left.ref_id,
                    right.ref_id,
                    self.ruleset_version,
                )
            )
        return ContradictionDetectionResultDTO(self.ruleset_version, tuple(contradictions))


class FakeConfidenceCompositionStrategy(_FakeStrategy):
    """Visible arithmetic fixture: component mean minus 0.05 per contradiction."""

    def compose(self, request: ConfidenceCompositionRequestDTO) -> ConfidenceCompositionResultDTO:
        self._validate_ruleset(request.ruleset_version)
        count = Decimal(len(request.trust_items))
        components = {}
        quantum = Decimal("0.000001")
        for name in _COMPONENT_NAMES:
            mean = sum(
                (getattr(item, name).as_decimal() for item in request.trust_items),
                Decimal("0"),
            ) / count
            components[name] = mean.quantize(quantum, rounding=ROUND_HALF_EVEN)
        base = sum(components.values(), Decimal("0")) / Decimal(len(_COMPONENT_NAMES))
        score = max(Decimal("0"), base - Decimal("0.05") * len(request.contradiction_ids))
        score = score.quantize(quantum, rounding=ROUND_HALF_EVEN)
        return ConfidenceCompositionResultDTO(
            self.ruleset_version,
            _canonical(score),
            {name: _canonical(value) for name, value in components.items()},
            request.contradiction_ids,
        )


__all__ = (
    "FakeConfidenceCompositionStrategy",
    "FakeContradictionDetectionStrategy",
    "FakeDuplicateDetectionStrategy",
    "FakeIndependenceGroupingStrategy",
    "FakeTrustComponentStrategy",
)
