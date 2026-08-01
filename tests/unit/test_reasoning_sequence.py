from __future__ import annotations

import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crypto_trust_agent.application.dto.common import DeadlineDTO  # noqa: E402
from crypto_trust_agent.application.dto.reasoning import (  # noqa: E402
    ConfidenceComponentsDTO,
    ProviderDTO,
    ReasoningResultDTO,
)
from crypto_trust_agent.application.reasoning import (  # noqa: E402
    ReasoningSequenceRunner,
    ReasoningTokenCounter,
)
from crypto_trust_agent.domain.primitives import ContractValidationError  # noqa: E402
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock  # noqa: E402
from crypto_trust_agent.infrastructure.fakes.reasoning import FakeReasoningProvider  # noqa: E402
from tests.contract.shared_reasoning_assertions import (  # noqa: E402
    deadline,
    generate_request,
    invalid_result,
)


class GuardProbeProvider:
    non_production = True

    def __init__(
        self,
        delegate: FakeReasoningProvider,
        *,
        generate_action: str | None = None,
        repair_action: str | None = None,
        primary_only: bool = False,
    ) -> None:
        self.delegate = delegate
        self.generate_action = generate_action
        self.repair_action = repair_action
        self.primary_only = primary_only

    @staticmethod
    def _invoke(action: str):
        if action == "block":
            time.sleep(0.2)
            raise AssertionError("late provider result must be ignored")
        if action == "throw":
            raise RuntimeError("Authorization Bearer vendor-secret")
        if action == "invalid":
            return object()
        raise AssertionError("unsupported probe action")

    def generate(self, request):
        if self.generate_action and (
            not self.primary_only or request.model_role == "primary"
        ):
            return self._invoke(self.generate_action)
        return self.delegate.generate(request)

    def repair(self, request):
        if self.repair_action:
            return self._invoke(self.repair_action)
        return self.delegate.repair(request)

    def health_check(self, request):
        return self.delegate.health_check(request)


class ReasoningSequenceRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock("2026-08-01T02:00:00Z")
        self.provider = FakeReasoningProvider(self.clock)
        self.token_counters = (
            ReasoningTokenCounter(
                "primary", "fake-v1", "fake-tokenizer-1.0.0", lambda value: len(value)
            ),
            ReasoningTokenCounter(
                "fallback", "fake-v1", "fake-tokenizer-1.0.0", lambda value: len(value)
            ),
        )
        self.runner = ReasoningSequenceRunner(
            self.provider,
            self.clock,
            token_counters=self.token_counters,
        )

    def make_runner(self, provider) -> ReasoningSequenceRunner:
        return ReasoningSequenceRunner(
            provider,
            self.clock,
            token_counters=self.token_counters,
        )

    def requests(self):
        primary = generate_request("OP-RSN-SEQ-PRIMARY")
        fallback = replace(
            generate_request("OP-RSN-SEQ-FALLBACK", model_role="fallback"),
            context=primary.context,
            context_hash=primary.context_hash,
        )
        repair_deadline = deadline("OP-RSN-SEQ-REPAIR", at="2026-08-01T02:01:00Z")
        return primary, repair_deadline, fallback

    @staticmethod
    def tight_deadline(operation_id: str) -> DeadlineDTO:
        return DeadlineDTO(
            "1.0.0",
            operation_id,
            "2026-08-01T02:00:01Z",
            1,
            "2026-08-01T02:00:00Z",
            100,
        )

    def test_primary_valid_stops_without_repair_or_fallback(self) -> None:
        primary, repair_deadline, fallback = self.requests()
        outcome = self.runner.run(primary, "OP-RSN-SEQ-REPAIR", repair_deadline, fallback)
        self.assertTrue(outcome.publishable)
        self.assertEqual(("primary_generate",), outcome.attempts)
        self.assertEqual(1, self.provider.invocation_count)

    def test_invalid_primary_gets_at_most_one_repair_with_shared_deadline(self) -> None:
        primary, repair_deadline, fallback = self.requests()
        self.provider.configure_generate(primary.operation_id, invalid_result())
        outcome = self.runner.run(primary, "OP-RSN-SEQ-REPAIR", repair_deadline, fallback)
        self.assertTrue(outcome.publishable)
        self.assertEqual(("primary_generate", "primary_repair"), outcome.attempts)
        self.assertEqual(2, self.provider.invocation_count)

    def test_failed_repair_falls_back_once_and_invalid_fallback_is_not_publishable(self) -> None:
        primary, repair_deadline, fallback = self.requests()
        self.provider.configure_generate(primary.operation_id, invalid_result())
        self.provider.configure_repair("OP-RSN-SEQ-REPAIR", "reasoning_timeout")
        first = self.runner.run(primary, "OP-RSN-SEQ-REPAIR", repair_deadline, fallback)
        self.assertTrue(first.publishable)
        self.assertEqual(("primary_generate", "primary_repair", "fallback_generate"), first.attempts)

        other = FakeReasoningProvider(self.clock)
        runner = self.make_runner(other)
        other.configure_generate(primary.operation_id, invalid_result())
        other.configure_repair("OP-RSN-SEQ-REPAIR", "reasoning_timeout")
        other.configure_generate(fallback.operation_id, invalid_result(role="fallback"))
        invalid = runner.run(primary, "OP-RSN-SEQ-REPAIR", repair_deadline, fallback)
        self.assertFalse(invalid.publishable)
        self.assertEqual("invalid", invalid.result.outcome)
        self.assertEqual(3, other.invocation_count)

    def test_repair_must_share_primary_sixty_second_window_and_context(self) -> None:
        primary, _, fallback = self.requests()
        later = deadline("OP-RSN-SEQ-REPAIR", at="2026-08-01T02:01:01Z")
        with self.assertRaises(ContractValidationError):
            self.runner.run(primary, "OP-RSN-SEQ-REPAIR", later, fallback)
        changed_context = replace(fallback.context, question="Different bounded question")
        changed = replace(fallback, context=changed_context, context_hash=changed_context.context_hash())
        with self.assertRaises(ContractValidationError):
            self.runner.run(primary, "OP-RSN-SEQ-REPAIR", deadline("OP-RSN-SEQ-REPAIR"), changed)
        self.assertEqual(0, self.provider.invocation_count)

    def test_blocking_primary_and_repair_are_outer_guarded(self) -> None:
        primary, repair_deadline, fallback = self.requests()
        primary = replace(primary, deadline=self.tight_deadline(primary.operation_id))
        guarded = self.make_runner(GuardProbeProvider(
            self.provider, generate_action="block", primary_only=True
        ))
        started = time.perf_counter()
        outcome = guarded.run(primary, "OP-RSN-SEQ-REPAIR", self.tight_deadline("OP-RSN-SEQ-REPAIR"), fallback)
        self.assertLess(time.perf_counter() - started, 0.1)
        self.assertTrue(outcome.publishable)
        self.assertEqual(("primary_generate", "fallback_generate"), outcome.attempts)

        repair_primary, _, repair_fallback = self.requests()
        self.provider.configure_generate(repair_primary.operation_id, invalid_result())
        repair_guarded = self.make_runner(GuardProbeProvider(
            self.provider, repair_action="block"
        ))
        tight_repair = DeadlineDTO(
            "1.0.0",
            "OP-RSN-SEQ-REPAIR",
            repair_primary.deadline.deadline_at_utc,
            1,
            repair_primary.deadline.sent_at_utc,
            100,
        )
        started = time.perf_counter()
        repaired = repair_guarded.run(
            repair_primary,
            "OP-RSN-SEQ-REPAIR",
            tight_repair,
            repair_fallback,
        )
        self.assertLess(time.perf_counter() - started, 0.1)
        self.assertTrue(repaired.publishable)
        self.assertEqual(
            ("primary_generate", "primary_repair", "fallback_generate"),
            repaired.attempts,
        )

    def test_throwing_and_invalid_runtime_provider_results_are_safe(self) -> None:
        primary, repair_deadline, fallback = self.requests()
        for action in ("throw", "invalid"):
            with self.subTest(action=action):
                runner = self.make_runner(GuardProbeProvider(
                    self.provider,
                    generate_action=action,
                ))
                outcome = runner.run(
                    primary,
                    "OP-RSN-SEQ-REPAIR",
                    repair_deadline,
                    fallback,
                )
                self.assertFalse(outcome.publishable)
                self.assertEqual("unexpected_provider_error", outcome.result.error.code)
                self.assertNotIn("secret", repr(outcome.result.to_wire()).lower())

    def test_provider_model_version_must_match_tokenizer_binding(self) -> None:
        primary, repair_deadline, fallback = self.requests()
        self.provider.configure_generate(primary.operation_id, "model_unavailable")
        now = "2026-08-01T02:00:00Z"
        mismatched = ReasoningResultDTO(
            "valid",
            ProviderDTO("fake_reasoning_model", "wrong-v2", "fallback", "INV-MISMATCH"),
            (),
            (),
            (),
            (),
            (),
            ConfidenceComponentsDTO("0.5", "0.5", "0.5", "0.5"),
            (),
            now,
            now,
        )
        self.provider.configure_generate(fallback.operation_id, mismatched)
        outcome = self.runner.run(
            primary,
            "OP-RSN-SEQ-REPAIR",
            repair_deadline,
            fallback,
        )
        self.assertFalse(outcome.publishable)
        self.assertEqual("invalid", outcome.result.outcome)
        self.assertIn(
            "provider_model_mismatch",
            {item.code for item in outcome.result.validation_diagnostics},
        )

    def test_primary_window_cannot_be_extended_by_a_far_absolute_deadline(self) -> None:
        primary, _, fallback = self.requests()
        far_primary_deadline = DeadlineDTO(
            "1.0.0", primary.operation_id, "2026-08-01T02:15:00Z", 60_000,
            "2026-08-01T02:00:00Z", 100,
        )
        primary = replace(primary, deadline=far_primary_deadline)
        far_repair = DeadlineDTO(
            "1.0.0", "OP-RSN-SEQ-REPAIR", "2026-08-01T02:15:00Z", 60_000,
            "2026-08-01T02:00:00Z", 100,
        )
        with self.assertRaises(ContractValidationError):
            self.runner.run(primary, "OP-RSN-SEQ-REPAIR", far_repair, fallback)
        self.assertEqual(0, self.provider.invocation_count)


if __name__ == "__main__":
    unittest.main()
