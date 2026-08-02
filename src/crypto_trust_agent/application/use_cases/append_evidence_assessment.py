"""Append a versioned EvidenceAssessment after validating Evidence lineage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from threading import RLock
from typing import Callable

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.repositories import (
    AppendAssessmentsRequestDTO,
    ClockReadRequestDTO,
    EvidenceAssessmentDTO,
    GetEvidenceRequestDTO,
)
from crypto_trust_agent.application.ports import Clock, EvidenceRepository
from crypto_trust_agent.domain.evidence import EvidenceAssessment

IdentifierFactory = Callable[[str], str]
_OPERATION = re.compile(r"^OP-[A-Z0-9][A-Z0-9._:-]{0,126}$")
_ASSESSMENT = re.compile(r"^ASSESS-.+")


class AppendEvidenceAssessmentRejected(RuntimeError):
    """Safe application-level assessment rejection."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AppendEvidenceAssessmentCommand:
    operation_id: str
    task_id: str
    execution_id: str
    evidence_id: str
    assessment_sequence: int
    assessment_version: str
    ruleset_version: str
    source_trust: str
    relevance: str
    freshness: str
    independence: str
    independence_group: str
    consistency: str
    overall_confidence: str
    contradiction_severity: str
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "limitations", tuple(self.limitations))


class AppendEvidenceAssessmentUseCase:
    """Build and append one immutable assessment with operation-safe replay."""

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
        self._replays: dict[
            str, tuple[AppendEvidenceAssessmentCommand, EvidenceAssessmentDTO]
        ] = {}

    def execute(self, command: AppendEvidenceAssessmentCommand) -> EvidenceAssessmentDTO:
        with self._lock:
            replay = self._replays.get(command.operation_id)
            if replay is not None:
                if replay[0] != command:
                    raise AppendEvidenceAssessmentRejected("operation_payload_conflict")
                return replay[1]
            self._validate_command(command)
            now = self._read_now()
            get_operation = self._operation_id("OP-GET-EVIDENCE-")
            found = self._evidence.get(
                GetEvidenceRequestDTO(
                    get_operation,
                    command.task_id,
                    command.evidence_id,
                    self._deadline(get_operation, now),
                    include_quarantined=False,
                )
            )
            if isinstance(found, ErrorResultDTO):
                raise AppendEvidenceAssessmentRejected(found.error.code)
            if found.execution_id != command.execution_id:
                raise AppendEvidenceAssessmentRejected("execution_lineage_mismatch")

            assessment_id = self._assessment_id()
            try:
                assessment = EvidenceAssessment(
                    assessment_id=assessment_id,
                    task_id=command.task_id,
                    evidence_id=command.evidence_id,
                    assessment_sequence=command.assessment_sequence,
                    assessment_version=command.assessment_version,
                    ruleset_version=command.ruleset_version,
                    source_trust=command.source_trust,
                    relevance=command.relevance,
                    freshness=command.freshness,
                    independence=command.independence,
                    independence_group=command.independence_group,
                    consistency=command.consistency,
                    overall_confidence=command.overall_confidence,
                    contradiction_severity=command.contradiction_severity,
                    computed_at=now,
                    limitations=command.limitations,
                )
                dto = EvidenceAssessmentDTO(
                    assessment.assessment_id,
                    assessment.task_id,
                    assessment.evidence_id,
                    assessment.assessment_sequence,
                    assessment.assessment_version,
                    assessment.ruleset_version,
                    assessment.source_trust,
                    assessment.relevance,
                    assessment.freshness,
                    assessment.independence,
                    assessment.independence_group,
                    assessment.consistency,
                    assessment.overall_confidence,
                    assessment.contradiction_severity.value,
                    assessment.computed_at,
                    assessment.limitations,
                    assessment.schema_version,
                )
            except (TypeError, ValueError) as error:
                raise AppendEvidenceAssessmentRejected("invalid_assessment_request") from error

            append_operation = self._operation_id("OP-APPEND-ASSESSMENTS-")
            appended = self._evidence.append_assessments(
                AppendAssessmentsRequestDTO(
                    append_operation,
                    command.task_id,
                    (dto,),
                    self._deadline(append_operation, now),
                )
            )
            if isinstance(appended, ErrorResultDTO):
                raise AppendEvidenceAssessmentRejected(appended.error.code)
            self._replays[command.operation_id] = (command, dto)
            return dto

    @staticmethod
    def _validate_command(command: AppendEvidenceAssessmentCommand) -> None:
        if (
            not _OPERATION.fullmatch(command.operation_id)
            or not command.task_id.startswith("TASK-")
            or not command.execution_id.startswith("EXEC-")
            or not command.evidence_id.startswith("EVID-")
        ):
            raise AppendEvidenceAssessmentRejected("invalid_assessment_request")

    def _read_now(self):
        operation_id = self._operation_id("OP-CLOCK-ASSESSMENT-")
        result = self._clock.now_utc(ClockReadRequestDTO(operation_id))
        if isinstance(result, ErrorResultDTO):
            raise AppendEvidenceAssessmentRejected(result.error.code)
        return result.utc

    def _operation_id(self, prefix: str) -> str:
        try:
            value = self._new_id(prefix)
        except Exception as error:
            raise AppendEvidenceAssessmentRejected("invalid_generated_id") from error
        if not isinstance(value, str) or not _OPERATION.fullmatch(value):
            raise AppendEvidenceAssessmentRejected("invalid_generated_id")
        return value

    def _assessment_id(self) -> str:
        try:
            value = self._new_id("ASSESS-")
        except Exception as error:
            raise AppendEvidenceAssessmentRejected("invalid_generated_id") from error
        if not isinstance(value, str) or not _ASSESSMENT.fullmatch(value):
            raise AppendEvidenceAssessmentRejected("invalid_generated_id")
        return value

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
    "AppendEvidenceAssessmentCommand",
    "AppendEvidenceAssessmentRejected",
    "AppendEvidenceAssessmentUseCase",
)
