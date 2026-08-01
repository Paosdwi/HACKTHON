import asyncio
import json
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.common import DeadlineDTO
from crypto_trust_agent.application.dto.repositories import PreflightRecordDTO, TaskRecordDTO, TransitionExecutionRequestDTO
from crypto_trust_agent.application.use_cases.start_formal_execution import StartFormalExecutionUseCase
from crypto_trust_agent.infrastructure.fakes import FakeClock, FakeExecutionRepository, FakePlatformStore, FakeTaskRepository
from crypto_trust_agent.infrastructure.identity import CognitoPrincipalBoundary, FakeCognitoTokenVerifier, HmacPrincipalPseudonymizer
from crypto_trust_agent.presentation.api import create_execution_asgi_app, create_execution_fastapi_app

ISSUER = "https://cognito-idp.ap-southeast-1.amazonaws.com/ap-southeast-1_example"
AUDIENCE = "crypto-trust-client"
SUBJECT = "private-cognito-subject-456"
USER_TOKEN = "verified-user-token"
ADMIN_TOKEN = "verified-admin-token"
HMAC_KEY = b"0123456789abcdef0123456789abcdef"
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64


class IDs:
    def __init__(self) -> None:
        self.lock = Lock()
        self.value = 0

    def __call__(self, prefix: str) -> str:
        with self.lock:
            self.value += 1
            return f"{prefix}{self.value:08X}"


def seed_ready(store, task_id="TASK-001", preflight_id="PF-001", subject=SUBJECT):
    checked = "2026-08-01T02:00:00Z"
    store.tasks[task_id] = TaskRecordDTO(task_id, subject, HASH_A, "ready_for_execution", 2, checked)
    store.preflights[task_id] = [PreflightRecordDTO(
        preflight_id, task_id, 1, HASH_B, HASH_C, checked, "2026-08-01T02:01:00Z", True,
        ({"name": "official_dataset", "required": True, "status": "healthy", "safe_reason_code": None},),
        {"items": ()},
    )]


def authenticator():
    common = {"iss": ISSUER, "aud": AUDIENCE, "exp": 1785553200, "sub": SUBJECT}
    return CognitoPrincipalBoundary(
        FakeCognitoTokenVerifier({
            USER_TOKEN: {**common, "cognito:groups": ["Users"]},
            ADMIN_TOKEN: {**common, "cognito:groups": ["CryptoTrustAdmins"]},
        }),
        ISSUER,
        AUDIENCE,
        HmacPrincipalPseudonymizer("k2026-01", HMAC_KEY),
        now_provider=lambda: datetime(2026, 8, 1, 2, 0, tzinfo=UTC),
    )


def body(operation="OP-API-START-001", execution="EXEC-001"):
    return {"operation_id": operation, "execution_id": execution, "preflight_id": "PF-001", "input_lock_hash": HASH_B}


