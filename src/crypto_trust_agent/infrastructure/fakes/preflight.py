"""Deterministic lightweight readiness fakes; no inference or network I/O."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import timedelta
from threading import RLock

from crypto_trust_agent.application.dto.common import (
    DeadlineExceededError,
    ErrorResultDTO,
    PortErrorCategory,
    PortErrorDTO,
    build_local_deadline,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO, TaskReadinessDTO, TaskReadinessRequestDTO
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock
from crypto_trust_agent.infrastructure.fakes.repositories import FakePlatformStore


def _jsonable(value):
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _deadline_error(clock: FakeClock, provider: str, request: object) -> ErrorResultDTO | None:
    try:
        build_local_deadline(
            request.deadline,
            provider_timeout_ms=3_000,
            now_utc=clock.current_utc().as_datetime(),
            now_monotonic_ms=clock.current_monotonic_ms(),
            runtime_id=clock.runtime_id,
        )
    except DeadlineExceededError:
        return ErrorResultDTO(
            PortErrorDTO(
                schema_version="1.0.0",
                code="deadline_exceeded",
                category=PortErrorCategory.TIMEOUT,
                retryable=False,
                safe_message="Deadline exceeded.",
                provider=provider,
                operation_id=request.operation_id,
                details={},
                occurred_at=clock.current_utc(),
            )
        )
    return None


class FakeTaskReadinessProbe:
    non_production = True

    def __init__(
        self,
        store: FakePlatformStore,
        clock: FakeClock,
        *,
        dataset_status: str = "healthy",
        dataset_version: str = "official-dataset-2026-05-31",
        dataset_reason_code: str | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._dataset_status = dataset_status
        self._dataset_version = dataset_version
        self._dataset_reason = dataset_reason_code
        self._lock = RLock()
        self.probe_count = 0

    def check(self, request: TaskReadinessRequestDTO) -> TaskReadinessDTO | ErrorResultDTO:
        with self._lock, self._store.lock:
            expired = _deadline_error(self._clock, "input_readiness", request)
            if expired is not None:
                return expired
            self.probe_count += 1
            task = self._store.tasks.get(request.task_id)
            proposed = self._store.proposed_tasks.get(request.task_id)
            required = {
                "question", "assets_requested_order", "assets_canonical", "timeframe",
                "question_type", "formal_run_intent", "sourcing_plan", "analysis_plan",
            }
            valid = (
                task is not None
                and task.version == request.task_version
                and task.request_fingerprint == request.request_fingerprint
                and proposed is not None
                and required.issubset(proposed)
                and proposed.get("formal_run_intent") is True
            )
            canonical_input = {
                "question": proposed.get("question") if proposed else None,
                "assets_requested_order": proposed.get("assets_requested_order") if proposed else None,
                "assets_canonical": proposed.get("assets_canonical") if proposed else None,
                "timeframe": proposed.get("timeframe") if proposed else None,
                "question_type": proposed.get("question_type") if proposed else None,
                "formal_run_intent": proposed.get("formal_run_intent") if proposed else None,
                "fingerprint_ruleset_version": task.fingerprint_ruleset_version if task else None,
                "sourcing_ruleset_version": (proposed.get("sourcing_plan") or {}).get("ruleset_version") if proposed else None,
                "analysis_ruleset_version": (proposed.get("analysis_plan") or {}).get("ruleset_version") if proposed else None,
            }
            wire = json.dumps(_jsonable(canonical_input), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            input_hash = "sha256:" + hashlib.sha256(wire.encode("utf-8")).hexdigest()
            return TaskReadinessDTO(
                input_lock_hash=input_hash,
                input_status="healthy" if valid else "unhealthy",
                input_capability_version="input-lock-1.0.0",
                input_safe_reason_code=None if valid else "task_input_not_ready",
                dataset_status=self._dataset_status,
                dataset_capability_version=self._dataset_version,
                dataset_safe_reason_code=self._dataset_reason,
            )


class FakeProviderHealthProbe:
    non_production = True

    def __init__(
        self,
        clock: FakeClock,
        provider: str,
        capability: str,
        *,
        status: str = "healthy",
        safe_reason_code: str | None = None,
        latency_ms: int = 1,
    ) -> None:
        self._clock = clock
        self._provider = provider
        self._capability = capability
        self._status = status
        self._reason = safe_reason_code
        self._latency_ms = latency_ms
        self._raw_diagnostic: str | None = None
        self._lock = RLock()
        self.probe_count = 0
        self.inference_count = 0

    def set_health(self, status: str, safe_reason_code: str | None, *, raw_diagnostic: str | None = None) -> None:
        with self._lock:
            self._status = status
            self._reason = safe_reason_code
            self._raw_diagnostic = raw_diagnostic

    def health_check(self, request: object) -> ProviderHealthDTO | ErrorResultDTO:
        with self._lock:
            expired = _deadline_error(self._clock, self._provider, request)
            if expired is not None:
                return expired
            self.probe_count += 1
            now = self._clock.current_utc()
            expires = (now.as_datetime() + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
            return ProviderHealthDTO(
                provider=self._provider,
                capability=self._capability,
                status=self._status,
                checked_at=now,
                latency_ms=self._latency_ms,
                safe_reason_code=self._reason,
                expires_at=expires,
            )


__all__ = ("FakeProviderHealthProbe", "FakeTaskReadinessProbe")
