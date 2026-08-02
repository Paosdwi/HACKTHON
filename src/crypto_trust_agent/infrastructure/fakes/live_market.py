"""Deterministic non-production LiveMarketDataProvider fake."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from threading import RLock

from crypto_trust_agent.application.dto.common import (
    DeadlineExceededError, ErrorResultDTO, PortErrorCategory, PortErrorDTO,
    build_local_deadline,
)
from crypto_trust_agent.application.dto.live_market import (
    LiveMarketBarDTO, LiveMarketCapabilitiesDTO, LiveMarketCapabilitiesRequestDTO,
    LiveMarketDataRequestDTO, LiveMarketDataResultDTO, LiveMarketHealthRequestDTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO


class FakeLiveMarketDataProvider:
    non_production = True
    max_attempts = 1
    hidden_retries = 0

    def __init__(self, clock) -> None:
        self._clock = clock
        self._completed: dict[str, tuple[LiveMarketDataRequestDTO, LiveMarketDataResultDTO | ErrorResultDTO]] = {}
        self._lock = RLock()
        self.invocation_count = 0

    def _error(self, operation_id: str, code: str) -> ErrorResultDTO:
        category = PortErrorCategory.TIMEOUT if code == "deadline_exceeded" else PortErrorCategory.CONFLICT
        return PortErrorDTO("1.0.0", code, category, False, "Live market request failed.", "binance_spot", operation_id, {}, self._clock.current_utc()).as_result()

    def _deadline(self, request, timeout_ms: int) -> None:
        build_local_deadline(request.deadline, provider_timeout_ms=timeout_ms,
            now_utc=self._clock.current_utc().as_datetime(),
            now_monotonic_ms=self._clock.current_monotonic_ms(), runtime_id=self._clock.runtime_id)

    def fetch_daily_ohlcv(self, request: LiveMarketDataRequestDTO) -> LiveMarketDataResultDTO | ErrorResultDTO:
        with self._lock:
            replay = self._completed.get(request.operation_id)
            if replay:
                return replay[1] if replay[0] == request else self._error(request.operation_id, "payload_conflict")
            try:
                self._deadline(request, 30_000)
            except DeadlineExceededError:
                return self._error(request.operation_id, "deadline_exceeded")
            self.invocation_count += 1
            bars = []
            current = request.start_date
            fetched = str(self._clock.current_utc())
            while current <= request.end_date:
                open_ms = int(datetime.combine(current, time.min, tzinfo=UTC).timestamp() * 1000)
                payload = {"asset": request.asset, "day": current.isoformat(), "open": "100", "high": "105", "low": "95", "close": "101", "volume": "1234.5"}
                content_hash = "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                bars.append(LiveMarketBarDTO(request.asset, request.pair, current, "100", "105", "95", "101", "1234.5",
                    f"https://api.binance.com/api/v3/klines?interval=1d&symbol={request.pair}", fetched,
                    open_ms, open_ms + 86_400_000 - 1, content_hash))
                current += timedelta(days=1)
            result = LiveMarketDataResultDTO(request.operation_id, request.asset, request.pair,
                request.start_date, request.end_date, tuple(bars), True, (), (), fetched)
            self._completed[request.operation_id] = (request, result)
            return result

    def health_check(self, request: LiveMarketHealthRequestDTO) -> ProviderHealthDTO | ErrorResultDTO:
        try:
            self._deadline(request, 3_000)
        except DeadlineExceededError:
            return self._error(request.operation_id, "deadline_exceeded")
        return ProviderHealthDTO("binance_spot", "live_market_data", "healthy", self._clock.current_utc(), 0, None, None)

    def capabilities(self, request: LiveMarketCapabilitiesRequestDTO) -> LiveMarketCapabilitiesDTO | ErrorResultDTO:
        try:
            self._deadline(request, 3_000)
        except DeadlineExceededError:
            return self._error(request.operation_id, "deadline_exceeded")
        return LiveMarketCapabilitiesDTO()


__all__ = ("FakeLiveMarketDataProvider",)
