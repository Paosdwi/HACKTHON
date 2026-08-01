"""Controllable in-process Clock fake; never use as a production clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import RLock

from crypto_trust_agent.application.dto.repositories import (
    ClockReadRequestDTO,
    MonotonicInstantDTO,
    UtcInstantDTO,
)
from crypto_trust_agent.domain.primitives import UtcInstant


class FakeClock:
    """Thread-safe wall/monotonic fake with independently controlled values."""

    non_production = True

    def __init__(
        self,
        now_utc: str | UtcInstant,
        *,
        monotonic_ms: int = 0,
        runtime_id: str = "fake-runtime-00000001",
    ) -> None:
        self._lock = RLock()
        self._utc = now_utc if isinstance(now_utc, UtcInstant) else UtcInstant(now_utc)
        if monotonic_ms < 0 or len(runtime_id) < 16:
            raise ValueError("invalid fake clock initial value")
        self._monotonic_ms = monotonic_ms
        self.runtime_id = runtime_id

    def now_utc(self, request: ClockReadRequestDTO) -> UtcInstantDTO:
        del request
        with self._lock:
            return UtcInstantDTO(self._utc)

    def monotonic_ms(self, request: ClockReadRequestDTO) -> MonotonicInstantDTO:
        del request
        with self._lock:
            return MonotonicInstantDTO(self.runtime_id, self._monotonic_ms)

    def current_utc(self) -> UtcInstant:
        with self._lock:
            return self._utc

    def current_monotonic_ms(self) -> int:
        with self._lock:
            return self._monotonic_ms

    def set_utc(self, value: str | UtcInstant) -> None:
        with self._lock:
            self._utc = value if isinstance(value, UtcInstant) else UtcInstant(value)

    def set_monotonic_ms(self, value: int) -> None:
        with self._lock:
            if type(value) is not int or value < self._monotonic_ms:
                raise ValueError("fake monotonic time cannot decrease")
            self._monotonic_ms = value

    def advance(self, *, wall_seconds: float = 0, monotonic_ms: int = 0) -> None:
        if wall_seconds < 0 or monotonic_ms < 0:
            raise ValueError("advance values must be nonnegative")
        with self._lock:
            current = self._utc.as_datetime() + timedelta(seconds=wall_seconds)
            self._utc = UtcInstant(current.astimezone(UTC).isoformat().replace("+00:00", "Z"))
            self._monotonic_ms += monotonic_ms
