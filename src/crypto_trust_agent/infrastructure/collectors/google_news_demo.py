"""Small fixed-host RSS reader used by the public hackathon demo."""

from __future__ import annotations

import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse


_BASE = "https://news.google.com/rss/search"
_MAX_BYTES = 1_048_576
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class DemoNewsItem:
    title: str
    summary: str
    url: str
    source_name: str
    published_at: str | None


class GoogleNewsRssCollector:
    """Fetch at most a few public headlines from one immutable HTTPS host."""

    def __init__(self, *, maximum_items: int = 3) -> None:
        if not 1 <= maximum_items <= 5:
            raise ValueError("maximum_items must be between 1 and 5")
        self._maximum_items = maximum_items

    @staticmethod
    def _query(asset: str, category: str) -> str:
        topic = "official announcement" if category == "official" else "cryptocurrency news"
        return f"{asset} {topic} when:14d"

    def collect(self, asset: str, category: str, *, timeout_seconds: float = 8.0) -> tuple[DemoNewsItem, ...]:
        if asset not in {"BTC", "ETH", "SOL", "BNB", "XRP"}:
            raise ValueError("unsupported demo news asset")
        if category not in {"news", "official"}:
            return ()
        query = quote_plus(self._query(asset, category))
        url = f"{_BASE}?q={query}&hl=en-US&gl=US&ceid=US:en"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "CryptoTrust-Hackathon-Demo/1.0"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed HTTPS host
            final = urlparse(response.geturl())
            if final.scheme != "https" or final.hostname != "news.google.com":
                raise RuntimeError("news redirect left the approved host")
            payload = response.read(_MAX_BYTES + 1)
        if len(payload) > _MAX_BYTES:
            raise RuntimeError("news response is too large")
        root = ET.fromstring(payload)
        items: list[DemoNewsItem] = []
        for node in root.findall("./channel/item")[: self._maximum_items]:
            title = self._clean(node.findtext("title") or "")
            summary = self._clean(node.findtext("description") or title)
            link = (node.findtext("link") or "").strip()
            parsed = urlparse(link)
            if not title or parsed.scheme != "https" or not parsed.hostname:
                continue
            source_node = node.find("source")
            source_name = self._clean(source_node.text if source_node is not None and source_node.text else "Google News")
            published_at = self._published(node.findtext("pubDate"))
            items.append(DemoNewsItem(title[:500], summary[:1500], link, source_name[:128], published_at))
        return tuple(items)

    @staticmethod
    def _clean(value: str) -> str:
        return " ".join(html.unescape(_TAG.sub(" ", value)).split())

    @staticmethod
    def _published(value: str | None) -> str | None:
        if not value:
            return None
        try:
            return parsedate_to_datetime(value).astimezone(UTC).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OverflowError):
            return None


__all__ = ("DemoNewsItem", "GoogleNewsRssCollector")
