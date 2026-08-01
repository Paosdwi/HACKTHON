from __future__ import annotations

import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.planning import QuestionType  # noqa: E402
from crypto_trust_agent.application.use_cases.create_task import (  # noqa: E402
    CreateTaskCommand,
    CreateTaskRateLimited,
    CreateTaskUseCase,
    CreateTaskValidationError,
)
from crypto_trust_agent.infrastructure.fakes import (  # noqa: E402
    FakeClock,
    FakePlatformStore,
    FakeTaskRepository,
)
from crypto_trust_agent.infrastructure.identity import (  # noqa: E402
    CognitoPrincipalBoundary,
    FakeCognitoTokenVerifier,
    HmacPrincipalPseudonymizer,
    IdentityAuthenticationError,
)


ISSUER = "https://cognito-idp.ap-southeast-1.amazonaws.com/ap-southeast-1_example"
AUDIENCE = "crypto-trust-client"
RAW_SUBJECT = "cognito-subject-user-123"
HMAC_KEY = b"0123456789abcdef0123456789abcdef"


class IdentifierFactory:
    def __init__(self) -> None:
        self._lock = Lock()
        self._value = 0

    def __call__(self, prefix: str) -> str:
        with self._lock:
            self._value += 1
            return f"{prefix}{self._value:08X}"


class AuditSink:
    def __init__(self) -> None:
        self._lock = Lock()
        self.records = []

    def __call__(self, item) -> None:
        with self._lock:
            self.records.append(item)


def classify(question: str, assets: tuple[str, ...]) -> QuestionType:
    if question.startswith("Compare") and len(assets) == 2:
        return QuestionType.ASSET_COMPARISON
    if question.startswith("Validate") and len(assets) == 1:
        return QuestionType.HYPOTHESIS_VALIDATION
    if question.startswith("Status") and len(assets) == 1:
        return QuestionType.MARKET_STATUS
    raise ValueError("unsupported question type")


def command(*, question: str = "Status of BTC", assets: tuple[str, ...] = ("BTC",)) -> CreateTaskCommand:
    return CreateTaskCommand(
        trusted_user_scope=RAW_SUBJECT,
        principal_pseudonym="hmac-sha256:k2026-01:" + "a" * 64,
        question=question,
        assets=assets,
        timeframe_start="2026-07-18T00:00:00Z",
        timeframe_end="2026-08-01T00:00:00Z",
        formal_run=True,
    )


class VerifiedPrincipalBoundaryTests(unittest.TestCase):
    def boundary(self, claims: dict[str, object] | None = None, *, rejected: tuple[str, ...] = ()) -> CognitoPrincipalBoundary:
        valid_claims = claims or {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "exp": 1785553200,
            "sub": RAW_SUBJECT,
            "cognito:groups": ["Users", "CryptoTrustAdmins"],
        }
        return CognitoPrincipalBoundary(
            verifier=FakeCognitoTokenVerifier({"valid-token": valid_claims}, rejected_tokens=rejected),
            expected_issuer=ISSUER,
            expected_audience=AUDIENCE,
            pseudonymizer=HmacPrincipalPseudonymizer("k2026-01", HMAC_KEY),
            now_provider=lambda: datetime(2026, 8, 1, 2, 0, tzinfo=UTC),
        )

    def test_verified_claims_are_the_only_principal_source(self) -> None:
        principal = self.boundary().authenticate("Bearer valid-token")
        self.assertEqual(RAW_SUBJECT, principal.subject)
        self.assertTrue(principal.is_admin)
        self.assertRegex(principal.pseudonym, r"^hmac-sha256:k2026-01:[0-9a-f]{64}$")
        self.assertEqual(principal.pseudonym, self.boundary().authenticate("Bearer valid-token").pseudonym)

    def test_wrong_issuer_audience_expiry_subject_groups_or_signature_are_rejected(self) -> None:
        variants = (
            {"iss": "https://evil.example", "aud": AUDIENCE, "exp": 1785553200, "sub": RAW_SUBJECT},
            {"iss": ISSUER, "aud": "wrong-client", "exp": 1785553200, "sub": RAW_SUBJECT},
            {"iss": ISSUER, "aud": AUDIENCE, "exp": 1, "sub": RAW_SUBJECT},
            {"iss": ISSUER, "aud": AUDIENCE, "exp": 1785553200},
            {"iss": ISSUER, "aud": AUDIENCE, "exp": 1785553200, "sub": RAW_SUBJECT, "cognito:groups": "CryptoTrustAdmins"},
        )
        for claims in variants:
            with self.subTest(claims=claims), self.assertRaises(IdentityAuthenticationError):
                self.boundary(claims).authenticate("Bearer valid-token")
        with self.assertRaises(IdentityAuthenticationError):
            self.boundary(rejected=("bad-signature",)).authenticate("Bearer bad-signature")
        with self.assertRaises(IdentityAuthenticationError):
            self.boundary().authenticate("valid-token")

    def test_admin_group_is_exact_and_pseudonym_rotates_by_key_version(self) -> None:
        claims = {"iss": ISSUER, "aud": AUDIENCE, "exp": 1785553200, "sub": RAW_SUBJECT, "cognito:groups": ["cryptotrustadmins"]}
        self.assertFalse(self.boundary(claims).authenticate("Bearer valid-token").is_admin)
        first = HmacPrincipalPseudonymizer("k2026-01", HMAC_KEY).pseudonymize(RAW_SUBJECT)
        second = HmacPrincipalPseudonymizer("k2026-02", b"abcdef0123456789abcdef0123456789").pseudonymize(RAW_SUBJECT)
        self.assertNotEqual(first, second)


class CreateTaskUseCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock("2026-08-01T02:00:00Z", monotonic_ms=100)
        self.store = FakePlatformStore()
        self.repository = FakeTaskRepository(self.store, self.clock)
        self.audit = AuditSink()
        self.use_case = CreateTaskUseCase(
            task_repository=self.repository,
            clock=self.clock,
            question_classifier=classify,
            identifier_factory=IdentifierFactory(),
            audit_recorder=self.audit,
        )

    def test_create_then_reuse_counts_both_and_audit_is_sanitized(self) -> None:
        created = self.use_case.execute(command())
        reused = self.use_case.execute(command())
        self.assertEqual("created", created.outcome)
        self.assertEqual("reused", reused.outcome)
        self.assertEqual(created.task_id, reused.task_id)
        self.assertEqual(1, len(self.store.tasks))
        self.assertEqual(8, reused.rate_limit.remaining)
        serialized = json.dumps([item.to_safe_dict() for item in self.audit.records], sort_keys=True)
        self.assertNotIn(RAW_SUBJECT, serialized)
        self.assertNotIn("Bearer", serialized)
        self.assertIn("hmac-sha256:k2026-01:", serialized)
        self.assertIn("fingerprint-1.0.0", serialized)

    def test_eleventh_request_is_rate_limited_before_reuse_side_effect(self) -> None:
        first = self.use_case.execute(command())
        for _ in range(9):
            self.assertEqual("reused", self.use_case.execute(command()).outcome)
        with self.assertRaises(CreateTaskRateLimited):
            self.use_case.execute(command())
        self.assertEqual(1, len(self.store.tasks))
        self.assertEqual(first.task_id, next(iter(self.store.tasks)))

    def test_invalid_input_creates_no_task_but_counts_authenticated_requests(self) -> None:
        invalid_commands = (
            command(assets=("DOGE",)),
            command(question="Unsupported request"),
            CreateTaskCommand(RAW_SUBJECT, "hmac-sha256:k2026-01:" + "a" * 64, "Status", ("BTC",), "2026-08-01T00:00:01Z", "2026-08-02T00:00:00Z", True),
            CreateTaskCommand(RAW_SUBJECT, "hmac-sha256:k2026-01:" + "a" * 64, "Status", ("BTC",), "2026-08-02T00:00:00Z", "2026-08-01T00:00:00Z", True),
        )
        for item in invalid_commands:
            with self.subTest(item=item), self.assertRaises(CreateTaskValidationError):
                self.use_case.execute(item)
        self.assertEqual({}, self.store.tasks)
        self.assertEqual(4, sum(len(items) for items in self.store.task_create_counters.values()))

    def test_audit_failure_does_not_hide_committed_task_result(self) -> None:
        def broken_audit(_item) -> None:
            raise RuntimeError("log backend unavailable")

        use_case = CreateTaskUseCase(
            self.repository,
            self.clock,
            classify,
            IdentifierFactory(),
            broken_audit,
        )
        result = use_case.execute(command())
        self.assertEqual("created", result.outcome)
        self.assertIn(result.task_id, self.store.tasks)

    def test_all_three_question_types_build_contract_valid_tasks(self) -> None:
        requests = (
            command(question="Status of BTC", assets=("BTC",)),
            command(question="Validate BTC thesis", assets=("BTC",)),
            command(question="Compare ETH and BTC", assets=("ETH", "BTC")),
        )
        for index, item in enumerate(requests):
            with self.subTest(item=item):
                item = CreateTaskCommand(
                    trusted_user_scope=f"{RAW_SUBJECT}-{index}",
                    principal_pseudonym=f"hmac-sha256:k2026-01:{index:064x}",
                    question=item.question,
                    assets=item.assets,
                    timeframe_start=item.timeframe_start,
                    timeframe_end=item.timeframe_end,
                    formal_run=item.formal_run,
                )
                self.assertEqual("created", self.use_case.execute(item).outcome)

    def test_ten_concurrent_identical_requests_create_only_one_task(self) -> None:
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(lambda _: self.use_case.execute(command()), range(10)))
        self.assertEqual(1, sum(item.outcome == "created" for item in results))
        self.assertEqual(9, sum(item.outcome == "reused" for item in results))
        self.assertEqual(1, len({item.task_id for item in results}))
        self.assertEqual(1, len(self.store.tasks))


if __name__ == "__main__":
    unittest.main()
