"""Application-owned EvidenceExtractor 2.0.0 Port and version negotiation."""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from crypto_trust_agent.application.dto.common import ErrorResultDTO
from crypto_trust_agent.application.dto.evidence_extractor import (
    ExtractRequestDTO,
    ExtractionResultDTO,
    ExtractorHealthCheckRequestDTO,
)
from crypto_trust_agent.application.dto.evidence_extractor_v2 import RepairRequestV2DTO
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO
from crypto_trust_agent.domain.primitives import ContractValidationError

EVIDENCE_EXTRACTOR_V1 = "1.0.0"
EVIDENCE_EXTRACTOR_V2 = "2.0.0"
APPROVED_EVIDENCE_EXTRACTOR_VERSIONS = (
    EVIDENCE_EXTRACTOR_V1,
    EVIDENCE_EXTRACTOR_V2,
)
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def _validate_declared_versions(values: tuple[str, ...] | list[str]) -> set[str]:
    declared = tuple(values)
    if not declared:
        raise ContractValidationError("at least one EvidenceExtractor version is required")
    validated: set[str] = set()
    for value in declared:
        matched = _SEMVER.fullmatch(value) if isinstance(value, str) else None
        if matched is None:
            raise ContractValidationError("invalid EvidenceExtractor contract version")
        if int(matched.group(1)) not in {1, 2}:
            raise ContractValidationError("unsupported EvidenceExtractor major version")
        if value not in APPROVED_EVIDENCE_EXTRACTOR_VERSIONS:
            raise ContractValidationError("unsupported EvidenceExtractor contract version")
        validated.add(value)
    return validated


def negotiate_evidence_extractor_version(
    core_supported: tuple[str, ...] | list[str],
    adapter_supported: tuple[str, ...] | list[str],
    *,
    require_successful_repair: bool = False,
) -> str:
    """Select an explicit common version; successful repair always requires v2."""

    core = _validate_declared_versions(core_supported)
    adapter = _validate_declared_versions(adapter_supported)
    common = core & adapter
    if require_successful_repair:
        if EVIDENCE_EXTRACTOR_V2 not in common:
            raise ContractValidationError(
                "successful EvidenceExtractor repair requires contract 2.0.0"
            )
        return EVIDENCE_EXTRACTOR_V2
    if EVIDENCE_EXTRACTOR_V2 in common:
        return EVIDENCE_EXTRACTOR_V2
    if EVIDENCE_EXTRACTOR_V1 in common:
        return EVIDENCE_EXTRACTOR_V1
    raise ContractValidationError("no common EvidenceExtractor contract version")


@runtime_checkable
class EvidenceExtractorV2(Protocol):
    """Full v2 Port: unchanged v1 extract/health and authoritative v2 repair."""

    def extract(self, request: ExtractRequestDTO) -> ExtractionResultDTO | ErrorResultDTO: ...

    def repair(self, request: RepairRequestV2DTO) -> ExtractionResultDTO | ErrorResultDTO: ...

    def health_check(
        self,
        request: ExtractorHealthCheckRequestDTO,
    ) -> ProviderHealthDTO | ErrorResultDTO: ...


__all__ = (
    "APPROVED_EVIDENCE_EXTRACTOR_VERSIONS",
    "EVIDENCE_EXTRACTOR_V1",
    "EVIDENCE_EXTRACTOR_V2",
    "EvidenceExtractorV2",
    "negotiate_evidence_extractor_version",
)
