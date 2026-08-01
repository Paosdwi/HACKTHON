from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.reasoning import (
    AnalysisRefDTO,
    ConclusionDTO,
    ConfidenceComponentsDTO,
    DiagnosticDTO,
    EvidenceRefDTO,
    FactDTO,
    GenerateRequestDTO,
    InferenceDTO,
    ProviderDTO,
    ReasoningContextDTO,
    ReasoningHealthCheckRequestDTO,
    ReasoningResultDTO,
    RepairRequestDTO,
)
from crypto_trust_agent.application.ports.preflight import ProviderHealthDTO
from crypto_trust_agent.application.ports.reasoning import ReasoningProvider
from tests.contract.shared_collector_extractor_assertions import (
    assert_method_contracts,
    assert_operation_wire_valid,
    assert_schema_examples,
)

NOW = "2026-08-01T02:00:00Z"
HASH_A = "sha256:" + "a" * 64


def deadline(operation_id: str, *, seconds: int = 60, at: str | None = None) -> DeadlineDTO:
    return DeadlineDTO("1.0.0", operation_id, at or f"2026-08-01T02:01:00Z", seconds * 1000, NOW, 100)


def context() -> ReasoningContextDTO:
    return ReasoningContextDTO(
        "What is the BTC market status?",
        (EvidenceRefDTO("EVID-001", "ASSESS-001", "bounded excerpt", "supports", "0.8"),),
        (AnalysisRefDTO("ANALYSIS-001", "1.0.0", "Model result is available", ("DATASET:BTC-001",)),),
        (), (), (),
    )


def generate_request(operation_id: str = "OP-RSN-01", *, model_role: str = "primary") -> GenerateRequestDTO:
    value = context()
    return GenerateRequestDTO(
        operation_id, "TASK-001", "EXEC-001", model_role, value, value.context_hash(),
        "1.0.0", "reasoning-guardrail-1.0.0", deadline(operation_id),
    )


def invalid_result(*, role: str = "primary") -> ReasoningResultDTO:
    diagnostic = DiagnosticDTO("/facts", "citation_missing", "A citation is required")
    return ReasoningResultDTO(
        "invalid", ProviderDTO("reasoning_model", "fake-v1", role, "INV-INVALID"),
        (), (), (), (), (), ConfidenceComponentsDTO("0", "0", "0", "0"),
        (diagnostic,), NOW, "2026-08-01T02:00:10Z",
    )


def repair_request(operation_id: str = "OP-RSN-REPAIR") -> RepairRequestDTO:
    original = invalid_result()
    return RepairRequestDTO(
        operation_id, "TASK-001", "EXEC-001", context().context_hash(), original,
        original.validation_diagnostics, "1.0.0", "reasoning-guardrail-1.0.0",
        deadline(operation_id),
    )


