import asyncio
import json
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.repositories import TaskRecordDTO
from crypto_trust_agent.application.use_cases.preflight import PreflightUseCase
from crypto_trust_agent.infrastructure.fakes import (
    FakeClock,
    FakePlatformStore,
    FakeProviderHealthProbe,
    FakeTaskReadinessProbe,
    FakeTaskRepository,
)
from crypto_trust_agent.infrastructure.identity import CognitoPrincipalBoundary, FakeCognitoTokenVerifier, HmacPrincipalPseudonymizer
from crypto_trust_agent.presentation.api import create_preflight_asgi_app, create_preflight_fastapi_app

ISSUER = "https://cognito-idp.ap-southeast-1.amazonaws.com/ap-southeast-1_example"
AUDIENCE = "crypto-trust-client"
SUBJECT = "private-cognito-subject-456"
TOKEN = "header.payload.signature-canary"
HMAC_KEY = b"0123456789abcdef0123456789abcdef"
HASH_A = "sha256:" + "a" * 64


class IDs:
    def __init__(self) -> None:
        self.lock = Lock()
        self.value = 0

    def __call__(self, prefix: str) -> str:
        with self.lock:
            self.value += 1
            return f"{prefix}{self.value:08X}"


def make_authenticator():
    return CognitoPrincipalBoundary(
        FakeCognitoTokenVerifier({TOKEN: {"iss": ISSUER, "aud": AUDIENCE, "exp": 1785553200, "sub": SUBJECT, "cognito:groups": ["Users"]}}),
        ISSUER,
        AUDIENCE,
        HmacPrincipalPseudonymizer("k2026-01", HMAC_KEY),
        now_provider=lambda: datetime(2026, 8, 1, 2, 0, tzinfo=UTC),
    )


def build_system():
    clock = FakeClock("2026-08-01T02:00:00Z")
    store = FakePlatformStore()
    store.tasks["TASK-001"] = TaskRecordDTO("TASK-001", SUBJECT, HASH_A, "ready_for_preflight", 1, "2026-08-01T02:00:00Z")
    store.proposed_tasks["TASK-001"] = {
        "task_id": "TASK-001", "question": "Status of BTC", "assets_requested_order": ("BTC",), "assets_canonical": ("BTC",),
        "timeframe": {"start": "2026-07-18T00:00:00Z", "end": "2026-08-01T00:00:00Z"}, "question_type": "market_status",
        "formal_run_intent": True, "sourcing_plan": {"ruleset_version": "planner-1.0.0"}, "analysis_plan": {"ruleset_version": "planner-1.0.0"},
    }
    local = FakeTaskReadinessProbe(store, clock)
    probes = (
        FakeProviderHealthProbe(clock, "nova", "extraction_v1"),
        FakeProviderHealthProbe(clock, "opus", "reasoning_v1"),
        FakeProviderHealthProbe(clock, "sagemaker", "market_regime_v1"),
        FakeProviderHealthProbe(clock, "external_allowlist", "collector_policy_v1"),
    )
    use_case = PreflightUseCase(FakeTaskRepository(store, clock), clock, local, *probes, IDs())
    return clock, store, local, probes, use_case


async def request(app, *, token=TOKEN, body=None, query=b"", extra_headers=()):
    raw = json.dumps({} if body is None else body).encode()
    sent = []
    consumed = False
    async def receive():
        nonlocal consumed
        if consumed:
            return {"type": "http.disconnect"}
        consumed = True
        return {"type": "http.request", "body": raw, "more_body": False}
    async def send(message):
        sent.append(message)
    headers = [(b"content-type", b"application/json"), *extra_headers]
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {"type": "http", "method": "POST", "path": "/api/v1/tasks/TASK-001/preflight", "query_string": query, "headers": headers}
    await app(scope, receive, send)
    start = next(item for item in sent if item["type"] == "http.response.start")
    payload = json.loads(b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body"))
    return start["status"], dict(start["headers"]), payload


class PreflightApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock, self.store, self.local, self.probes, use_case = build_system()
        self.app = create_preflight_asgi_app(use_case, make_authenticator())

    def call(self, **kwargs):
        return asyncio.run(request(self.app, **kwargs))

    def test_success_response_contains_binding_but_no_execution(self) -> None:
        status, _, body = self.call()
        self.assertEqual(200, status)
        self.assertTrue(body["ready"])
        self.assertEqual(60, body["ttl_seconds"])
        self.assertEqual("TASK-001", body["task_id"])
        self.assertIn("input_lock_hash", body)
        self.assertIn("dependency_snapshot_hash", body)
        self.assertNotIn("execution_id", body)
        self.assertEqual({}, self.store.executions)
        self.assertEqual({}, self.store.quota)

    def test_fourth_request_maps_429_without_additional_probe(self) -> None:
        for _ in range(3):
            self.assertEqual(200, self.call()[0])
        counts = (self.local.probe_count, *(item.probe_count for item in self.probes))
        status, headers, body = self.call()
        self.assertEqual(429, status)
        self.assertEqual("preflight_rate_limited", body["error"]["code"])
        self.assertGreaterEqual(int(headers[b"retry-after"]), 1)
        self.assertEqual(counts, (self.local.probe_count, *(item.probe_count for item in self.probes)))

    def test_identity_only_comes_from_verified_principal(self) -> None:
        attacks = (
            {"token": None},
            {"token": "bad-token"},
            {"body": {"sub": SUBJECT}},
            {"query": b"user_id=attacker"},
            {"extra_headers": ((b"x-user-id", SUBJECT.encode()),)},
        )
        for attack in attacks:
            with self.subTest(attack=attack):
                status, _, body = self.call(**attack)
                self.assertIn(status, {401, 422})
                self.assertIn(body["error"]["code"], {"authentication_required", "identity_override_forbidden"})
        self.assertEqual(0, self.local.probe_count)

    def test_cross_tenant_task_is_hidden_as_404_before_probe(self) -> None:
        self.store.tasks["TASK-001"] = TaskRecordDTO("TASK-001", "another-user", HASH_A, "ready_for_preflight", 1, "2026-08-01T02:00:00Z")
        status, _, body = self.call()
        self.assertEqual(404, status)
        self.assertEqual("task_not_found", body["error"]["code"])
        self.assertEqual(0, self.local.probe_count)


class PreflightFastApiCompositionTests(unittest.TestCase):
    def test_fastapi_mount_preserves_verified_identity_boundary(self) -> None:
        from fastapi.testclient import TestClient
        _, store, _, _, use_case = build_system()
        client = TestClient(create_preflight_fastapi_app(use_case, make_authenticator()))
        passed = client.post("/api/v1/tasks/TASK-001/preflight", json={}, headers={"Authorization": f"Bearer {TOKEN}"})
        denied = client.post("/api/v1/tasks/TASK-001/preflight", json={})
        self.assertEqual(200, passed.status_code)
        self.assertTrue(passed.json()["ready"])
        self.assertEqual(401, denied.status_code)
        self.assertEqual({}, store.executions)


if __name__ == "__main__":
    unittest.main()