async def request(app, *, token=USER_TOKEN, request_body=None, path="/api/v1/tasks/TASK-001/executions", query=b"", extra_headers=()):
    raw = json.dumps(body() if request_body is None else request_body).encode("utf-8")
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
    scope = {"type": "http", "method": "POST", "path": path, "query_string": query, "headers": headers}
    await app(scope, receive, send)
    start = next(item for item in sent if item["type"] == "http.response.start")
    payload = json.loads(b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body"))
    return start["status"], dict(start["headers"]), payload


class ExecutionApiTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock("2026-08-01T02:00:00Z")
        self.store = FakePlatformStore()
        seed_ready(self.store)
        self.tasks = FakeTaskRepository(self.store, self.clock)
        self.executions = FakeExecutionRepository(self.store, self.clock)
        self.use_case = StartFormalExecutionUseCase(self.tasks, self.executions, self.clock, IDs())
        self.app = create_execution_asgi_app(self.use_case, authenticator())

    def call(self, **kwargs):
        return asyncio.run(request(self.app, **kwargs))

    def test_verified_user_starts_initial_execution_with_safe_deadline_response(self):
        status, _, payload = self.call()
        self.assertEqual(201, status)
        self.assertEqual("execution_created", payload["outcome"])
        self.assertEqual(900, payload["hard_deadline_seconds"])
        self.assertEqual("user_initial", payload["attempt_kind"])
        self.assertNotIn(SUBJECT, json.dumps(payload))
        self.assertNotIn(USER_TOKEN, json.dumps(payload))

    def test_identity_and_admin_authority_cannot_come_from_body_query_or_custom_header(self):
        attacks = (
            {"token": None},
            {"token": "invalid"},
            {"request_body": body() | {"trusted_user_scope": SUBJECT}},
            {"request_body": body() | {"is_admin": True}},
            {"query": b"user_scope=attacker"},
            {"extra_headers": ((b"x-cognito-groups", b"CryptoTrustAdmins"),)},
        )
        for attack in attacks:
            with self.subTest(attack=attack):
                status, _, payload = self.call(**attack)
                self.assertIn(status, {401, 422})
                self.assertIn(payload["error"]["code"], {"authentication_required", "identity_override_forbidden"})
        self.assertEqual({}, self.store.executions)

    def test_non_admin_cannot_rerun_but_verified_admin_can(self):
        self.call()
        fail(self.executions, self.clock, "EXEC-001", "provider_timeout")
        seed_ready(self.store, "TASK-002", "PF-002")
        rerun_body = {
            "operation_id": "OP-API-RERUN-001",
            "execution_id": "EXEC-002",
            "preflight_id": "PF-002",
            "input_lock_hash": HASH_B,
            "original_execution_id": "EXEC-001",
            "technical_failure_code": "provider_timeout",
        }
        denied = self.call(path="/api/v1/tasks/TASK-002/executions", request_body=rerun_body)
        allowed = self.call(path="/api/v1/tasks/TASK-002/executions", request_body=rerun_body, token=ADMIN_TOKEN)
        self.assertEqual(403, denied[0])
        self.assertEqual("admin_authorization_required", denied[2]["error"]["code"])
        self.assertEqual(201, allowed[0])
        self.assertEqual(2, allowed[2]["attempt_number"])
        self.assertEqual(2, len(self.store.executions))

    def test_cross_tenant_task_is_hidden_and_unknown_repository_errors_are_safe(self):
        self.store.tasks["TASK-001"] = TaskRecordDTO("TASK-001", "other-subject", HASH_A, "ready_for_execution", 2, "2026-08-01T02:00:00Z")
        status, _, payload = self.call()
        self.assertEqual(404, status)
        self.assertEqual("task_not_found", payload["error"]["code"])
        self.assertNotIn("other-subject", json.dumps(payload))


def port_deadline(clock, operation):
    now = clock.current_utc()
    return DeadlineDTO("1.0.0", operation, (now.as_datetime() + timedelta(seconds=30)).isoformat().replace("+00:00", "Z"), 2_000, now, 100)


def fail(repository, clock, execution_id, code):
    op1 = f"OP-COLLECT-{execution_id}"
    collecting = repository.transition(TransitionExecutionRequestDTO(op1, execution_id, 1, "created", "collecting", clock.current_utc(), port_deadline(clock, op1)))
    op2 = f"OP-FAIL-{execution_id}"
    repository.transition(TransitionExecutionRequestDTO(op2, execution_id, collecting.version, "collecting", "failed", clock.current_utc(), port_deadline(clock, op2), safe_reason_code=code))


class ExecutionFastApiTests(unittest.TestCase):
    def test_fastapi_mount_preserves_verified_principal_boundary(self):
        from fastapi.testclient import TestClient
        clock = FakeClock("2026-08-01T02:00:00Z")
        store = FakePlatformStore()
        seed_ready(store)
        use_case = StartFormalExecutionUseCase(FakeTaskRepository(store, clock), FakeExecutionRepository(store, clock), clock, IDs())
        client = TestClient(create_execution_fastapi_app(use_case, authenticator()))
        passed = client.post("/api/v1/tasks/TASK-001/executions", json=body(), headers={"Authorization": f"Bearer {USER_TOKEN}"})
        denied = client.post("/api/v1/tasks/TASK-001/executions", json=body(operation="OP-OTHER", execution="EXEC-OTHER"))
        self.assertEqual(201, passed.status_code)
        self.assertEqual(401, denied.status_code)
        self.assertEqual(1, len(store.executions))


if __name__ == "__main__":
    unittest.main()
