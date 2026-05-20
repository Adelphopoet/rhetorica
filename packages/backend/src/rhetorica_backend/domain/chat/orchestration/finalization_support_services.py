"""Focused finalization collaborators for transport orchestrators."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rhetorica_backend.domain.chat.orchestration import shared_kernel
from rhetorica_backend.domain.chat.orchestration.final_response_contract import (
    build_final_response_payload,
)
from rhetorica_backend.domain.chat.orchestration.llm_finalization_policy import (
    LlmFinalizationPolicy,
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

        execution_attempt = shared_kernel.resolve_execution_attempt_from_payload(
            llm_payload=parsed_payload,
            state=state,
        )
        execution = shared_kernel.resolve_execution_from_payload(
            llm_payload=parsed_payload,
            state=state,
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
            "analysis_plan": (
                state.analysis_plan.model_dump(mode="json")
                if getattr(state, "analysis_plan", None) is not None
                else None
            ),
            "worker_results": [
                result.model_dump(mode="json")
                for result in getattr(state, "worker_results", [])[-5:]
            ],
            "checkpoint_digest": (
                state.checkpoint.digest_payload()
                if getattr(state, "checkpoint", None) is not None
                else None
            ),
            "claims": parsed_payload.get("claims", []),
            "evidence": parsed_payload.get("evidence", []),
            "claim_evidence_links": parsed_payload.get("claim_evidence_links", []),
            "uncertainty_flags": parsed_payload.get("uncertainty_flags", []),
            "execution": execution.model_dump(mode="json") if execution else None,
            "successful_executions": [
                attempt.execution.model_dump(mode="json")
                for attempt in shared_kernel.successful_execution_attempts(state)
            ],
            "analysis_document": (
                parsed_payload.get("analysis_document")
                if isinstance(parsed_payload.get("analysis_document"), dict)
                else None
            ),
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
        }
        return shared_kernel.augment_payload_with_explainability(
            payload=payload,
            state=state,
        )

    def finalize_direct_plain_text(
        self,
        *,
        raw_final_text: str,
        state: Any,
        uncertainty_flags: list[str] | None = None,
    ) -> dict[str, Any]:
        state.add_warning("deterministic_finalizer_used")
        answer = shared_kernel.strip_thinking_tags(raw_final_text).strip() or raw_final_text
        return self.build_final_payload_from_parsed(
            parsed_payload=build_final_response_payload(
                answer_markdown=answer,
                sql=state.generated_sql,
                uncertainty_flags=list(uncertainty_flags or []),
            ),
            fallback_answer=answer,
            state=state,
        )

    @staticmethod
    def fallback_final_payload() -> dict[str, Any]:
        return {
            "answer": (
                "I could not produce a schema-valid answer. "
                "Please retry the question with a narrower scope."
            ),
            "answer_markdown": (
                "I could not produce a schema-valid answer. "
                "Please retry the question with a narrower scope."
            ),
            "sql": None,
            "target": None,
            "claims": [],
            "evidence": [],
            "claim_evidence_links": [],
            "uncertainty_flags": ["schema_validation_failed"],
            "visualization": None,
        }


class RecoveryFinalizationService:
    """Decorates LLM finalization policy with runtime-loop diagnostics."""

    def __init__(
        self,
        *,
        finalization_policy: LlmFinalizationPolicy,
        emit_diagnostic_event: Callable[..., None],
    ) -> None:
        self._finalization_policy = finalization_policy
        self._emit_diagnostic_event = emit_diagnostic_event

    def attempt_execution_recovery_finalization(self, **kwargs: Any) -> dict[str, Any] | None:
        return self._finalization_policy.attempt_execution_recovery_finalization(**kwargs)

    def attempt_duplicate_loop_recovery_finalization(
        self,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        state = kwargs.get("state")
        if state is not None:
            self._emit_diagnostic_event(
                "llm.duplicate_tool_loop.recovery_requested",
                successful_execution_count=len(shared_kernel.successful_execution_attempts(state)),
                warnings=list(state.warnings),
            )
        return self._finalization_policy.attempt_duplicate_loop_recovery_finalization(**kwargs)