class ReasoningProviderContractAssertions:
    def make_provider(self):
        raise NotImplementedError

    def configure_generate(self, provider, operation_id: str, response: object) -> None:
        raise NotImplementedError

    def configure_repair(self, provider, operation_id: str, response: object) -> None:
        raise NotImplementedError

    def configure_health(self, provider, operation_id: str, response: object) -> None:
        raise NotImplementedError

    def test_schema_examples_and_all_frozen_method_policies(self) -> None:
        assert_schema_examples(self, "reasoning_provider")
        assert_method_contracts(self, "reasoning_provider", {
            "CT-REASON-GENERATE-01": {
                "timeout_by_model_role_ms": {"primary": 60000, "fallback": 60000},
                "retry_owner": "core", "max_attempts": 1, "hidden_adapter_retries": False,
                "idempotency": {"mode": "operation_context_role_model_identity", "key_fields": ["operation_id", "context_hash", "model_role", "output_schema_version"]},
                "concurrency": {"mode": "core_sequenced_single_model_call"},
                "cancellation": {"deadline_propagated": True},
                "semantic_invariants": ["primary_then_at_most_one_primary_repair_then_fallback_then_validate", "invalid_fallback_is_not_published"],
                "error_codes": ["context_invalid", "context_too_large", "guardrail_rejected", "reasoning_schema_invalid", "citation_invalid", "numeric_inconsistency", "reasoning_timeout", "reasoning_rate_limited", "model_unavailable", "fallback_unavailable", "deadline_exceeded", "unexpected_provider_error"],
            },
            "CT-REASON-REPAIR-01": {
                "timeout_ms": 60000, "retry_owner": "core", "max_attempts": 1, "hidden_adapter_retries": False,
                "idempotency": {"mode": "operation_context_original_result_identity", "key_fields": ["operation_id", "context_hash"]},
                "concurrency": {"mode": "at_most_one_primary_repair"},
                "cancellation": {"deadline_propagated": True},
                "semantic_invariants": ["repair_only_after_primary_validation_failure"],
                "error_codes": ["guardrail_rejected", "reasoning_schema_invalid", "citation_invalid", "numeric_inconsistency", "reasoning_timeout", "reasoning_rate_limited", "model_unavailable", "deadline_exceeded", "unexpected_provider_error"],
            },
            "CT-REASON-HEALTH-01": {
                "timeout_ms": 3000, "retry_owner": "core", "max_attempts": 1, "hidden_adapter_retries": False,
                "idempotency": {"mode": "side_effect_free_health_read", "key_fields": ["operation_id", "model_role"]},
                "concurrency": {"mode": "independent_health_reads"},
                "error_codes": ["reasoning_timeout", "model_unavailable", "fallback_unavailable", "deadline_exceeded", "unexpected_provider_error"],
            },
        })

    def test_runtime_protocol_non_production_exact_methods_and_wire(self) -> None:
        provider = self.make_provider()
        self.assertIsInstance(provider, ReasoningProvider)
        self.assertTrue(provider.non_production)
        self.assertTrue(all(callable(getattr(provider, name)) for name in ("generate", "repair", "health_check")))
        request = generate_request()
        self.assertEqual({"schema_version", "operation_id", "task_id", "execution_id", "model_role", "context", "context_hash", "output_schema_version", "guardrail_policy_version", "deadline"}, set(request.to_wire()))
        with self.assertRaises(FrozenInstanceError):
            request.model_role = "fallback"
        with self.assertRaises(AttributeError):
            request.context.evidence_refs.append(EvidenceRefDTO("EVID-X", "ASSESS-X", "x", "context", "0"))
        result = provider.generate(request)
        assert_operation_wire_valid(self, "reasoning_provider", "generate", request, result)

    def test_generate_success_citation_graph_replay_and_payload_conflict(self) -> None:
        provider = self.make_provider()
        request = generate_request("OP-RSN-REPLAY")
        first = provider.generate(request)
        self.assertEqual("valid", first.outcome)
        self.assertIs(first, provider.generate(request))
        self.assertEqual(1, provider.invocation_count)
        changed_context = replace(context(), question="Different bounded question")
        changed = replace(request, context=changed_context, context_hash=changed_context.context_hash())
        conflict = provider.generate(changed)
        self.assertEqual("context_invalid", conflict.error.code)
        self.assertEqual(1, provider.invocation_count)

    def test_generate_rejects_hallucinated_context_citations_and_invalid_schema(self) -> None:
        provider = self.make_provider()
        bad = ReasoningResultDTO(
            "valid", ProviderDTO("reasoning_model", "v1", "primary", "INV-BAD"),
            (FactDTO("FACT-001", "hallucinated", ("EVID-999",), ()),), (), (), (), (),
            ConfidenceComponentsDTO("0.5", "0.5", "0.5", "0.5"), (), NOW, "2026-08-01T02:00:01Z",
        )
        self.configure_generate(provider, "OP-RSN-CITE", bad)
        self.assertEqual("citation_invalid", provider.generate(generate_request("OP-RSN-CITE")).error.code)
        self.configure_generate(provider, "OP-RSN-SCHEMA", object())
        self.assertEqual("reasoning_schema_invalid", provider.generate(generate_request("OP-RSN-SCHEMA")).error.code)

    def test_generate_timeout_unavailable_unknown_and_deadline_before_io_are_safe(self) -> None:
        provider = self.make_provider()
        for operation_id, configured, code in (
            ("OP-RSN-TIMEOUT", "reasoning_timeout", "reasoning_timeout"),
            ("OP-RSN-UNAVAILABLE", "model_unavailable", "model_unavailable"),
            ("OP-RSN-UNKNOWN", RuntimeError("Authorization Bearer secret"), "unexpected_provider_error"),
        ):
            self.configure_generate(provider, operation_id, configured)
            result = provider.generate(generate_request(operation_id))
            self.assertIsInstance(result, ErrorResultDTO)
            self.assertEqual(code, result.error.code)
            self.assertNotIn("secret", repr(result.to_wire()).lower())
        count = provider.invocation_count
        request = generate_request("OP-RSN-EXPIRED")
        request = replace(request, deadline=deadline("OP-RSN-EXPIRED", at=NOW))
        self.assertEqual("deadline_exceeded", provider.generate(request).error.code)
        self.assertEqual(count, provider.invocation_count)

    def test_repair_requires_prior_matching_primary_invalid_and_is_at_most_once(self) -> None:
        provider = self.make_provider()
        request = repair_request("OP-RSN-REPAIR-EARLY")
        self.assertEqual("reasoning_schema_invalid", provider.repair(request).error.code)

        primary_operation = "OP-RSN-PRIMARY-INVALID"
        primary_invalid = invalid_result()
        self.configure_generate(provider, primary_operation, primary_invalid)
        primary_request = generate_request(primary_operation)
        self.assertEqual("invalid", provider.generate(primary_request).outcome)
        repair = replace(repair_request("OP-RSN-REPAIR-ONCE"), context_hash=primary_request.context_hash, original_result=primary_invalid)
        repaired = provider.repair(repair)
        self.assertEqual("valid", repaired.outcome)
        self.assertIs(repaired, provider.repair(repair))
        second = replace(repair, operation_id="OP-RSN-REPAIR-TWICE", deadline=deadline("OP-RSN-REPAIR-TWICE"))
        self.assertEqual("reasoning_schema_invalid", provider.repair(second).error.code)

    def test_repair_is_task_execution_bound_and_revalidates_context_citations(self) -> None:
        provider = self.make_provider()
        primary_operation = "OP-RSN-PRIMARY-BOUND"
        primary_invalid = invalid_result()
        self.configure_generate(provider, primary_operation, primary_invalid)
        primary_request = generate_request(primary_operation)
        provider.generate(primary_request)
        repair = replace(
            repair_request("OP-RSN-REPAIR-BOUND"),
            context_hash=primary_request.context_hash,
            original_result=primary_invalid,
        )
        cross_task = replace(repair, task_id="TASK-OTHER")
        self.assertEqual("reasoning_schema_invalid", provider.repair(cross_task).error.code)

        hallucinated = ReasoningResultDTO(
            "valid", ProviderDTO("reasoning_model", "v1", "primary", "INV-REPAIR-BAD"),
            (FactDTO("FACT-REPAIR-BAD", "hallucinated", ("EVID-999",), ()),),
            (), (), (), (), ConfidenceComponentsDTO("0.5", "0.5", "0.5", "0.5"),
            (), NOW, "2026-08-01T02:00:01Z",
        )
        self.configure_repair(provider, repair.operation_id, hallucinated)
        self.assertEqual("citation_invalid", provider.repair(repair).error.code)

    def test_repair_typed_failure_and_fallback_invalid_is_not_publishable(self) -> None:
        provider = self.make_provider()
        primary_operation = "OP-RSN-PRIMARY-FAIL"
        original = invalid_result()
        self.configure_generate(provider, primary_operation, original)
        primary_request = generate_request(primary_operation)
        provider.generate(primary_request)
        repair = replace(repair_request("OP-RSN-REPAIR-FAIL"), context_hash=primary_request.context_hash, original_result=original)
        self.configure_repair(provider, repair.operation_id, "reasoning_timeout")
        self.assertEqual("reasoning_timeout", provider.repair(repair).error.code)

        fallback = invalid_result(role="fallback")
        self.configure_generate(provider, "OP-RSN-FALLBACK-INVALID", fallback)
        result = provider.generate(generate_request("OP-RSN-FALLBACK-INVALID", model_role="fallback"))
        self.assertEqual("invalid", result.outcome)
        self.assertFalse(provider.is_publishable(result))

    def test_health_is_role_bound_three_second_side_effect_free(self) -> None:
        provider = self.make_provider()
        request = ReasoningHealthCheckRequestDTO("OP-RSN-HEALTH", "primary", deadline("OP-RSN-HEALTH", seconds=3, at="2026-08-01T02:00:03Z"))
        result = provider.health_check(request)
        self.assertIsInstance(result, ProviderHealthDTO)
        self.assertEqual("primary_reasoning", result.capability)
        self.assertEqual(0, provider.invocation_count)
        assert_operation_wire_valid(self, "reasoning_provider", "health_check", request, result)

        self.configure_health(provider, "OP-RSN-HEALTH-UNKNOWN", RuntimeError("vendor bearer secret"))
        unknown_request = ReasoningHealthCheckRequestDTO(
            "OP-RSN-HEALTH-UNKNOWN", "fallback",
            deadline("OP-RSN-HEALTH-UNKNOWN", seconds=3, at="2026-08-01T02:00:03Z"),
        )
        unknown = provider.health_check(unknown_request)
        self.assertEqual("unexpected_provider_error", unknown.error.code)
        self.assertNotIn("secret", repr(unknown.to_wire()).lower())
