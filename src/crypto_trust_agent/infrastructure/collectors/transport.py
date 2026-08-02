"""Bounded static HTTP/RSS and Playwright transport boundaries."""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import http.client
import socket
import ssl
import time
from typing import Callable, Iterable, Protocol
from urllib.parse import urlsplit
import zlib

from .security import MAX_CLEAN_BYTES, MAX_RAW_BYTES, PolicyViolation, ValidatedTarget


@dataclass(frozen=True)
class FetchResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    peer_ip: str
    network_targets: tuple[tuple[str, str], ...] = ()


class Fetcher(Protocol):
    def fetch(
        self, target: ValidatedTarget, connect_timeout_s: float,
        read_timeout_s: float, total_timeout_s: float,
        request_guard: Callable[[str, str], None] | None = None,
    ) -> FetchResponse: ...


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str, timeout: float, context: ssl.SSLContext) -> None:
        super().__init__(host, 443, timeout=timeout, context=context)
        self._ip = ip

    def connect(self) -> None:
        raw = socket.create_connection((self._ip, 443), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class PinnedHttpsFetcher:
    """Single-attempt HTTPS fetch pinned to a prevalidated address."""

    def __init__(self, context: ssl.SSLContext | None = None, user_agent: str = "CryptoTrustCollector/1.0") -> None:
        self._context = context or ssl.create_default_context()
        self._user_agent = user_agent

    @staticmethod
    def _bounded_decompress(
        response: http.client.HTTPResponse, connection: _PinnedHTTPSConnection,
        total_expires: float, read_timeout_s: float,
    ) -> bytes:
        encoding = (response.getheader("Content-Encoding") or "identity").lower()
        decoder = None
        if encoding == "gzip":
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif encoding == "deflate":
            decoder = zlib.decompressobj()
        elif encoding not in ("identity", ""):
            raise PolicyViolation("invalid_source_schema", "Unsupported content encoding")
        output = bytearray()
        compressed = 0
        while True:
            remaining = total_expires - time.monotonic()
            if remaining <= 0:
                raise PolicyViolation("fetch_timeout", "Static collection total timeout was exceeded", True)
            if connection.sock:
                connection.sock.settimeout(min(read_timeout_s, remaining))
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            compressed += len(chunk)
            if compressed > MAX_RAW_BYTES:
                raise PolicyViolation("payload_too_large", "Compressed source payload exceeds limit")
            data = decoder.decompress(chunk, MAX_RAW_BYTES + 1 - len(output)) if decoder else chunk
            output.extend(data)
            if len(output) > MAX_RAW_BYTES or (decoder and decoder.unconsumed_tail):
                raise PolicyViolation("payload_too_large", "Decompressed source payload exceeds limit")
        if decoder:
            output.extend(decoder.flush(MAX_RAW_BYTES + 1 - len(output)))
        if len(output) > MAX_RAW_BYTES:
            raise PolicyViolation("payload_too_large", "Decompressed source payload exceeds limit")
        return bytes(output)

    def fetch(
        self, target: ValidatedTarget, connect_timeout_s: float,
        read_timeout_s: float, total_timeout_s: float,
        request_guard: Callable[[str, str], None] | None = None,
    ) -> FetchResponse:
        started = time.monotonic()
        total_expires = started + total_timeout_s
        ip = target.approved_ips[0]
        if request_guard:
            request_guard(target.url, ip)
        connection = _PinnedHTTPSConnection(target.host, ip, connect_timeout_s, self._context)
        try:
            parts = urlsplit(target.url)
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            connection.request("GET", path, headers={
                "Accept": "text/html,application/rss+xml,application/atom+xml,text/plain,application/json",
                "Accept-Encoding": "gzip, deflate",
                "User-Agent": self._user_agent,
            })
            if connection.sock:
                remaining = total_expires - time.monotonic()
                if remaining <= 0:
                    raise PolicyViolation("fetch_timeout", "Static collection total timeout was exceeded", True)
                connection.sock.settimeout(min(read_timeout_s, remaining))
            response = connection.getresponse()
            body = self._bounded_decompress(response, connection, total_expires, read_timeout_s)
            headers = {key.lower(): value for key, value in response.getheaders()}
            return FetchResponse(response.status, headers, body, ip, ((target.url, ip),))
        except (TimeoutError, socket.timeout) as exc:
            raise PolicyViolation("fetch_timeout", "Source fetch timed out", True) from exc
        except PolicyViolation:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            raise PolicyViolation("collector_unavailable", "Source transport is unavailable", True) from exc
        finally:
            connection.close()


class PlaywrightClient(Protocol):
    """Injected runtime must guard every navigation and subresource request."""

    def render(
        self, url: str, timeout_ms: int, max_bytes: int,
        request_guard: Callable[[str, str], None],
    ) -> FetchResponse: ...


class PlaywrightFetcher:
    def __init__(self, client: PlaywrightClient) -> None:
        self._client = client

    def fetch(
        self, target: ValidatedTarget, connect_timeout_s: float,
        read_timeout_s: float, total_timeout_s: float,
        request_guard: Callable[[str, str], None] | None = None,
    ) -> FetchResponse:
        del connect_timeout_s, read_timeout_s
        if request_guard is None:
            raise PolicyViolation("collector_unavailable", "Playwright request guard is required")
        try:
            response = self._client.render(
                target.url, max(1, int(total_timeout_s * 1000)), MAX_RAW_BYTES, request_guard,
            )
        except PolicyViolation:
            raise
        except TimeoutError as exc:
            raise PolicyViolation("fetch_timeout", "Playwright collection timed out", True) from exc
        except Exception as exc:
            raise PolicyViolation("unexpected_provider_error", "Playwright provider failed unexpectedly") from exc
        if len(response.body) > MAX_RAW_BYTES:
            raise PolicyViolation("payload_too_large", "Rendered source payload exceeds limit")
        return response


class _VisibleTextParser(HTMLParser):
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._blocked = 0
        self._stack: list[tuple[str, bool]] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        hidden = any(key.lower() in {"hidden", "aria-hidden"} and value in (None, "", "true") for key, value in attrs)
        blocked = normalized in {"script", "style", "noscript", "template", "svg"} or hidden
        if normalized not in self._VOID:
            self._stack.append((normalized, blocked))
            self._blocked += int(blocked)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == normalized:
                removed = self._stack[index:]
                del self._stack[index:]
                self._blocked -= sum(int(item[1]) for item in removed)
                break

    def handle_data(self, data: str) -> None:
        if not self._blocked:
            stripped = " ".join(data.split())
            if stripped:
                self.parts.append(stripped)


def _decode(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    for item in content_type.split(";")[1:]:
        if item.strip().lower().startswith("charset="):
            charset = item.split("=", 1)[1].strip().strip('"')
    try:
        return body.decode(charset, errors="strict")
    except (LookupError, UnicodeDecodeError) as exc:
        raise PolicyViolation("invalid_source_schema", "Source text encoding is invalid") from exc


def clean_content(body: bytes, content_type: str) -> tuple[str, str]:
    media_type = content_type.split(";", 1)[0].strip().lower() or "text/plain"
    allowed = {"text/html", "text/plain", "application/json", "text/csv", "application/rss+xml", "application/atom+xml"}
    if media_type not in allowed:
        raise PolicyViolation("invalid_source_schema", "Source media type is unsupported")
    text = _decode(body, content_type)
    if media_type == "text/html":
        parser = _VisibleTextParser()
        parser.feed(text)
        cleaned = "\n".join(parser.parts)
        output_media = "text/html"
    elif media_type in {"application/rss+xml", "application/atom+xml"}:
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise PolicyViolation("invalid_source_schema", "RSS or Atom document is invalid") from exc
        parts: list[str] = []
        for element in root.iter():
            local = element.tag.rsplit("}", 1)[-1].lower()
            if local in {"title", "description", "summary", "content", "published", "updated", "link"}:
                value = (element.text or element.attrib.get("href") or "").strip()
                if value:
                    parts.append(" ".join(value.split()))
        cleaned = "\n".join(parts)
        output_media = "text/plain"
    else:
        cleaned = text
        output_media = media_type
    if not cleaned:
        raise PolicyViolation("invalid_source_schema", "Source content is empty after cleaning")
    if len(cleaned.encode("utf-8")) > MAX_CLEAN_BYTES:
        raise PolicyViolation("payload_too_large", "Cleaned source content exceeds limit")
    return cleaned, output_media
