"""Bounded Binance.US market fallback for the public AWS demo.

This adapter is intentionally separate from the frozen LiveMarketDataProvider
v1 contract, whose provenance requires api.binance.com.  It is used only by
the public demo when the global endpoint is unavailable from the AWS region.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time as day_time
from decimal import Decimal, InvalidOperation
import json
import socket
from urllib.parse import urlencode

from .security import PolicyViolation, UrlSecurityPolicy
from .transport import Fetcher, PinnedHttpsFetcher


BASE_URL = "https://api.binance.us"
_DAY_MS = 86_400_000
_MAX_RESPONSE_BYTES = 512 * 1024


def _default_resolver(host: str, port: int) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ))


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("market decimal must be a string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("invalid market decimal") from error
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("invalid market decimal")
    return parsed


@dataclass(frozen=True, slots=True)
class DemoMarketSnapshot:
    asset: str
    pair: str
    first_day: date
    last_day: date
    first_close: Decimal
    last_close: Decimal
    latest_volume: Decimal
    source_url: str


class BinanceUsDemoMarketCollector:
    """One-attempt, fixed-host Binance.US daily OHLCV reader."""

    def __init__(
        self,
        *,
        fetcher: Fetcher | None = None,
        resolver=_default_resolver,
    ) -> None:
        self._fetcher = fetcher or PinnedHttpsFetcher(
            user_agent="CryptoTrustAwsDemo/1.0"
        )
        self._security = UrlSecurityPolicy({"api.binance.us"}, resolver)

    def collect(
        self,
        asset: str,
        start: date,
        end: date,
        *,
        timeout_ms: int = 10_000,
    ) -> DemoMarketSnapshot:
        if asset not in {"BTC", "ETH", "SOL", "BNB", "XRP"}:
            raise ValueError("unsupported asset")
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 30_000 or start > end:
            raise ValueError("invalid market request")
        pair = f"{asset}USDT"
        query = urlencode({
            "symbol": pair,
            "interval": "1d",
            "startTime": int(datetime.combine(start, day_time.min, tzinfo=UTC).timestamp() * 1000),
            "endTime": int(datetime.combine(end, day_time.min, tzinfo=UTC).timestamp() * 1000) + _DAY_MS - 1,
            "limit": 1000,
        })
        url = f"{BASE_URL}/api/v3/klines?{query}"
        target = self._security.validate(url)
        response = self._fetcher.fetch(
            target,
            min(3.0, timeout_ms / 1000),
            min(10.0, timeout_ms / 1000),
            timeout_ms / 1000,
            lambda requested, peer: self._security.validate_peer(
                self._security.validate(requested), peer
            ),
        )
        self._security.validate_peer(target, response.peer_ip)
        if response.status != 200:
            raise PolicyViolation("collector_unavailable", "Binance.US market data is unavailable", True)
        if len(response.body) > _MAX_RESPONSE_BYTES:
            raise PolicyViolation("payload_too_large", "Binance.US response exceeds limit")
        payload = json.loads(response.body.decode("utf-8"))
        if not isinstance(payload, list) or not payload:
            raise ValueError("invalid Binance.US response")

        rows: list[tuple[date, Decimal, Decimal]] = []
        for row in payload:
            if not isinstance(row, list) or len(row) != 12:
                raise ValueError("invalid Binance.US kline")
            if type(row[0]) is not int or type(row[6]) is not int:
                raise ValueError("invalid Binance.US timestamp")
            if row[0] % _DAY_MS != 0 or row[6] != row[0] + _DAY_MS - 1:
                raise ValueError("non-daily Binance.US kline")
            day = datetime.fromtimestamp(row[0] / 1000, tz=UTC).date()
            if start <= day <= end:
                rows.append((day, _decimal(row[4]), _decimal(row[5])))
        if not rows or tuple(day for day, _, _ in rows) != tuple(sorted(day for day, _, _ in rows)):
            raise ValueError("invalid Binance.US coverage")
        first, last = rows[0], rows[-1]
        return DemoMarketSnapshot(
            asset, pair, first[0], last[0], first[1], last[1], last[2], url
        )


__all__ = ("BASE_URL", "BinanceUsDemoMarketCollector", "DemoMarketSnapshot")
