from __future__ import annotations

import asyncio
import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from urllib.parse import urlencode

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.planning import QuestionType  # noqa: E402
from crypto_trust_agent.application.use_cases.create_task import CreateTaskUseCase  # noqa: E402
from crypto_trust_agent.infrastructure.fakes import FakeClock, FakePlatformStore, FakeTaskRepository  # noqa: E402
from crypto_trust_agent.infrastructure.identity import (  # noqa: E402
    CognitoPrincipalBoundary,
    FakeCognitoTokenVerifier,
    HmacPrincipalPseudonymizer,
)
from crypto_trust_agent.presentation.api import create_task_asgi_app  # noqa: E402

ISSUER = "https://cognito-idp.ap-southeast-1.amazonaws.com/ap-southeast-1_example"
AUDIENCE = "crypto-trust-client"
SUBJECT = "private-cognito-subject-456"
TOKEN = "header.payload.signature-canary"
HMAC_KEY = b"0123456789abcdef0123456789abcdef"


class IDs:
    def __init__(self) -> None:
        self.lock = Lock()
        self.value = 0

    def __call__(self, prefix: str) -> str:
        with self.lock:
            self.value += 1
            return f"{prefix}{self.value:08X}"


class Audit:
    def __init__(self) -> None:
        self.records = []

    def __call__(self, item) -> None:
        self.records.append(item)


def classify(question: str, assets: tuple[str, ...]) -> QuestionType:
    if len(assets) == 2 and question.startswith("Compare"):
        return QuestionType.ASSET_COMPARISON
    if len(assets) == 1 and question.startswith("Validate"):
        return QuestionType.HYPOTHESIS_VALIDATION
    if len(assets) == 1 and question.startswith("Status"):
        return QuestionType.MARKET_STATUS
    raise ValueError("unsupported question type")


def payload() -> dict[str, object]:
    return {
        "question": "Status of BTC",
        "assets": ["BTC"],
        "timeframe": {"start": "2026-07-18T00:00:00Z", "end": "2026-08-01T00:00:00Z"},
        "formal_run": True,
    }


async def asgi_request(
    app,
    *,
    body: dict[str, object],
    headers: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    raw_query: bytes | None = None,
):
    raw_body = json.dumps(body).encode("utf-8")
    sent = []
    consumed = False

    async def receive():
        nonlocal consumed
        if consumed:
            return {"type": "http.disconnect"}
        consumed = True
        return {"type": "http.request", "body": raw_body, "more_body": False}

    async def send(message):
        sent.append(message)

    all_headers = {"content-type": "application/json", **(headers or {})}
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/v1/tasks",
        "raw_path": b"/api/v1/tasks",
        "query_string": raw_query if raw_query is not None else urlencode(query or {}).encode("ascii"),
        "headers": [(key.lower().encode("ascii"), value.encode("utf-8")) for key, value in all_headers.items()],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 443),
    }
    await app(scope, receive, send)
    start = next(item for item in sent if item["type"] == "http.response.start")
    response_body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    return start["status"], dict(start.get("headers", ())), json.loads(response_body)


class TaskApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock("2026-08-01T02:00:00Z")
        self.store = FakePlatformStore()
        self.audit = Audit()
        use_case = CreateTaskUseCase(FakeTaskRepository(self.store, self.clock), self.clock, classify, IDs(), self.audit)
        verifier = FakeCognitoTokenVerifier(
            {TOKEN: {"iss": ISSUER, "aud": AUDIENCE, "exp": 1785553200, "sub": SUBJECT, "cognito:groups": ["Users"]}},
            rejected_tokens=("bad-signature",),
        )
        authenticator = CognitoPrincipalBoundary(
            verifier,
            ISSUER,
            AUDIENCE,
            HmacPrincipalPseudonymizer("k2026-01", HMAC_KEY),
            now_provider=lambda: datetime(2026, 8, 1, 2, 0, tzinfo=UTC),
        )
        self.app = create_task_asgi_app(use_case, authenticator)
        self.auth = {"authorization": f"Bearer {TOKEN}"}

    def request(self, **kwargs):
        return asyncio.run(asgi_request(self.app, **kwargs))

    def test_created_and_reused_http_mapping(self) -> None:
        created_status, _, created = self.request(body=payload(), headers=self.auth)
        reused_status, _, reused = self.request(body=payload(), headers=self.auth)
        self.assertEqual(201, created_status)
        self.assertEqual(200, reused_status)
        self.assertEqual("created", created["idempotency_outcome"])
        self.assertEqual("reused", reused["idempotency_outcome"])
        self.assertEqual(created["task_id"], reused["task_id"])
        self.assertNotIn("execution_id", created)

    def test_ten_concurrent_api_requests_have_one_created_task(self) -> None:
        with ThreadPoolExecutor(max_workers=10) as executor:
            responses = list(
                executor.map(
                    lambda _: self.request(body=payload(), headers=self.auth),
                    range(10),
                )
            )
        bodies = [item[2] for item in responses]
        self.assertEqual(1, sum(item["idempotency_outcome"] == "created" for item in bodies))
        self.assertEqual(9, sum(item["idempotency_outcome"] == "reused" for item in bodies))
        self.assertEqual(1, len({item["task_id"] for item in bodies}))
        self.assertEqual(1, len(self.store.tasks))

    def test_missing_invalid_or_bad_signature_authorization_maps_401(self) -> None:
        for headers in ({}, {"authorization": "Basic abc"}, {"authorization": "Bearer bad-signature"}):
            with self.subTest(headers=headers):
                status, response_headers, result = self.request(body=payload(), headers=headers)
                self.assertEqual(401, status)
                self.assertEqual("authentication_required", result["error"]["code"])
                self.assertIn(b"application/json", response_headers[b"content-type"])
        self.assertEqual({}, self.store.tasks)

    def test_body_query_and_custom_header_identity_override_map_422(self) -> None:
        body_override = payload() | {"user_id": "attacker"}
        attacks = (
            {"body": body_override, "headers": self.auth},
            {"body": payload(), "headers": self.auth, "query": {"sub": "attacker"}},
            {"body": payload(), "headers": self.auth | {"x-user-id": "attacker"}},
            {"body": payload(), "headers": self.auth | {"x-cognito-groups": "CryptoTrustAdmins"}},
        )
        for attack in attacks:
            with self.subTest(attack=attack):
                status, _, result = self.request(**attack)
                self.assertEqual(422, status)
                self.assertEqual("identity_override_forbidden", result["error"]["code"])
        self.assertEqual({}, self.store.tasks)

    def test_invalid_business_payload_maps_422_without_task_creation(self) -> None:
        invalid = payload() | {"assets": ["DOGE"]}
        status, _, result = self.request(body=invalid, headers=self.auth)
        self.assertEqual(422, status)
        self.assertEqual("invalid_task_request", result["error"]["code"])
        self.assertEqual({}, self.store.tasks)
        self.assertEqual(1, sum(len(items) for items in self.store.task_create_counters.values()))

    def test_eleventh_valid_request_maps_429_and_does_not_create_another_task(self) -> None:
        for _ in range(10):
            self.assertIn(self.request(body=payload(), headers=self.auth)[0], {200, 201})
        status, headers, result = self.request(body=payload(), headers=self.auth)
        self.assertEqual(429, status)
        self.assertEqual("task_create_rate_limited", result["error"]["code"])
        self.assertEqual(b"1", headers[b"retry-after"])
        self.assertEqual(1, len(self.store.tasks))

    def test_oversized_body_and_non_ascii_query_are_safely_rejected(self) -> None:
        too_large = payload() | {"question": "x" * 70_000}
        status, _, result = self.request(body=too_large, headers=self.auth)
        self.assertEqual(413, status)
        self.assertEqual("request_too_large", result["error"]["code"])

        status, _, result = self.request(body=payload(), headers=self.auth, raw_query=b"\xff")
        self.assertEqual(422, status)
        self.assertEqual("invalid_task_request", result["error"]["code"])
        self.assertEqual({}, self.store.tasks)

    def test_logs_never_contain_raw_identity_token_claims_or_hmac_key(self) -> None:
        self.request(body=payload(), headers=self.auth)
        serialized = json.dumps([item.to_safe_dict() for item in self.audit.records], sort_keys=True)
        for canary in (SUBJECT, TOKEN, "CryptoTrustAdmins", HMAC_KEY.decode("ascii"), "authorization"):
            self.assertNotIn(canary, serialized.lower() if canary == "authorization" else serialized)
        self.assertRegex(serialized, r"hmac-sha256:k2026-01:[0-9a-f]{64}")


if __name__ == "__main__":
    unittest.main()


class FastApiCompositionTests(unittest.TestCase):
    def test_fastapi_mount_uses_the_verified_identity_and_use_case_boundary(self) -> None:
        from fastapi.testclient import TestClient
        from crypto_trust_agent.presentation.api import create_fastapi_app

        clock = FakeClock("2026-08-01T02:00:00Z")
        store = FakePlatformStore()
        use_case = CreateTaskUseCase(
            FakeTaskRepository(store, clock),
            clock,
            classify,
            IDs(),
            Audit(),
        )
        authenticator = CognitoPrincipalBoundary(
            FakeCognitoTokenVerifier(
                {
                    TOKEN: {
                        "iss": ISSUER,
                        "aud": AUDIENCE,
                        "exp": 1785553200,
                        "sub": SUBJECT,
                        "cognito:groups": ["Users"],
                    }
                }
            ),
            ISSUER,
            AUDIENCE,
            HmacPrincipalPseudonymizer("k2026-01", HMAC_KEY),
            now_provider=lambda: datetime(2026, 8, 1, 2, 0, tzinfo=UTC),
        )
        client = TestClient(create_fastapi_app(use_case, authenticator))

        created = client.post(
            "/api/v1/tasks",
            json=payload(),
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        unauthorized = client.post("/api/v1/tasks", json=payload())

        self.assertEqual(201, created.status_code)
        self.assertEqual("created", created.json()["idempotency_outcome"])
        self.assertEqual(401, unauthorized.status_code)
        self.assertEqual("authentication_required", unauthorized.json()["error"]["code"])
