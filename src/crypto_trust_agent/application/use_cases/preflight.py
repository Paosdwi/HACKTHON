"""Formal-run pre-flight readiness orchestration without inference or quota use."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Mapping

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.repositories import (
    AppendPreflightResultRequestDTO,
    ClockReadRequestDTO,
    ConsumePreflightSlotRequestDTO,
    PreflightRecordDTO,
    RateLimitDecisionDTO,
    TaskQueryRequestDTO,
)
from crypto_trust_agent.application.ports import Clock, TaskRepository
from crypto_trust_agent.application.ports.preflight import (
    CollectorHealthCheckRequestDTO,
    HealthCheckPort,
    HealthCheckRequestDTO,
    ProviderHealthDTO,
    ReasoningHealthCheckRequestDTO,
    TaskReadinessDTO,
    TaskReadinessProbe,
    TaskReadinessRequestDTO,
)

IdentifierFactory = Callable[[str], str]
_BLOCKING = {"unhealthy", "not_configured"}


class PreflightRateLimited(RuntimeError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("preflight rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


class PreflightTaskNotFound(LookupError):
    pass


class PreflightDependencyError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PreflightCommand:
    trusted_user_scope: str
    task_id: str


@dataclass(frozen=True, slots=True)
class PreflightResult:
    record: PreflightRecordDTO
    rate_limit: RateLimitDecisionDTO

    @property
    def ready(self) -> bool:
        return self.record.ready

    @property
    def safe_reason_codes(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                str(item["safe_reason_code"])
                for item in self.record.checks
                if item.get("safe_reason_code") is not None
            )
        )

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "preflight_id": self.record.preflight_id,
            "task_id": self.record.task_id,
            "task_version": self.record.task_version,
            "ready": self.record.ready,
            "input_lock_hash": self.record.input_lock_hash,
            "dependency_snapshot_hash": self.record.dependency_snapshot_hash,
            "checked_at": str(self.record.checked_at),
            "expires_at": str(self.record.expires_at),
            "ttl_seconds": self.record.ttl_seconds,
            "single_use": True,
            "consumed": self.record.consumed_at is not None,
            "checks": [dict(item) for item in self.record.checks],
            "safe_reason_codes": list(self.safe_reason_codes),
            "rate_limit": {
                "limit": self.rate_limit.limit,
                "remaining": self.rate_limit.remaining,
                "window_seconds": self.rate_limit.window_seconds,
            },
        }


class PreflightUseCase:
    """Rate gate first, then perform only side-effect-free lightweight probes."""

    def __init__(
        self,
        task_repository: TaskRepository,
        clock: Clock,
        task_readiness: TaskReadinessProbe,
        nova: HealthCheckPort,
        opus: HealthCheckPort,
        sagemaker: HealthCheckPort,
        allowlist: HealthCheckPort,
        identifier_factory: IdentifierFactory,
    ) -> None:
        self._tasks = task_repository
        self._clock = clock
        self._task_readiness = task_readiness
        self._nova = nova
        self._opus = opus
        self._sagemaker = sagemaker
        self._allowlist = allowlist
        self._new_id = identifier_factory

    def execute(self, command: PreflightCommand) -> PreflightResult:
        if not isinstance(command.trusted_user_scope, str) or not command.trusted_user_scope or not command.task_id.startswith("TASK-"):
            raise PreflightTaskNotFound("task not found")
        now = self._read_now()

        # This atomic task-scoped gate must precede every local or provider probe.
        slot_operation = self._new_id("OP-PREFLIGHT-SLOT-")
        slot = self._tasks.consume_preflight_slot(
            ConsumePreflightSlotRequestDTO(
                operation_id=slot_operation,
                trusted_user_scope=command.trusted_user_scope,
                task_id=command.task_id,
                deadline=self._deadline(slot_operation, now),
            )
        )
        if isinstance(slot, ErrorResultDTO):
            self._raise_repository_error(slot)
        if not slot.allowed:
            raise PreflightRateLimited(max(1, slot.retry_after_seconds))

        task_operation = self._new_id("OP-PREFLIGHT-TASK-")
        task = self._tasks.get(
            TaskQueryRequestDTO(
                operation_id=task_operation,
                trusted_user_scope=command.trusted_user_scope,
                task_id=command.task_id,
                deadline=self._deadline(task_operation, now),
            )
        )
        if isinstance(task, ErrorResultDTO):
            self._raise_repository_error(task)

        local_operation = self._new_id("OP-PREFLIGHT-LOCAL-")
        try:
            local_result = self._task_readiness.check(
                TaskReadinessRequestDTO(
                    operation_id=local_operation,
                    task_id=task.task_id,
                    task_version=task.version,
                    request_fingerprint=task.request_fingerprint,
                    deadline=self._deadline(local_operation, now),
                )
            )
        except Exception:
            local_result = self._failed_local("unexpected_provider_error")
        local = self._local_result(local_result)

        provider_results = (
            ("nova", self._probe(self._nova, HealthCheckRequestDTO, now)),
            ("opus", self._probe(self._opus, ReasoningHealthCheckRequestDTO, now, model_role="primary")),
            ("sagemaker", self._probe(self._sagemaker, HealthCheckRequestDTO, now)),
            ("external_allowlist", self._probe(self._allowlist, CollectorHealthCheckRequestDTO, now, provider="external_allowlist")),
        )
        checks = [
            self._check("input", local.input_status, local.input_safe_reason_code),
            self._check("official_dataset", local.dataset_status, local.dataset_safe_reason_code),
        ]
        snapshot_items = [
            self._snapshot("input", local.input_capability_version, local.input_status, now),
            self._snapshot("official_dataset", local.dataset_capability_version, local.dataset_status, now),
        ]
        for name, health in provider_results:
            checks.append(self._check(name, health.status, health.safe_reason_code))
            capability_version = f"{health.provider}:{health.capability}:{health.schema_version}"
            snapshot_items.append(self._snapshot(name, capability_version[:128], health.status, health.checked_at))

        checked_at = self._read_now()
        dependency_hash = self._hash({"checks": checks, "items": snapshot_items})
        ready = all(item["status"] not in _BLOCKING for item in checks)
        preflight_id = self._new_id("PF-")
        record = PreflightRecordDTO(
            preflight_id=preflight_id,
            task_id=task.task_id,
            task_version=task.version,
            input_lock_hash=local.input_lock_hash,
            dependency_snapshot_hash=dependency_hash,
            checked_at=checked_at,
            expires_at=(checked_at.as_datetime() + timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
            ready=ready,
            checks=tuple(checks),
            dependency_snapshot={"items": tuple(snapshot_items)},
        )
        append_operation = self._new_id("OP-PREFLIGHT-APPEND-")
        saved = self._tasks.append_preflight_result(
            AppendPreflightResultRequestDTO(
                operation_id=append_operation,
                expected_task_version=task.version,
                record=record,
                deadline=self._deadline(append_operation, now),
            )
        )
        if isinstance(saved, ErrorResultDTO):
            self._raise_repository_error(saved)
        return PreflightResult(saved, slot)

    def _probe(self, port: HealthCheckPort, request_type, now, **kwargs) -> ProviderHealthDTO:
        operation_id = self._new_id("OP-PREFLIGHT-HEALTH-")
        try:
            result = port.health_check(request_type(operation_id=operation_id, deadline=self._deadline(operation_id, now), **kwargs))
        except Exception:
            return ProviderHealthDTO("core", "unknown", "unhealthy", now, 0, "unexpected_provider_error", None)
        if isinstance(result, ErrorResultDTO):
            return ProviderHealthDTO(result.error.provider, "unknown", "unhealthy", result.error.occurred_at, 0, result.error.code, None)
        return result

    @staticmethod
    def _failed_local(code: str) -> TaskReadinessDTO:
        return TaskReadinessDTO(
            input_lock_hash="sha256:" + "0" * 64,
            input_status="unhealthy",
            input_capability_version="unknown",
            input_safe_reason_code=code,
            dataset_status="unhealthy",
            dataset_capability_version="unknown",
            dataset_safe_reason_code=code,
        )

    @classmethod
    def _local_result(cls, result: TaskReadinessDTO | ErrorResultDTO) -> TaskReadinessDTO:
        if isinstance(result, ErrorResultDTO):
            return cls._failed_local(result.error.code)
        return result

    @staticmethod
    def _check(name: str, status: str, reason: str | None) -> dict[str, object]:
        return {"name": name, "required": True, "status": status, "safe_reason_code": reason}

    @staticmethod
    def _snapshot(name: str, version: str, status: str, checked_at) -> dict[str, object]:
        return {"name": name, "capability_version": version, "status": status, "checked_at": str(checked_at)}

    @classmethod
    def _hash(cls, value: Mapping[str, object]) -> str:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _read_now(self):
        operation_id = self._new_id("OP-CLOCK-PREFLIGHT-")
        result = self._clock.now_utc(ClockReadRequestDTO(operation_id))
        if isinstance(result, ErrorResultDTO):
            raise PreflightDependencyError(result.error.code)
        return result.utc

    @staticmethod
    def _deadline(operation_id: str, now) -> DeadlineDTO:
        return DeadlineDTO(
            schema_version="1.0.0",
            operation_id=operation_id,
            deadline_at_utc=(now.as_datetime() + timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
            budget_ms=3_000,
            sent_at_utc=now,
            safety_margin_ms=100,
        )

    @staticmethod
    def _raise_repository_error(result: ErrorResultDTO) -> None:
        if result.error.code == "task_not_found":
            raise PreflightTaskNotFound("task not found")
        raise PreflightDependencyError(result.error.code)


__all__ = (
    "PreflightCommand",
    "PreflightDependencyError",
    "PreflightRateLimited",
    "PreflightResult",
    "PreflightTaskNotFound",
    "PreflightUseCase",
)
