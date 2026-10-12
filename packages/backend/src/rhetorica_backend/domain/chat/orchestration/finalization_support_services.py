"""Focused finalization collaborators for transport orchestrators."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rhetorica_backend.domain.chat.orchestration import shared_kernel
from rhetorica_backend.domain.chat.orchestration.final_payload_policy import (
    FinalPayloadPolicy,
)


class FinalPayloadBuilderService:
    """Builds normalized final payloads without transport-shell logic."""

    def __init__(
        self,
        *,
        normalize_answer: Callable[..., str],
    ) -> None:
        self._normalize_answer = normalize_answer

    def build_final_payload_from_parsed(
        self,
        *,
        parsed_payload: dict[str, Any],
        fallback_answer: str | None,
        state: Any,
    ) -> dict[str, Any]:
        target = (
            parsed_payload.get("target") if isinstance(parsed_payload.get("target"), dict) else None
        )
        answer = shared_kernel.to_optional_str(
            parsed_payload.get("answer_markdown") or parsed_payload.get("answer")
        )
        if answer is None:
            answer = shared_kernel.to_optional_str(fallback_answer)
        if answer is None:
            answer = "Model did not return a valid answer."
        answer = shared_kernel.strip_thinking_tags(answer).strip() or answer
        answer = self._normalize_answer(answer=answer, state=state)
        evidence = self._supported_evidence(parsed_payload.get("evidence"), state)
        claim_evidence_links = self._links_for_evidence(
            links=parsed_payload.get("claim_evidence_links"),
            evidence=evidence,
        )

        execution_attempt = shared_kernel.resolve_execution_attempt_from_payload(
            llm_payload=parsed_payload,
            state=state,
        )
        execution = shared_kernel.resolve_execution_from_payload(
            llm_payload=parsed_payload,
            state=state,
        )
        analysis_document = (
            parsed_payload.get("analysis_document")
            if isinstance(parsed_payload.get("analysis_document"), dict)
            else shared_kernel.analysis_document_from_visualization_candidates(state=state)
        )
        payload = {
            "answer": answer,
            "sql": shared_kernel.resolve_sql_from_payload(
                llm_payload=parsed_payload,
                state=state,
                execution_attempt=execution_attempt,
            ),
            "target": target,
            "selected_database_connector": (
                shared_kernel.resolve_selected_connector_from_payload(
                    llm_payload=parsed_payload,
                    state=state,
                    execution_attempt=execution_attempt,
                )
            ),
            "metadata_sources": sorted(state.metadata_sources),
            "context_tables": state.context_tables[:20],
            "runtime_context": shared_kernel.runtime_context_payload_from_state(state),
            "claims": parsed_payload.get("claims", []),
            "evidence": evidence,
            "claim_evidence_links": claim_evidence_links,
            "uncertainty_flags": parsed_payload.get("uncertainty_flags", []),
            "execution": execution.model_dump(mode="json") if execution else None,
            "successful_executions": [
                attempt.execution.model_dump(mode="json")
                for attempt in shared_kernel.successful_execution_attempts(state)
            ],
            "analysis_document": analysis_document,
            "visualization": (
                parsed_payload.get("visualization")
                if isinstance(parsed_payload.get("visualization"), dict)
                else state.visualization_candidate.model_dump(mode="json")
                if getattr(state, "visualization_candidate", None) is not None
                else None
            ),
            "optimization_trace": state.optimization_trace.model_dump(mode="json"),
            "capability_trace": shared_kernel.build_capability_trace_from_state(
                state=state,
                metadata_sources=sorted(state.metadata_sources),
                warnings=state.warnings,
                execution=execution,
            ).model_dump(mode="json"),
            "prompt_version": state.prompt_version,
            "warnings": state.warnings,
            "files": [item.model_dump(mode="json") for item in state.published_files],
        }
        return shared_kernel.augment_payload_with_explainability(
            payload=payload,
            state=state,
        )

    @staticmethod
    def _supported_evidence(value: Any, state: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        supported: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            if FinalPayloadPolicy.has_supported_evidence({"evidence": [item]}, state):
                supported.append(item)
        return supported

    @staticmethod
    def _links_for_evidence(
        *,
        links: Any,
        evidence: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not isinstance(links, list) or not evidence:
            return []
        supported_ids = {
            item["evidence_id"]
            for item in evidence
            if isinstance(item.get("evidence_id"), str)
        }
        filtered: list[dict[str, Any]] = []
        for link in links:
            if not isinstance(link, dict) or not isinstance(link.get("claim_id"), str):
                continue
            evidence_ids = link.get("evidence_ids")
            if not isinstance(evidence_ids, list):
                continue
            kept_ids = [
                evidence_id
                for evidence_id in evidence_ids
                if isinstance(evidence_id, str) and evidence_id in supported_ids
            ]
            if kept_ids:
                filtered.append({"claim_id": link["claim_id"], "evidence_ids": kept_ids})
        return filtered
