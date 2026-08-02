"""Nova Web Grounding URL-discovery-only boundary for PA70."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from typing import Callable, Mapping, Protocol, Sequence
import time

from ..collectors.adapter import EventSink, NullEventSink, SecureSourceCollector, _utc_text
from ..collectors.security import LocalDeadline, PolicyViolation, SCHEMA_VERSION


class GroundingDiscoveryClient(Protocol):
    def discover_urls(
        self, *, query: str, source_category: str, assets: Sequence[str],
        reporting_range: Mapping[str, str], timeout_s: float,
    ) -> Sequence[str]: ...


class NovaWebGroundingCollector:
    """Discovers URLs, then delegates every byte fetch to SecureSourceCollector.

    The injected client is not given plan mutation authority and its response may
    contain URLs only. It receives no fetch/storage callbacks or credentials.
    """

    contract_version = "source-collector-1.0.0-internal-mapping"
    provider_version = "nova-web-grounding-boundary-1.0.0"
    hidden_retries = 0
    waiting_for_core_interface = True

    def __init__(
        self, discovery_client: GroundingDiscoveryClient,
        collector: SecureSourceCollector, *, discovery_timeout_s: float = 10.0,
        event_sink: EventSink | None = None,
        now_utc: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if not 0 < discovery_timeout_s <= 15:
            raise ValueError("discovery_timeout_s must be in (0, 15]")
        self._client = discovery_client
        self._collector = collector
        self._timeout = discovery_timeout_s
        self._events = event_sink or NullEventSink()
        self._now = now_utc or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic

    def _emit(self, request: Mapping[str, object], status: str, count: int, code: str | None) -> None:
        event = {
            "schema_version": SCHEMA_VERSION, "step": "discover_source_urls",
            "tool": "nova_web_grounding", "status": status, "retry_count": 0,
            "task_id": request.get("task_id"), "execution_id": request.get("execution_id"),
            "operation_id": request.get("operation_id"),
            "sanitized_parameters": {"source_category": request.get("source_category")},
            "result_summary": {"discovered_url_count": count, "error_code": code},
        }
        try:
            self._events.emit(event)
        except Exception:
            return

    def discover_and_collect(self, request: Mapping[str, object]) -> dict:
        started = self._now()
        started_mono = self._monotonic()
        try:
            SecureSourceCollector._validate_request(request)
            if request["approved_urls"]:
                raise PolicyViolation("invalid_source_schema", "Grounding discovery requires an empty approved URL list")
            deadline = LocalDeadline(dict(request["deadline"]), self._timeout, self._now, self._monotonic)
            timeout_s = deadline.require_time()
            discovered = self._client.discover_urls(
                query=str(request["approved_query"]),
                source_category=str(request["source_category"]),
                assets=tuple(request["assets"]),
                reporting_range=deepcopy(request["reporting_range"]),
                timeout_s=timeout_s,
            )
            if isinstance(discovered, (str, bytes)) or not isinstance(discovered, Sequence):
                raise PolicyViolation("invalid_source_schema", "Grounding response must contain URLs only")
            urls = list(discovered)
            if len(urls) > 100 or any(not isinstance(url, str) for url in urls):
                raise PolicyViolation("invalid_source_schema", "Grounding URL result is invalid")
            urls = list(dict.fromkeys(urls))
            self._emit(request, "completed", len(urls), None)
            recollection_request = deepcopy(dict(request))
            recollection_request["approved_urls"] = urls
            # New bounded operation identity distinguishes discovery from controlled recollection.
            digest = hashlib.sha256(str(request["operation_id"]).encode()).hexdigest()[:24].upper()
            recollection_request["operation_id"] = "OP-GR-" + digest
            recollection_request["deadline"] = deepcopy(request["deadline"])
            recollection_request["deadline"]["operation_id"] = recollection_request["operation_id"]
            return self._collector.collect(recollection_request)
        except TimeoutError:
            violation = PolicyViolation("fetch_timeout", "Grounding discovery timed out", True)
        except PolicyViolation as caught:
            violation = caught
        except Exception:
            violation = PolicyViolation("unexpected_provider_error", "Grounding provider failed unexpectedly")
        self._emit(request, "failed", 0, violation.code)
        duration = min(30000, max(0, int((self._monotonic() - started_mono) * 1000)))
        return {
            "schema_version": SCHEMA_VERSION, "operation_id": request.get("operation_id", "OP-GROUNDING-UNKNOWN"),
            "outcome": "failed", "records": [], "started_at": _utc_text(started),
            "finished_at": _utc_text(self._now()), "duration_ms": duration,
            "retry_count": 0,
            "issues": [{"code": violation.code, "retryable": violation.retryable, "safe_message": violation.safe_message}],
            "limitations": ["Grounding URL discovery was unavailable"],
        }
