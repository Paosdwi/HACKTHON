from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys
from threading import Barrier, Event
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Bind this Provider harness to the checked-out Core namespace package.
core_package = types.ModuleType("crypto_trust_agent")
core_package.__path__ = [str(SRC / "crypto_trust_agent")]
sys.modules["crypto_trust_agent"] = core_package

from crypto_trust_agent.application.dto.common import ErrorResultDTO, LocalDeadline
from crypto_trust_agent.application.dto.evidence_extractor import ExtractionResultDTO
from crypto_trust_agent.application.ports.evidence_extractor_v2 import EvidenceExtractorV2
from crypto_trust_agent.infrastructure.extraction.adapter import (
    NovaLiteEvidenceExtractor,
    ProviderFailure,
    StubNovaClient,
)
from crypto_trust_agent.infrastructure.extraction.adapter_v2 import (
    NovaLiteEvidenceExtractorV2,
)
from tests.contract.shared_collector_extractor_assertions import (
    repair_request as repair_request_v1,
)
from tests.contract.shared_evidence_extractor_v2_assertions import (
    CONTENT,
    EvidenceExtractorV2ContractAssertions,
    deadline,
    repair_request,
)

NOW = datetime(2026, 8, 1, 2, 0, 0, tzinfo=UTC)


class HarnessClock:
    def __init__(self) -> None:
        self.now = NOW
        self.monotonic = 123_000

    def advance(self, elapsed_ms: int) -> None:
        self.now += timedelta(milliseconds=elapsed_ms)
        self.monotonic += elapsed_ms


@dataclass(frozen=True, slots=True)
class LateProviderResponse:
    result: ExtractionResultDTO
    elapsed_ms: int


