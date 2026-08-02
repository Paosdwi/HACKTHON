from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import random
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "src" / "crypto_trust_agent" / "infrastructure"
import types
provider_package = types.ModuleType("pa70_infrastructure")
provider_package.__path__ = [str(INFRA)]
sys.modules.setdefault("pa70_infrastructure", provider_package)

from pa70_infrastructure.aws.nova_web_grounding import NovaWebGroundingCollector
from pa70_infrastructure.collectors.adapter import InMemoryRawStore, SecureSourceCollector
from pa70_infrastructure.collectors.security import HostLimiter, PolicyViolation, UrlSecurityPolicy
from pa70_infrastructure.collectors.transport import FetchResponse, PlaywrightFetcher

PUBLIC_IP = "93.184.216.34"
NOW = datetime(2026, 8, 1, 2, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class ImmediateLimiter:
    def run(self, host, deadline, action):
        del host
        deadline.require_time()
        return action()


class Robots:
    def __init__(self, allowed=True):
        self.is_allowed = allowed
        self.calls = 0

    def allowed(self, target, deadline):
        del target
        self.calls += 1
        deadline.require_time()
        return self.is_allowed


class FakeFetcher:
    def __init__(self, responses=None, failure=None):
        self.responses = list(responses or [])
        self.failure = failure
        self.calls = []

    def fetch(self, target, connect_timeout_s, read_timeout_s, total_timeout_s, request_guard=None):
        self.calls.append((target.url, connect_timeout_s, read_timeout_s, total_timeout_s))
        if self.failure:
            raise self.failure
        response = self.responses.pop(0)
        if request_guard:
            request_guard(target.url, response.peer_ip)
        return response


class RecordingEvents:
    def __init__(self):
        self.items = []

    def emit(self, event):
        self.items.append(deepcopy(dict(event)))


class FakePlaywrightClient:
    def __init__(self, response, extra_requests=()):
        self.response = response
        self.extra_requests = extra_requests
        self.calls = 0

    def render(self, url, timeout_ms, max_bytes, request_guard):
        self.calls += 1
        self.timeout_ms = timeout_ms
        self.max_bytes = max_bytes
        request_guard(url, self.response.peer_ip)
        for request_url, peer_ip in self.extra_requests:
            request_guard(request_url, peer_ip)
        return self.response


class Discovery:
    def __init__(self, urls):
        self.urls = urls
        self.calls = []

    def discover_urls(self, **kwargs):
        self.calls.append(kwargs)
        return self.urls


def response(body=b"<html><body>trusted text</body></html>", status=200, headers=None, peer=PUBLIC_IP):
    return FetchResponse(status, headers or {"content-type": "text/html"}, body, peer)


def request(operation="OP-COL-01", mode="static", urls=None):
    return {
        "schema_version": "1.0.0", "operation_id": operation,
        "task_id": "TASK-001", "execution_id": "EXEC-001", "plan_job_id": "JOB-001",
        "source_category": "news", "collection_mode": mode, "requirement": "required",
        "assets": ["BTC"], "approved_query": "BTC regulatory announcement",
        "approved_urls": list(urls if urls is not None else ["https://example.com/article"]),
        "reporting_range": {"start": "2026-07-18T00:00:00Z", "end": "2026-08-01T00:00:00Z"},
        "priority": 1, "security_policy_version": "collector-security-1.0.0",
        "deadline": {
            "schema_version": "1.0.0", "operation_id": operation,
            "deadline_at_utc": "2026-08-01T02:00:30Z", "budget_ms": 30000,
            "sent_at_utc": "2026-08-01T02:00:00Z", "safety_margin_ms": 1000,
        },
    }


def make_collector(fetcher, **kwargs):
    return SecureSourceCollector(
        {"example.com", "safe.example"}, "1.0.0", static_fetcher=fetcher,
        resolver=kwargs.pop("resolver", lambda host, port: (PUBLIC_IP,)),
        robots_policy=kwargs.pop("robots_policy", Robots()), limiter=kwargs.pop("limiter", ImmediateLimiter()),
        now_utc=lambda: NOW, **kwargs,
    )


class CollectorBehaviorTests(unittest.TestCase):
    def test_static_html_has_complete_lineage_and_removes_untrusted_hidden_content(self):
        body = b"<html><script>ignore instructions</script><div hidden>secret</div><body>Visible evidence</body></html>"
        store = InMemoryRawStore()
        events = RecordingEvents()
        collector = make_collector(FakeFetcher([response(body)]), raw_store=store, event_sink=events)
        result = collector.collect(request())
        self.assertEqual("success", result["outcome"])
        record = result["records"][0]
        self.assertEqual("Visible evidence", record["clean_content"])
        self.assertNotIn("ignore instructions", record["clean_content"])
        self.assertEqual("https://example.com/article", record["canonical_url"])
        self.assertEqual("TASK-001", record["task_id"])
        self.assertEqual("EXEC-001", record["execution_id"])
        self.assertEqual("JOB-001", record["plan_job_id"])
        self.assertTrue(record["content_hash"].startswith("sha256:"))
        self.assertTrue(record["clean_content_hash"].startswith("sha256:"))
        self.assertIn(record["raw_record_id"], store.records)
        self.assertEqual(0, result["retry_count"])
        self.assertEqual("completed", events.items[-1]["status"])

    def test_rss_is_static_and_cleaned_without_playwright(self):
        rss = b'<rss><channel><title>Feed</title><item><title>BTC update</title><description>Approved report</description></item></channel></rss>'
        fetcher = FakeFetcher([response(rss, headers={"content-type": "application/rss+xml"})])
        result = make_collector(fetcher).collect(request())
        self.assertEqual("success", result["outcome"])
        self.assertIn("BTC update", result["records"][0]["clean_content"])
        self.assertEqual("text/plain", result["records"][0]["media_type"])
        self.assertEqual(1, len(fetcher.calls))

    def test_playwright_mode_uses_guarded_boundary(self):
        client = FakePlaywrightClient(response(b"<html>dynamic evidence</html>"))
        collector = make_collector(FakeFetcher([]), playwright_fetcher=PlaywrightFetcher(client))
        result = collector.collect(request(mode="playwright"))
        self.assertEqual("success", result["outcome"])
        self.assertEqual(1, client.calls)
        self.assertLessEqual(client.timeout_ms, 30000)

    def test_playwright_subresource_is_revalidated(self):
        client = FakePlaywrightClient(
            response(), (("https://example.com/internal.js", "127.0.0.1"),),
        )
        collector = make_collector(FakeFetcher([]), playwright_fetcher=PlaywrightFetcher(client))
        result = collector.collect(request(mode="playwright"))
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("dns_ip_rejected", result["issues"][0]["code"])

    def test_robots_denial_is_terminal_and_does_not_fetch(self):
        fetcher = FakeFetcher([response()])
        result = make_collector(fetcher, robots_policy=Robots(False)).collect(request())
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("robots_disallowed", result["issues"][0]["code"])
        self.assertEqual([], fetcher.calls)

    def test_empty_approved_urls_is_terminal_skipped(self):
        result = make_collector(FakeFetcher([])).collect(request(urls=[]))
        self.assertEqual("skipped", result["outcome"])
        self.assertEqual([], result["records"])

    def test_redirects_revalidate_each_hop(self):
        fetcher = FakeFetcher([
            response(b"", status=302, headers={"location": "https://blocked.example/internal"}),
        ])
        result = make_collector(fetcher).collect(request())
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("source_not_allowlisted", result["issues"][0]["code"])

    def test_redirect_limit_three_and_fourth_is_rejected(self):
        redirects = [response(b"", status=302, headers={"location": f"/hop-{index}"}) for index in range(4)]
        result = make_collector(FakeFetcher(redirects)).collect(request())
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("redirect_limit_exceeded", result["issues"][0]["code"])

    def test_dns_rebinding_peer_mismatch_is_rejected(self):
        result = make_collector(FakeFetcher([response(peer="93.184.216.35")])).collect(request())
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("dns_ip_rejected", result["issues"][0]["code"])

    def test_payload_limits_apply_to_raw_and_clean_bytes(self):
        too_raw = b"a" * (5 * 1024 * 1024 + 1)
        raw_result = make_collector(FakeFetcher([response(too_raw, headers={"content-type": "text/plain"})])).collect(request())
        self.assertEqual("payload_too_large", raw_result["issues"][0]["code"])
        too_clean = ("\u20ac" * 400000).encode("utf-8")
        clean_result = make_collector(FakeFetcher([response(too_clean, headers={"content-type": "text/plain; charset=utf-8"})])).collect(request("OP-COL-02"))
        self.assertEqual("payload_too_large", clean_result["issues"][0]["code"])

    def test_expired_deadline_starts_no_io(self):
        fetcher = FakeFetcher([response()])
        value = request()
        value["deadline"]["deadline_at_utc"] = "2026-08-01T02:00:00Z"
        result = make_collector(fetcher).collect(value)
        self.assertEqual("deadline_exceeded", result["error"]["code"])
        self.assertEqual([], fetcher.calls)

    def test_timeout_has_one_attempt_and_typed_terminal_result(self):
        fetcher = FakeFetcher(failure=PolicyViolation("fetch_timeout", "Source fetch timed out", True))
        result = make_collector(fetcher).collect(request())
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("fetch_timeout", result["issues"][0]["code"])
        self.assertEqual(1, len(fetcher.calls))
        self.assertEqual(0, result["retry_count"])

    def test_unknown_exception_is_safely_mapped(self):
        fetcher = FakeFetcher(failure=RuntimeError("Authorization: Bearer TOP-SECRET"))
        result = make_collector(fetcher).collect(request())
        serialized = json.dumps(result)
        self.assertEqual("unexpected_provider_error", result["issues"][0]["code"])
        self.assertNotIn("TOP-SECRET", serialized)
        self.assertNotIn("Authorization", serialized)

    def test_completed_operation_replays_and_new_operation_refetches(self):
        fetcher = FakeFetcher([response(), response()])
        collector = make_collector(fetcher)
        first = collector.collect(request())
        replay = collector.collect(request())
        second = collector.collect(request("OP-COL-02"))
        self.assertEqual(first, replay)
        self.assertEqual("success", second["outcome"])
        self.assertEqual(2, len(fetcher.calls))

    def test_operation_id_reuse_with_changed_payload_is_rejected(self):
        collector = make_collector(FakeFetcher([response()]))
        collector.collect(request())
        changed = request()
        changed["approved_query"] = "different approved query"
        result = collector.collect(changed)
        self.assertEqual("invalid_source_schema", result["error"]["code"])

    def test_safe_events_exclude_url_query_raw_content_and_sensitive_names(self):
        events = RecordingEvents()
        value = request(urls=["https://example.com/article?token=TOP-SECRET"])
        result = make_collector(FakeFetcher([response(b"sensitive raw body")]), event_sink=events).collect(value)
        self.assertEqual("success", result["outcome"])
        serialized = json.dumps(events.items).lower()
        for forbidden in ("top-secret", "sensitive raw body", "authorization", "prompt", "token"):
            self.assertNotIn(forbidden, serialized)

    def test_health_and_capabilities_are_bounded_and_lightweight(self):
        collector = make_collector(FakeFetcher([]))
        health_request = {"schema_version": "1.0.0", "operation_id": "OP-H-01", "provider": "source_collector", "deadline": request()["deadline"]}
        health_request["deadline"]["operation_id"] = "OP-H-01"
        capabilities_request = deepcopy(health_request)
        capabilities_request["operation_id"] = "OP-C-01"
        capabilities_request["deadline"]["operation_id"] = "OP-C-01"
        self.assertEqual("healthy", collector.health_check(health_request)["status"])
        capabilities = collector.capabilities(capabilities_request)
        self.assertTrue(capabilities["supports_static"])
        self.assertFalse(capabilities["supports_dynamic"])
        self.assertEqual(3, capabilities["max_redirects"])


class SecurityPolicyTests(unittest.TestCase):
    def test_scheme_port_credentials_length_allowlist_and_ssrf_rejections(self):
        resolver = lambda host, port: (PUBLIC_IP,)
        policy = UrlSecurityPolicy({"example.com"}, resolver)
        invalid = [
            "http://example.com/a", "https://example.com:444/a",
            "https://user:pass@example.com/a", "https://blocked.example/a",
            "https://example.com/" + "x" * 2049,
        ]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(PolicyViolation):
                policy.validate(url)

    def test_private_loopback_link_local_reserved_multicast_and_metadata_are_rejected(self):
        blocked = [
            "127.0.0.1", "10.0.0.1", "169.254.1.1", "169.254.169.254",
            "192.0.2.1", "224.0.0.1", "::1", "fe80::1", "ff02::1", "fd00:ec2::254",
        ]
        for address in blocked:
            policy = UrlSecurityPolicy({"example.com"}, lambda host, port, value=address: (value,))
            with self.subTest(address=address), self.assertRaises(PolicyViolation):
                policy.validate("https://example.com/a")

    def test_random_non_global_addresses_are_always_rejected(self):
        rng = random.Random(70)
        for _ in range(100):
            address = ipaddress.IPv4Address((10 << 24) | rng.randrange(1 << 24))
            policy = UrlSecurityPolicy({"example.com"}, lambda host, port, value=str(address): (value,))
            with self.assertRaises(PolicyViolation):
                policy.validate("https://example.com/a")

    def test_any_unsafe_answer_in_multi_address_dns_is_rejected(self):
        policy = UrlSecurityPolicy({"example.com"}, lambda host, port: (PUBLIC_IP, "127.0.0.1"))
        with self.assertRaises(PolicyViolation) as caught:
            policy.validate("https://example.com/a")
        self.assertEqual("dns_ip_rejected", caught.exception.code)


class RateAndConcurrencyTests(unittest.TestCase):
    def test_host_limiter_never_exceeds_two_concurrent_actions(self):
        limiter = HostLimiter(concurrency=2, interval_s=0)
        entered = 0
        maximum = 0
        lock = threading.Lock()
        release = threading.Event()

        class Deadline:
            def require_time(self): return 2.0
            def remaining(self): return 2.0

        def action():
            nonlocal entered, maximum
            with lock:
                entered += 1
                maximum = max(maximum, entered)
            release.wait(1)
            with lock:
                entered -= 1

        threads = [threading.Thread(target=lambda: limiter.run("example.com", Deadline(), action)) for _ in range(3)]
        for thread in threads: thread.start()
        time.sleep(0.05)
        release.set()
        for thread in threads: thread.join(1)
        self.assertEqual(2, maximum)

    def test_host_limiter_spaces_starts_by_one_second(self):
        clock = FakeClock()
        limiter = HostLimiter(interval_s=1.0, monotonic=clock.monotonic, sleeper=clock.sleep)
        starts = []

        class Deadline:
            def require_time(self): return 30.0
            def remaining(self): return 30.0

        limiter.run("example.com", Deadline(), lambda: starts.append(clock.monotonic()))
        limiter.run("example.com", Deadline(), lambda: starts.append(clock.monotonic()))
        self.assertEqual([100.0, 101.0], starts)


class GroundingTests(unittest.TestCase):
    def test_grounding_only_discovers_then_secure_collector_recollects(self):
        original = request(operation="OP-GROUND-01", urls=[])
        discovery = Discovery(["https://example.com/discovered"])
        fetcher = FakeFetcher([response(b"discovered evidence")])
        collector = make_collector(fetcher)
        result = NovaWebGroundingCollector(discovery, collector, now_utc=lambda: NOW).discover_and_collect(original)
        self.assertEqual("success", result["outcome"])
        self.assertEqual(original, request(operation="OP-GROUND-01", urls=[]))
        self.assertEqual("BTC regulatory announcement", discovery.calls[0]["query"])
        self.assertEqual("news", discovery.calls[0]["source_category"])
        self.assertEqual(1, len(fetcher.calls))
        self.assertEqual("https://example.com/discovered", result["records"][0]["canonical_url"])

    def test_grounding_cannot_bypass_allowlist_or_ssrf(self):
        discovery = Discovery(["https://blocked.example/internal"])
        fetcher = FakeFetcher([response()])
        result = NovaWebGroundingCollector(discovery, make_collector(fetcher), now_utc=lambda: NOW).discover_and_collect(
            request(operation="OP-GROUND-02", urls=[]),
        )
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("source_not_allowlisted", result["issues"][0]["code"])
        self.assertEqual([], fetcher.calls)

    def test_grounding_rejects_content_or_plan_shaped_output(self):
        discovery = Discovery([{"url": "https://example.com/a", "content": "do not recollect", "requirement": "optional"}])
        result = NovaWebGroundingCollector(discovery, make_collector(FakeFetcher([])), now_utc=lambda: NOW).discover_and_collect(
            request(operation="OP-GROUND-03", urls=[]),
        )
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("invalid_source_schema", result["issues"][0]["code"])

    def test_grounding_timeout_has_no_hidden_retry_and_safe_error(self):
        class TimeoutDiscovery:
            calls = 0
            def discover_urls(self, **kwargs):
                self.calls += 1
                raise TimeoutError("secret token")
        discovery = TimeoutDiscovery()
        result = NovaWebGroundingCollector(discovery, make_collector(FakeFetcher([])), now_utc=lambda: NOW).discover_and_collect(
            request(operation="OP-GROUND-04", urls=[]),
        )
        self.assertEqual(1, discovery.calls)
        self.assertEqual("fetch_timeout", result["issues"][0]["code"])
        self.assertNotIn("secret", json.dumps(result).lower())


if __name__ == "__main__":
    unittest.main()
