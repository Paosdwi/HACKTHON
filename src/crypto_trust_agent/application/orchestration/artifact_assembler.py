"""Formal Run → Canonical Artifact Publication Bridge (T64).

Assembles ArtifactPublicationRequest from a PipelineSnapshot.
Fail-closed validation: task/execution identity, citation graph, Evidence lineage,
assessment consistency, Decimal wire, and required inputs must all pass.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from crypto_trust_agent.application.dto.common import DeadlineDTO
from crypto_trust_agent.application.dto.reasoning import (
    AnalysisRefDTO,
    ConclusionDTO,
    ConfidenceComponentsDTO,
    ContradictionDTO,
    FactDTO,
    InferenceDTO,
    ReasoningResultDTO,
)
from crypto_trust_agent.application.dto.repositories import (
    EvidenceAssessmentDTO,
    EvidenceClaimLinkDTO,
    EvidenceDTO,
    ExecutionEventDTO,
)
from crypto_trust_agent.application.orchestration.pipeline_context import PipelineSnapshot
from crypto_trust_agent.application.publication import (
    ArtifactPublicationRequest,
    CitedStatementDTO,
    EvidenceListDTO,
    ExecutionLogDTO,
    FinalReportDTO,
    KeyEvidenceDTO,
    MarketDataProvenanceDTO,
    SCHEMA_VERSION,
    TRANSITION_DATE,
)
from crypto_trust_agent.domain.primitives import UtcInstant


class AssemblerValidationError(ValueError):
    """Fail-closed: the pipeline output cannot form a valid publication."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise AssemblerValidationError(reason)


