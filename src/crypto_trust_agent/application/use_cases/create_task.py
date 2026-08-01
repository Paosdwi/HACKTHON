"""CreateTask application orchestration for validated, trusted principals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.repositories import (
    ClockReadRequestDTO,
    ConsumeTaskCreateSlotRequestDTO,
    CreateOrGetTaskRequestDTO,
    RateLimitDecisionDTO,
)
from crypto_trust_agent.application.planning import (
    PLANNER_RULESET_VERSION,
    QuestionType,
    build_plan,
)
from crypto_trust_agent.application.ports import Clock, TaskRepository
from crypto_trust_agent.domain.fingerprint import (
    FINGERPRINT_RULESET_VERSION,
    FingerprintValidationError,
    create_request_fingerprint,
)

_PSEUDONYM = re.compile(r"^hmac-sha256:[a-zA-Z0-9._-]{1,32}:[0-9a-f]{64}$")


class CreateTaskValidationError(ValueError):
    """The business request is invalid and must not mutate repositories."""


class CreateTaskRateLimited(RuntimeError):
    """The trusted principal exhausted the independent create-task limit."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("task create rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


class CreateTaskDependencyError(RuntimeError):
    """A required Core port failed with a safe typed error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CreateTaskCommand:
    trusted_user_scope: str
    principal_pseudonym: str
    question: str
    assets: tuple[str, ...]
    timeframe_start: str
    timeframe_end: str
    formal_run: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "assets", tuple(self.assets))


@dataclass(frozen=True, slots=True)
class CreateTaskResult:
    task_id: str
    outcome: str
    state: str
    version: int
    request_fingerprint: str
    fingerprint_ruleset_version: str
    window_expires_at: str
    rate_limit: RateLimitDecisionDTO


@dataclass(frozen=True, slots=True)
class TaskCreationAuditRecord:
    principal_pseudonym: str
    request_fingerprint: str
    fingerprint_ruleset_version: str
    idempotency_outcome: str
    task_id: str | None
    rate_limit_allowed: bool
    rate_limit_remaining: int

    def to_safe_dict(self) -> dict[str, str | int | bool | None]:
        return {
            "principal_pseudonym": self.principal_pseudonym,
            "request_fingerprint": self.request_fingerprint,
            "fingerprint_ruleset_version": self.fingerprint_ruleset_version,
            "idempotency_outcome": self.idempotency_outcome,
            "task_id": self.task_id,
            "rate_limit_allowed": self.rate_limit_allowed,
            "rate_limit_remaining": self.rate_limit_remaining,
        }


AuditRecorder = Callable[[TaskCreationAuditRecord], None]
QuestionClassifier = Callable[[str, tuple[str, ...]], QuestionType | str]
IdentifierFactory = Callable[[str], str]


class CreateTaskUseCase:
    """Validate, count, plan, and atomically create-or-get one Task."""

    def __init__(
        self,
        task_repository: TaskRepository,
        clock: Clock,
        question_classifier: QuestionClassifier,
        identifier_factory: IdentifierFactory,
        audit_recorder: AuditRecorder,
    ) -> None:
        self._tasks = task_repository
        self._clock = clock
        self._classify = question_classifier
        self._new_id = identifier_factory
        self._record_audit = audit_recorder

    def execute(self, command: CreateTaskCommand) -> CreateTaskResult:
        now = self._read_now()
        slot_operation = self._new_id("OP-TASK-SLOT-")
        slot = self._tasks.consume_task_create_slot(
            ConsumeTaskCreateSlotRequestDTO(
                operation_id=slot_operation,
                trusted_user_scope=command.trusted_user_scope,
                principal_subject_hash=command.principal_pseudonym,
                deadline=self._deadline(slot_operation, now),
            )
        )
        if isinstance(slot, ErrorResultDTO):
            raise CreateTaskDependencyError(slot.error.code)
        if not slot.allowed:
            raise CreateTaskRateLimited(max(1, slot.retry_after_seconds))

        fingerprint, question_type = self._validate(command)
        plan = build_plan(
            question_type=question_type,
            fingerprint=fingerprint,
            clock_snapshot=str(now),
        )

        create_operation = self._new_id("OP-TASK-CREATE-")
        task_id = self._new_id("TASK-")
        proposed_task = {
            "task_id": task_id,
            "question": fingerprint.normalized_question,
            "assets_requested_order": fingerprint.assets_requested_order,
            "assets_canonical": fingerprint.assets_canonical,
            "timeframe": {
                "start": fingerprint.timeframe_start,
                "end": fingerprint.timeframe_end,
            },
            "question_type": question_type.value,
            "formal_run_intent": command.formal_run,
            "sourcing_plan": {
                "plan_id": "PLAN-S-" + plan.canonical_hash[-24:],
                "ruleset_version": PLANNER_RULESET_VERSION,
                "canonical_hash": plan.canonical_hash,
            },
            "analysis_plan": {
                "plan_id": "PLAN-A-" + plan.canonical_hash[-24:],
                "ruleset_version": PLANNER_RULESET_VERSION,
                "canonical_hash": plan.canonical_hash,
            },
            "created_at": str(now),
        }
        created = self._tasks.create_or_get(
            CreateOrGetTaskRequestDTO(
                operation_id=create_operation,
                trusted_user_scope=command.trusted_user_scope,
                principal_subject_hash=command.principal_pseudonym,
                request_fingerprint=fingerprint.request_fingerprint,
                idempotency_window_started_at=now,
                proposed_task=proposed_task,
                deadline=self._deadline(create_operation, now),
            )
        )
        if isinstance(created, ErrorResultDTO):
            raise CreateTaskDependencyError(created.error.code)

        audit = TaskCreationAuditRecord(
            principal_pseudonym=command.principal_pseudonym,
            request_fingerprint=fingerprint.request_fingerprint,
            fingerprint_ruleset_version=FINGERPRINT_RULESET_VERSION,
            idempotency_outcome=created.outcome,
            task_id=created.task.task_id,
            rate_limit_allowed=True,
            rate_limit_remaining=slot.remaining,
        )
        self._try_record_audit(audit)
        return CreateTaskResult(
            task_id=created.task.task_id,
            outcome=created.outcome,
            state=created.task.state,
            version=created.task.version,
            request_fingerprint=created.task.request_fingerprint,
            fingerprint_ruleset_version=created.task.fingerprint_ruleset_version,
            window_expires_at=str(created.window_expires_at),
            rate_limit=slot,
        )

    def _try_record_audit(self, audit: TaskCreationAuditRecord) -> None:
        try:
            self._record_audit(audit)
        except Exception:
            # Observability must not turn a committed create-or-get into a failed API call.
            pass

    def _validate(self, command: CreateTaskCommand):
        if (
            not isinstance(command.trusted_user_scope, str)
            or not 1 <= len(command.trusted_user_scope) <= 128
            or not _PSEUDONYM.fullmatch(command.principal_pseudonym)
            or type(command.formal_run) is not bool
        ):
            raise CreateTaskValidationError("invalid trusted principal or formal_run")
        try:
            fingerprint = create_request_fingerprint(
                question=command.question,
                assets=command.assets,
                timeframe_start=command.timeframe_start,
                timeframe_end=command.timeframe_end,
            )
            if len(fingerprint.normalized_question) > 2000:
                raise CreateTaskValidationError("question exceeds 2000 characters")
            if fingerprint.timeframe_start >= fingerprint.timeframe_end:
                raise CreateTaskValidationError("timeframe start must precede end")
            question_type = QuestionType(
                self._classify(
                    fingerprint.normalized_question,
                    fingerprint.assets_requested_order,
                )
            )
            expected_assets = 2 if question_type is QuestionType.ASSET_COMPARISON else 1
            if len(fingerprint.assets_requested_order) != expected_assets:
                raise CreateTaskValidationError("question type asset count mismatch")
        except (FingerprintValidationError, TypeError, ValueError) as error:
            if isinstance(error, CreateTaskValidationError):
                raise
            raise CreateTaskValidationError("unsupported or invalid task input") from error
        return fingerprint, question_type

    def _read_now(self):
        operation_id = self._new_id("OP-CLOCK-TASK-")
        result = self._clock.now_utc(ClockReadRequestDTO(operation_id))
        if isinstance(result, ErrorResultDTO):
            raise CreateTaskDependencyError(result.error.code)
        return result.utc

    @staticmethod
    def _deadline(operation_id: str, now) -> DeadlineDTO:
        return DeadlineDTO(
            schema_version="1.0.0",
            operation_id=operation_id,
            deadline_at_utc=(
                now.as_datetime() + timedelta(seconds=30)
            ).isoformat().replace("+00:00", "Z"),
            budget_ms=2_000,
            sent_at_utc=now,
            safety_margin_ms=100,
        )


__all__ = (
    "AuditRecorder",
    "CreateTaskCommand",
    "CreateTaskDependencyError",
    "CreateTaskRateLimited",
    "CreateTaskResult",
    "CreateTaskUseCase",
    "CreateTaskValidationError",
    "TaskCreationAuditRecord",
)