class HarnessNovaClient:
    non_production = True

    def __init__(self, clock: HarnessClock) -> None:
        self.clock = clock
        self.responses: dict[str, object] = {}
        self.calls: list[dict[str, object]] = []

    def configure(self, operation_id: str, response: object) -> None:
        self.responses[operation_id] = response

    def invoke(
        self,
        *,
        operation_id: str,
        payload: Mapping[str, object],
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> object:
        self.calls.append({
            "operation_id": operation_id,
            "payload": dict(payload),
            "timeout_ms": timeout_ms,
        })
        if cancelled():
            raise ProviderFailure("extractor_timeout", retryable=True)
        configured = self.responses.get(operation_id)
        if isinstance(configured, LateProviderResponse):
            self.clock.advance(configured.elapsed_ms)
            return configured.result
        if isinstance(configured, BaseException):
            raise configured
        if isinstance(configured, str):
            raise ProviderFailure(
                configured,
                retryable=configured in {
                    "extractor_rate_limited",
                    "extractor_timeout",
                    "extractor_unavailable",
                },
            )
        if configured is not None:
            return configured
        mode = payload["mode"]
        if mode == "repair":
            content = str(payload["authoritative_content"])
        else:
            content_input = payload["content"]
            assert isinstance(content_input, Mapping)
            content = str(
                content_input.get(
                    "clean_content",
                    "Content available through validated locator.",
                )
            )
        assets = payload["assets"]
        taxonomy = payload["allowed_event_taxonomy"]
        assert isinstance(assets, list) and isinstance(taxonomy, list)
        return {
            "claims": [{
                "text": "Deterministic repaired provider claim.",
                "quote": content[:4_096],
                "related_assets": [assets[0]],
                "event_type": taxonomy[0],
                "sentiment": "neutral",
                "relevance": "high",
            }],
            "validation_errors": [],
            "usage": {"input_units": None, "output_units": None},
            "invocation_id": f"INV-{operation_id[3:]}",
        }

    def probe(self, *, timeout_ms: int, cancelled: Callable[[], bool]) -> bool:
        return timeout_ms > 0 and not cancelled()


@dataclass(frozen=True, slots=True)
class LocatorScenario:
    clean_content: str | None = None
    elapsed_ms: int = 0
    error: BaseException | None = None


class HarnessContentResolver:
    non_production = True

    def __init__(self, clock: HarnessClock) -> None:
        self.clock = clock
        self.scenarios: dict[str, LocatorScenario] = {}
        self.calls: list[dict[str, object]] = []

    def configure(self, locator: str, scenario: LocatorScenario) -> None:
        self.scenarios[locator] = scenario

    def resolve(
        self,
        *,
        locator: str,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> str:
        self.calls.append({"locator": locator, "timeout_ms": timeout_ms})
        if cancelled():
            raise TimeoutError("cancelled")
        scenario = self.scenarios.get(locator)
        if scenario is None:
            raise FileNotFoundError("unconfigured locator")
        if scenario.elapsed_ms:
            self.clock.advance(scenario.elapsed_ms)
        if scenario.error is not None:
            raise scenario.error
        if scenario.clean_content is None:
            raise FileNotFoundError("missing content")
        return scenario.clean_content


@dataclass(slots=True)
class HarnessFixture:
    clock: HarnessClock
    client: HarnessNovaClient
    resolver: HarnessContentResolver


def make_adapter(fixture: HarnessFixture) -> NovaLiteEvidenceExtractorV2:
    return NovaLiteEvidenceExtractorV2(
        fixture.client,
        content_resolver=fixture.resolver,
        model_version="nova-2-lite-v2-test",
        now_utc=lambda: fixture.clock.now,
        monotonic_ms=lambda: fixture.clock.monotonic,
        runtime_id="pa71-v2-test-runtime",
    )


class ProviderV2SharedContractTests(
    EvidenceExtractorV2ContractAssertions,
    unittest.TestCase,
):
    """Run the unmodified Core v2 assertions against the PA71 adapter."""

    def setUp(self) -> None:
        self._fixtures: dict[int, HarnessFixture] = {}

    def make_extractor(self) -> NovaLiteEvidenceExtractorV2:
        clock = HarnessClock()
        fixture = HarnessFixture(
            clock,
            HarnessNovaClient(clock),
            HarnessContentResolver(clock),
        )
        adapter = make_adapter(fixture)
        self._fixtures[id(adapter)] = fixture
        return adapter

    def configure_repair(
        self,
        extractor: EvidenceExtractorV2,
        operation_id: str,
        response: object,
    ) -> None:
        self._fixtures[id(extractor)].client.configure(operation_id, response)

    def configure_locator(
        self,
        extractor: EvidenceExtractorV2,
        locator: str,
        *,
        clean_content: str | None = None,
        elapsed_ms: int = 0,
        error: BaseException | None = None,
    ) -> None:
        self._fixtures[id(extractor)].resolver.configure(
            locator,
            LocatorScenario(clean_content, elapsed_ms, error),
        )

    def configure_late_repair(
        self,
        extractor: EvidenceExtractorV2,
        operation_id: str,
        result: ExtractionResultDTO,
        *,
        elapsed_ms: int,
    ) -> None:
        self._fixtures[id(extractor)].client.configure(
            operation_id,
            LateProviderResponse(result, elapsed_ms),
        )

    def provider_invocation_count(self, extractor: EvidenceExtractorV2) -> int:
        return len(self._fixtures[id(extractor)].client.calls)

    def locator_resolution_count(self, extractor: EvidenceExtractorV2) -> int:
        return len(self._fixtures[id(extractor)].resolver.calls)


class ProviderV2BoundaryTests(unittest.TestCase):
    def make_fixture(self) -> tuple[NovaLiteEvidenceExtractorV2, HarnessFixture]:
        clock = HarnessClock()
        fixture = HarnessFixture(
            clock,
            HarnessNovaClient(clock),
            HarnessContentResolver(clock),
        )
        return make_adapter(fixture), fixture

    def test_declares_v2_capability_without_changing_v1_adapter(self) -> None:
        adapter, _ = self.make_fixture()
        self.assertIsInstance(adapter, EvidenceExtractorV2)
        self.assertEqual("2.0.0", adapter.contract_version)
        self.assertEqual(("2.0.0",), adapter.supported_contract_versions)
        self.assertEqual(1, adapter.max_attempts)
        self.assertEqual(0, adapter.hidden_retries)

        v1_client = StubNovaClient()
        v1 = NovaLiteEvidenceExtractor(
            v1_client,
            model_version="nova-2-lite-v1-test",
            now_utc=lambda: NOW,
            monotonic_ms=lambda: 123_000,
            runtime_id="pa71-v1-compat-runtime",
        )
        request = repair_request_v1("OP-REP-V1-COMPAT")
        result = v1.repair(request)
        self.assertEqual("quarantined", result.outcome)
        self.assertEqual((), result.claims)
        self.assertEqual("1.0.0", v1.contract_version)

    def test_provider_payload_contains_authority_not_system_lineage(self) -> None:
        adapter, fixture = self.make_fixture()
        result = adapter.repair(repair_request("OP-REP-V2-PAYLOAD"))
        self.assertIsInstance(result, ExtractionResultDTO)
        payload = fixture.client.calls[0]["payload"]
        self.assertEqual(CONTENT, payload["authoritative_content"])
        self.assertEqual(["BTC"], payload["assets"])
        self.assertEqual(["regulatory"], payload["allowed_event_taxonomy"])
        for forbidden in (
            "task_id",
            "execution_id",
            "raw_record_id",
            "raw_content_hash",
            "repair_authorization_hash",
            "evidence_id",
        ):
            self.assertNotIn(forbidden, payload)
        self.assertLessEqual(fixture.client.calls[0]["timeout_ms"], 20_000)
        self.assertEqual(1, len(fixture.client.calls))

    def test_repair_single_flight_does_not_serialize_unrelated_operations(self) -> None:
        class ConcurrentClient(HarnessNovaClient):
            def __init__(self, clock: HarnessClock) -> None:
                super().__init__(clock)
                self.boundary = Barrier(2)

            def invoke(
                self,
                *,
                operation_id: str,
                payload: Mapping[str, object],
                timeout_ms: int,
                cancelled: Callable[[], bool],
            ) -> object:
                self.boundary.wait(timeout=2)
                return super().invoke(
                    operation_id=operation_id,
                    payload=payload,
                    timeout_ms=timeout_ms,
                    cancelled=cancelled,
                )

        clock = HarnessClock()
        concurrent_client = ConcurrentClient(clock)
        concurrent_fixture = HarnessFixture(
            clock,
            concurrent_client,
            HarnessContentResolver(clock),
        )
        adapter = make_adapter(concurrent_fixture)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                adapter.repair,
                repair_request("OP-REP-V2-CONCURRENT-A"),
            )
            second = executor.submit(
                adapter.repair,
                repair_request("OP-REP-V2-CONCURRENT-B"),
            )
            results = (first.result(timeout=3), second.result(timeout=3))
        self.assertTrue(
            all(isinstance(result, ExtractionResultDTO) for result in results)
        )
        self.assertEqual(2, len(concurrent_client.calls))

        class BlockingClient(HarnessNovaClient):
            def __init__(self, clock: HarnessClock) -> None:
                super().__init__(clock)
                self.entered = Event()
                self.release = Event()

            def invoke(
                self,
                *,
                operation_id: str,
                payload: Mapping[str, object],
                timeout_ms: int,
                cancelled: Callable[[], bool],
            ) -> object:
                self.entered.set()
                if not self.release.wait(timeout=2):
                    raise TimeoutError("bounded harness wait expired")
                return super().invoke(
                    operation_id=operation_id,
                    payload=payload,
                    timeout_ms=timeout_ms,
                    cancelled=cancelled,
                )

        single_clock = HarnessClock()
        blocking_client = BlockingClient(single_clock)
        single_fixture = HarnessFixture(
            single_clock,
            blocking_client,
            HarnessContentResolver(single_clock),
        )
        single_adapter = make_adapter(single_fixture)
        request = repair_request("OP-REP-V2-SINGLE-FLIGHT")
        with ThreadPoolExecutor(max_workers=2) as executor:
            leader = executor.submit(single_adapter.repair, request)
            self.assertTrue(blocking_client.entered.wait(timeout=2))
            follower = executor.submit(single_adapter.repair, request)
            blocking_client.release.set()
            leader_result = leader.result(timeout=3)
            follower_result = follower.result(timeout=3)
        self.assertIs(leader_result, follower_result)
        self.assertEqual(1, len(blocking_client.calls))

    def test_expired_follower_cannot_bypass_its_local_deadline(self) -> None:
        class ExpiringFollowerClient(HarnessNovaClient):
            def __init__(self, clock: HarnessClock) -> None:
                super().__init__(clock)
                self.entered = Event()
                self.release = Event()

            def invoke(
                self,
                *,
                operation_id: str,
                payload: Mapping[str, object],
                timeout_ms: int,
                cancelled: Callable[[], bool],
            ) -> object:
                self.entered.set()
                if not self.release.wait(timeout=2):
                    raise TimeoutError("bounded harness wait expired")
                self.clock.advance(50)
                return super().invoke(
                    operation_id=operation_id,
                    payload=payload,
                    timeout_ms=timeout_ms,
                    cancelled=cancelled,
                )

        class SignalledFollowerAdapter(NovaLiteEvidenceExtractorV2):
            def __init__(
                self,
                client: HarnessNovaClient,
                *,
                fixture: HarnessFixture,
            ) -> None:
                super().__init__(
                    client,
                    content_resolver=fixture.resolver,
                    model_version="nova-2-lite-v2-test",
                    now_utc=lambda: fixture.clock.now,
                    monotonic_ms=lambda: fixture.clock.monotonic,
                    runtime_id="pa71-v2-test-runtime",
                )
                self.follower_waiting = Event()

            def _await_inflight(
                self,
                identity: tuple[str, str],
                event: Event,
                local_deadline: LocalDeadline,
                operation_id: str,
            ) -> ExtractionResultDTO | ErrorResultDTO:
                self.follower_waiting.set()
                return super()._await_inflight(
                    identity,
                    event,
                    local_deadline,
                    operation_id,
                )

        clock = HarnessClock()
        client = ExpiringFollowerClient(clock)
        fixture = HarnessFixture(
            clock,
            client,
            HarnessContentResolver(clock),
        )
        adapter = SignalledFollowerAdapter(client, fixture=fixture)
        operation = "OP-REP-V2-FOLLOWER-DEADLINE"
        leader_request = repair_request(operation)
        follower_request = repair_request(
            operation,
            request_deadline=deadline(operation, budget_ms=20),
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            leader = executor.submit(adapter.repair, leader_request)
            self.assertTrue(client.entered.wait(timeout=2))
            follower = executor.submit(adapter.repair, follower_request)
            self.assertTrue(adapter.follower_waiting.wait(timeout=2))
            client.release.set()
            leader_result = leader.result(timeout=3)
            follower_result = follower.result(timeout=3)

        replay = adapter.repair(leader_request)
        self.assertIsInstance(leader_result, ExtractionResultDTO)
        self.assertIsInstance(follower_result, ErrorResultDTO)
        self.assertEqual("deadline_exceeded", follower_result.error.code)
        self.assertIs(replay, leader_result)
        self.assertEqual(1, len(client.calls))

    def test_deadline_construction_failure_completes_single_flight(self) -> None:
        clock = HarnessClock()
        fixture = HarnessFixture(
            clock,
            HarnessNovaClient(clock),
            HarnessContentResolver(clock),
        )
        adapter = NovaLiteEvidenceExtractorV2(
            fixture.client,
            content_resolver=fixture.resolver,
            model_version="nova-2-lite-v2-test",
            now_utc=lambda: fixture.clock.now,
            monotonic_ms=lambda: fixture.clock.monotonic,
            runtime_id="",
        )
        request = repair_request("OP-REP-V2-DEADLINE-CONSTRUCTION")

        first = adapter.repair(request)
        second = adapter.repair(request)

        self.assertIsInstance(first, ErrorResultDTO)
        self.assertEqual("unexpected_provider_error", first.error.code)
        self.assertIs(first, second)
        self.assertEqual(0, len(fixture.client.calls))

    def test_provider_diagnostics_and_sdk_like_objects_fail_closed_without_leakage(self) -> None:
        adapter, fixture = self.make_fixture()
        operation = "OP-REP-V2-DIAGNOSTIC"
        fixture.client.configure(operation, {
            "claims": [],
            "validation_errors": [{
                "path": "/Authorization: Bearer TOP-SECRET",
                "code": "vendor_private_code",
                "safe_message": "full raw prompt and provider stack",
            }],
            "usage": {"input_units": 1, "output_units": 1},
            "invocation_id": "INV-DIAGNOSTIC",
        })
        result = adapter.repair(repair_request(operation))
        self.assertIsInstance(result, ErrorResultDTO)
        self.assertEqual("invalid_extraction_schema", result.error.code)
        rendered = json.dumps(result.to_wire()).lower()
        for forbidden in (
            "top-secret",
            "authorization",
            "bearer",
            "full raw prompt",
            "provider stack",
            "vendor_private_code",
        ):
            self.assertNotIn(forbidden, rendered)

        class SdkLikeResponse:
            request_id = "AWS-REQUEST-ID"
            secret = "TOP-SECRET"

        sdk_adapter, sdk_fixture = self.make_fixture()
        sdk_fixture.client.configure("OP-REP-V2-SDK", SdkLikeResponse())
        sdk_result = sdk_adapter.repair(repair_request("OP-REP-V2-SDK"))
        self.assertEqual("invalid_extraction_schema", sdk_result.error.code)
        self.assertNotIn("top-secret", repr(sdk_result.to_wire()).lower())


if __name__ == "__main__":
    unittest.main()