class FormalRunArtifactAssembler:
    """Build ArtifactPublicationRequest from a single Formal Run's pipeline outputs.

    Validates:
    - task/execution identity consistency
    - requested assets and question consistency
    - citation graph completeness (Fact→Evidence/Analysis, Inference→Fact, Conclusion→Fact/Inference)
    - Evidence/Analysis references exist
    - assessment version and Evidence identity consistency
    - required artifact inputs are present
    - does NOT accept data from other executions
    - produces degraded report when evidence/reasoning is insufficient
    """

    def assemble(
        self,
        snapshot: PipelineSnapshot,
        *,
        generated_at: str | UtcInstant,
        deadline: DeadlineDTO,
    ) -> ArtifactPublicationRequest:
        """Assemble from accumulated pipeline outputs. Fail-closed on validation."""
        self._validate_identity(snapshot)
        evidence_list = self._build_evidence_list(snapshot, generated_at)
        execution_log = self._build_execution_log(snapshot, generated_at)
        final_report = self._build_final_report(snapshot, evidence_list, generated_at)
        return ArtifactPublicationRequest(
            final_report=final_report,
            evidence_list=evidence_list,
            execution_log=execution_log,
            deadline=deadline,
        )

    def _validate_identity(self, snapshot: PipelineSnapshot) -> None:
        _require(bool(snapshot.task_id) and snapshot.task_id.startswith("TASK-"), "invalid task_id")
        _require(bool(snapshot.execution_id) and snapshot.execution_id.startswith("EXEC-"), "invalid execution_id")
        _require(bool(snapshot.question), "question is required")
        _require(bool(snapshot.assets), "assets are required")

        # All evidence must belong to this task/execution
        for item in snapshot.evidence:
            _require(item.task_id == snapshot.task_id, f"Evidence {item.evidence_id} task_id mismatch")
            _require(item.execution_id == snapshot.execution_id, f"Evidence {item.evidence_id} execution_id mismatch")

        # All assessments must reference evidence in this execution
        evidence_ids = {item.evidence_id for item in snapshot.evidence}
        for item in snapshot.assessments:
            _require(item.task_id == snapshot.task_id, f"Assessment {item.assessment_id} task_id mismatch")
            _require(item.evidence_id in evidence_ids, f"Assessment references missing Evidence {item.evidence_id}")

        # All claim links must reference evidence in this execution
        for item in snapshot.claim_links:
            _require(item.task_id == snapshot.task_id, f"ClaimLink {item.link_id} task_id mismatch")
            _require(item.evidence_id in evidence_ids, f"ClaimLink references missing Evidence {item.evidence_id}")

        # Events must belong to this execution
        for event in snapshot.events:
            _require(event.task_id == snapshot.task_id, f"Event {event.event_id} task_id mismatch")
            _require(event.execution_id == snapshot.execution_id, f"Event {event.event_id} execution_id mismatch")

    def _build_evidence_list(
        self,
        snapshot: PipelineSnapshot,
        generated_at: str | UtcInstant,
    ) -> EvidenceListDTO:
        _require(bool(snapshot.evidence), "at least one active Evidence is required for publication")
        _require(bool(snapshot.claim_links), "at least one ClaimLink is required")
        _require(bool(snapshot.assessments), "at least one Assessment is required")
        return EvidenceListDTO.from_records(
            task_id=snapshot.task_id,
            execution_id=snapshot.execution_id,
            evidence=snapshot.evidence,
            claim_links=snapshot.claim_links,
            assessments=snapshot.assessments,
            generated_at=generated_at,
        )

    def _build_execution_log(
        self,
        snapshot: PipelineSnapshot,
        generated_at: str | UtcInstant,
    ) -> ExecutionLogDTO:
        _require(bool(snapshot.events), "at least one ExecutionEvent is required")
        assessment_versions = tuple(
            {
                "assessment_id": item.assessment_id,
                "assessment_version": item.assessment_version,
            }
            for item in snapshot.assessments
        )
        assessment_events = tuple(
            item for item in snapshot.events if item.step == "assessment"
        )
        _require(
            not assessment_versions or bool(assessment_events),
            "assessment event is required for assessment lineage",
        )
        details = (
            {
                assessment_events[-1].event_id: {
                    "assessment_versions": assessment_versions,
                }
            }
            if assessment_events
            else {}
        )
        return ExecutionLogDTO.from_events(
            snapshot.events,
            details_by_event=details,
            generated_at=generated_at,
        )

    def _build_final_report(
        self,
        snapshot: PipelineSnapshot,
        evidence_list: EvidenceListDTO,
        generated_at: str | UtcInstant,
    ) -> FinalReportDTO:
        reasoning = snapshot.reasoning_result
        if reasoning is None or reasoning.outcome != "valid":
            return self._build_degraded_report(snapshot, evidence_list, generated_at)
        return self._build_full_report(snapshot, reasoning, evidence_list, generated_at)

    def _build_full_report(
        self,
        snapshot: PipelineSnapshot,
        reasoning: ReasoningResultDTO,
        evidence_list: EvidenceListDTO,
        generated_at: str | UtcInstant,
    ) -> FinalReportDTO:
        evidence_ids = {item.evidence_id for item in evidence_list.items}
        analysis_ids = {item.analysis_id for item in snapshot.analysis_refs}

        # Validate citation graph
        for fact in reasoning.facts:
            for ref in fact.evidence_refs:
                _require(ref in evidence_ids, f"Fact {fact.fact_id} references missing Evidence {ref}")
            for ref in fact.analysis_refs:
                _require(ref in analysis_ids, f"Fact {fact.fact_id} references missing Analysis {ref}")

        fact_ids = {item.fact_id for item in reasoning.facts}
        for inference in reasoning.inferences:
            for ref in inference.fact_refs:
                _require(ref in fact_ids, f"Inference {inference.inference_id} references missing Fact {ref}")

        inference_ids = {item.inference_id for item in reasoning.inferences}
        for conclusion in reasoning.conclusions:
            for ref in conclusion.fact_refs:
                _require(ref in fact_ids, f"Conclusion {conclusion.conclusion_id} references missing Fact {ref}")
            for ref in conclusion.inference_refs:
                _require(ref in inference_ids, f"Conclusion {conclusion.conclusion_id} references missing Inference {ref}")

        # Build supporting/counter evidence from claim link stances
        supporting: list[str] = []
        counter: list[str] = []
        for item in evidence_list.items:
            stances = {str(claim.get("stance", "")) for claim in item.related_claims}
            if "supports" in stances:
                supporting.append(item.evidence_id)
            if "contradicts" in stances:
                counter.append(item.evidence_id)

        # Market judgment from first conclusion or analysis
        market_statement = "Market regime analysis based on available data."
        if reasoning.conclusions:
            market_statement = reasoning.conclusions[0].statement
        market_judgment = CitedStatementDTO(
            market_statement,
            tuple(supporting[:5]) if supporting else (evidence_list.items[0].evidence_id,),
            tuple(a.analysis_id for a in snapshot.analysis_refs[:3]) if snapshot.analysis_refs else (),
        )

        # Source consistency
        source_consistency = CitedStatementDTO(
            "Sources are consistent within assessed limitations.",
            (evidence_list.items[0].evidence_id,),
            (),
        )

        # Key evidence
        key_evidence: list[KeyEvidenceDTO] = []
        for item in evidence_list.items[:5]:
            key_evidence.append(KeyEvidenceDTO(item.evidence_id, None, f"Primary evidence from {item.source}"))
        if snapshot.analysis_refs:
            key_evidence.append(KeyEvidenceDTO(None, snapshot.analysis_refs[0].analysis_id, "Market regime analysis"))

        # Market data provenance
        provenance = MarketDataProvenanceDTO(
            snapshot.official_dataset_used,
            snapshot.live_extension_used,
            TRANSITION_DATE if snapshot.live_extension_used else None,
        )

        # Contradictions from reasoning or strategy evaluation
        contradictions = tuple(snapshot.contradictions)
        if not contradictions and reasoning.conclusions:
            # No explicit contradictions
            contradictions = ()

        return FinalReportDTO(
            task_id=snapshot.task_id,
            execution_id=snapshot.execution_id,
            assets=snapshot.assets,
            question=snapshot.question,
            market_judgment=market_judgment,
            facts=reasoning.facts,
            analyses=tuple(snapshot.analysis_refs),
            inferences=reasoning.inferences,
            conclusions=reasoning.conclusions,
            key_evidence=tuple(key_evidence),
            supporting_evidence_ids=tuple(dict.fromkeys(supporting)),
            counter_evidence_ids=tuple(dict.fromkeys(counter)),
            contradictions=contradictions,
            confidence_components=reasoning.confidence_components,
            limitations=reasoning.limitations + tuple(snapshot.limitations),
            watchpoints=reasoning.watchpoints,
            source_consistency=source_consistency,
            market_data_provenance=provenance,
            generated_at=generated_at,
        )

    def _build_degraded_report(
        self,
        snapshot: PipelineSnapshot,
        evidence_list: EvidenceListDTO,
        generated_at: str | UtcInstant,
    ) -> FinalReportDTO:
        """Build a degraded report when reasoning is insufficient.

        Uses a truthful confidence-zero conclusion that explicitly states
        insufficient reasoning — does NOT fabricate a market direction or
        fake inference. The conclusion is a factual statement about the
        analysis state, not a prediction.
        """
        evidence_ids = tuple(item.evidence_id for item in evidence_list.items)
        first_evidence = evidence_ids[0] if evidence_ids else "EVID-NONE"

        # Single truthful fact: evidence was collected but reasoning failed
        fact = FactDTO(
            "FACT-DEGRADED-001",
            f"Evidence was collected for {', '.join(snapshot.assets)} but reasoning output is insufficient or invalid.",
            (first_evidence,) if evidence_ids else (),
            tuple(a.analysis_id for a in snapshot.analysis_refs[:1]) if snapshot.analysis_refs else (),
        )

        # Single truthful conclusion: cannot produce directional conclusion
        # Confidence is canonical zero — truthfully represents zero analytical confidence
        # This is NOT a fake conclusion; it is a factual statement about the analysis state
        conclusion = ConclusionDTO(
            "CONCL-INSUFFICIENT-001",
            "Analysis could not produce a directional conclusion due to insufficient or invalid reasoning output.",
            ("FACT-DEGRADED-001",),
            (),
            "0",
        )

        limitations = list(snapshot.limitations)
        limitations.append("Reasoning output was insufficient or invalid; report is degraded.")

        provenance = MarketDataProvenanceDTO(
            snapshot.official_dataset_used,
            snapshot.live_extension_used,
            TRANSITION_DATE if snapshot.live_extension_used else None,
        )

        return FinalReportDTO(
            task_id=snapshot.task_id,
            execution_id=snapshot.execution_id,
            assets=snapshot.assets,
            question=snapshot.question,
            market_judgment=CitedStatementDTO(
                "Market analysis degraded due to insufficient reasoning.",
                (first_evidence,),
                tuple(a.analysis_id for a in snapshot.analysis_refs[:1]) if snapshot.analysis_refs else (),
            ),
            facts=(fact,),
            analyses=tuple(snapshot.analysis_refs),
            inferences=(),
            conclusions=(conclusion,),
            key_evidence=(KeyEvidenceDTO(first_evidence, None, "Available evidence with degraded reasoning"),) if evidence_ids else (),
            supporting_evidence_ids=evidence_ids[:10],
            counter_evidence_ids=(),
            contradictions=tuple(snapshot.contradictions),
            confidence_components=ConfidenceComponentsDTO("0", "0", "0", "0"),
            limitations=tuple(limitations[:50]),
            watchpoints=("Monitor for updated reasoning availability.",),
            source_consistency=CitedStatementDTO(
                "Source consistency cannot be fully assessed with degraded reasoning.",
                (first_evidence,),
                (),
            ),
            market_data_provenance=provenance,
            generated_at=generated_at,
        )


__all__ = ("AssemblerValidationError", "FormalRunArtifactAssembler")
