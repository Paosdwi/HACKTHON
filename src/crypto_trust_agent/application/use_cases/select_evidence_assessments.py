"""Snapshot-bound latest EvidenceAssessment selection with safe audit publication."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from threading import RLock
from typing import Callable

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.repositories import (
    ClockReadRequestDTO,
    ExecutionEventDTO,
    GetEvidenceRequestDTO,
    GetLatestAssessmentsRequestDTO,
    LatestAssessmentsDTO,
    ListEvidenceRequestDTO,
    PublishEventRequestDTO,
)
from crypto_trust_agent.application.ports import Clock, EventPublisher, EvidenceRepository

IdentifierFactory = Callable[[str], str]
_OPERATION = re.compile(r"^OP-[A-Z0-9][A-Z0-9._:-]{0,126}$")
_EVENT = re.compile(r"^EVT-.+")


class LatestAssessmentSelectionRejected(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class LatestAssessmentSelectionCommand:
    operation_id: str
    task_id: str
    execution_id: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))


class LatestAssessmentSelectionUseCase:
    def __init__(
        self,
        evidence_repository: EvidenceRepository,
        event_publisher: EventPublisher,
        clock: Clock,
        identifier_factory: IdentifierFactory,
    ) -> None:
        self._evidence = evidence_repository
        self._events = event_publisher
        self._clock = clock
        self._new_id = identifier_factory
        self._lock = RLock()
        self._replays: dict[str, tuple[LatestAssessmentSelectionCommand, LatestAssessmentsDTO]] = {}

    def execute(self, command: LatestAssessmentSelectionCommand) -> LatestAssessmentsDTO:
        with self._lock:
            replay = self._replays.get(command.operation_id)
            if replay is not None:
                if replay[0] != command:
                    raise LatestAssessmentSelectionRejected("operation_payload_conflict")
                return replay[1]
            if (
                not _OPERATION.fullmatch(command.operation_id)
                or not command.task_id.startswith("TASK-")
                or not command.execution_id.startswith("EXEC-")
                or not command.evidence_ids
                or len(set(command.evidence_ids)) != len(command.evidence_ids)
            ):
                raise LatestAssessmentSelectionRejected("invalid_selection_request")
            now = self._read_now()
            for evidence_id in command.evidence_ids:
                operation_id = self._operation_id("OP-GET-EVIDENCE-")
                found = self._evidence.get(GetEvidenceRequestDTO(
                    operation_id,
                    command.task_id,
                    evidence_id,
                    self._deadline(operation_id, now),
                ))
                if isinstance(found, ErrorResultDTO):
                    raise LatestAssessmentSelectionRejected(found.error.code)
                if found.execution_id != command.execution_id:
                    raise LatestAssessmentSelectionRejected("execution_lineage_mismatch")
            list_operation = self._operation_id("OP-LIST-EVIDENCE-")
            page = self._evidence.list_for_task(ListEvidenceRequestDTO(
                list_operation,
                command.task_id,
                self._deadline(list_operation, now),
                validation_status="active",
            ))
            if isinstance(page, ErrorResultDTO):
                raise LatestAssessmentSelectionRejected(page.error.code)
            latest_operation = self._operation_id("OP-LATEST-ASSESSMENTS-")
            latest = self._evidence.get_latest_assessments(GetLatestAssessmentsRequestDTO(
                latest_operation,
                command.task_id,
                command.evidence_ids,
                page.snapshot_token,
                self._deadline(latest_operation, now),
            ))
            if isinstance(latest, ErrorResultDTO):
                raise LatestAssessmentSelectionRejected(latest.error.code)
            if len(latest.items) != len(command.evidence_ids):
                raise LatestAssessmentSelectionRejected("assessment_not_found")
            self._publish_audit(command, latest, now)
            self._replays[command.operation_id] = (command, latest)
            return latest

    def _publish_audit(self, command: LatestAssessmentSelectionCommand, latest: LatestAssessmentsDTO, now) -> None:
        event_id = self._event_id()
        publish_operation = self._operation_id("OP-PUBLISH-ASSESSMENTS-")
        assessment_ids = ",".join(sorted(item.assessment_id for item in latest.items))
        assessment_rulesets = ",".join(sorted({item.ruleset_version for item in latest.items}))
        assessment_versions = ",".join(sorted({item.assessment_version for item in latest.items}))
        event = ExecutionEventDTO(
            event_id=event_id,
            timestamp=now,
            task_id=command.task_id,
            execution_id=command.execution_id,
            step="select_assessments",
            tool="core_evidence",
            status="completed",
            duration_ms=0,
            retry_count=0,
            sanitized_parameters={"evidence_count": len(command.evidence_ids)},
            result_summary={
                "assessment_count": len(latest.items),
                "assessment_ids": assessment_ids,
                "assessment_rulesets": assessment_rulesets,
                "assessment_versions": assessment_versions,
                "selection_ruleset_version": latest.selection_ruleset_version,
            },
            error=None,
            deadline_remaining_ms=5_000,
            correlation={"operation_id": command.operation_id, "causation_event_id": None},
        )
        receipt = self._events.publish(PublishEventRequestDTO(
            publish_operation,
            event,
            self._deadline(publish_operation, now),
        ))
        if isinstance(receipt, ErrorResultDTO):
            raise LatestAssessmentSelectionRejected(receipt.error.code)

    def _read_now(self):
        operation_id = self._operation_id("OP-CLOCK-ASSESSMENTS-")
        result = self._clock.now_utc(ClockReadRequestDTO(operation_id))
        if isinstance(result, ErrorResultDTO):
            raise LatestAssessmentSelectionRejected(result.error.code)
        return result.utc

    def _operation_id(self, prefix: str) -> str:
        try:
            value = self._new_id(prefix)
        except Exception as error:
            raise LatestAssessmentSelectionRejected("invalid_generated_id") from error
        if not isinstance(value, str) or not _OPERATION.fullmatch(value):
            raise LatestAssessmentSelectionRejected("invalid_generated_id")
        return value

    def _event_id(self) -> str:
        try:
            value = self._new_id("EVT-")
        except Exception as error:
            raise LatestAssessmentSelectionRejected("invalid_generated_id") from error
        if not isinstance(value, str) or not _EVENT.fullmatch(value):
            raise LatestAssessmentSelectionRejected("invalid_generated_id")
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
    "LatestAssessmentSelectionCommand",
    "LatestAssessmentSelectionRejected",
    "LatestAssessmentSelectionUseCase",
)
