"""Recorded at-least-once EventPublisher fake (non-production)."""

from __future__ import annotations

from threading import RLock

from crypto_trust_agent.application.dto.common import ErrorResultDTO
from crypto_trust_agent.application.dto.repositories import (
    BatchItemReceiptDTO,
    ExecutionEventDTO,
    PublishBatchReceiptDTO,
    PublishBatchRequestDTO,
    PublishEventRequestDTO,
    PublishReceiptDTO,
)
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock
from crypto_trust_agent.infrastructure.fakes.repositories import _error, _guard_deadlines


@_guard_deadlines("event_publisher", {"publish": 2000, "publish_batch": 3000})
class FakeEventPublisher:
    """Deterministic recorder with event-ID/payload deduplication."""

    non_production = True

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self._lock = RLock()
        self._events: dict[str, ExecutionEventDTO] = {}

    @property
    def recorded_events(self) -> tuple[ExecutionEventDTO, ...]:
        with self._lock:
            return tuple(sorted(self._events.values(), key=lambda item: (item.timestamp.as_datetime(), item.event_id)))

    def publish(self, request: PublishEventRequestDTO) -> PublishReceiptDTO | ErrorResultDTO:
        with self._lock:
            existing = self._events.get(request.event.event_id)
            if existing is not None:
                if existing == request.event:
                    return PublishReceiptDTO(request.event.event_id, "existing", self._clock.current_utc())
                return _error(self._clock, "event_publisher", request.operation_id, "event_id_conflict", "integrity")
            self._events[request.event.event_id] = request.event
            return PublishReceiptDTO(request.event.event_id, "published", self._clock.current_utc())

    def publish_batch(self, request: PublishBatchRequestDTO) -> PublishBatchReceiptDTO | ErrorResultDTO:
        if not 1 <= len(request.events) <= 100:
            return _error(self._clock, "event_publisher", request.operation_id, "event_schema_invalid", "validation")
        with self._lock:
            staged: dict[str, ExecutionEventDTO] = {}
            receipts: list[BatchItemReceiptDTO] = []
            for event in request.events:
                existing = staged.get(event.event_id, self._events.get(event.event_id))
                if existing is not None and existing != event:
                    return _error(self._clock, "event_publisher", request.operation_id, "event_id_conflict", "integrity")
                outcome = "existing" if existing is not None else "published"
                staged[event.event_id] = event
                receipts.append(BatchItemReceiptDTO(event.event_id, outcome, None))
            self._events.update(staged)
            return PublishBatchReceiptDTO(tuple(receipts), self._clock.current_utc())
