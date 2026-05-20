"""Shared completion policy for sync and streaming turn finalization."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.chat.orchestration.analysis_artifacts import (
    AnalysisCheckpointLifecycle,
    AnalysisPlanCompletionGuard,
    AnalysisPlanLifecycle,
)
from rhetorica_backend.domain.chat.orchestration.final_response_contract import (
    build_final_response_payload,
)
from rhetorica_backend.domain.chat.orchestration.final_payload_policy import (
    FinalPayloadPolicy,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    AbortDecision,
    ClarificationDecision,
    DecisionFinalizedTurnCompletion,
    FallbackTurnCompletion,
    FinalAnswerDecision,
    LoopLimitTurnCompletion,
    NonToolTurnCompletionInput,
    SuccessfulTurnCompletion,
    TurnCompletion,
)


class TurnCompletionPolicy:
    """Owns terminal turn completion rules outside runtime shells."""

    def __init__(
        self,
        *,
        build_final_payload_from_parsed: Callable[..., dict[str, Any]],
        fallback_final_payload: Callable[[], dict[str, Any]],
        finalize_raw_answer: Callable[..., dict[str, Any]],
        has_needs_user_clarification_flag: Callable[[dict[str, Any]], bool],
        incomplete_tool_orchestration_payload: Callable[[Any], dict[str, Any]],
        is_acceptable_final_payload: Callable[..., bool],
        strip_thinking_tags: Callable[[str], str],
        to_optional_str: Callable[[Any], str | None],
        validate_or_repair_final_payload: Callable[..., dict[str, Any]],
    ) -> None:
        self._build_final_payload_from_parsed = build_final_payload_from_parsed
        self._fallback_final_payload = fallback_final_payload
        self._finalize_raw_answer = finalize_raw_answer
        self._has_needs_user_clarification_flag = has_needs_user_clarification_flag
        self._incomplete_tool_orchestration_payload = (
            incomplete_tool_orchestration_payload
        )
        self._is_acceptable_final_payload = is_acceptable_final_payload
        self._strip_thinking_tags = strip_thinking_tags
        self._to_optional_str = to_optional_str
        self._validate_or_repair_final_payload = validate_or_repair_final_payload
        self._analysis_plan_guard = AnalysisPlanCompletionGuard()
        self._analysis_plan_lifecycle = AnalysisPlanLifecycle()

    def build_final_payload_from_parsed(
        self,
        *,
        parsed_payload: dict[str, Any],
        fallback_answer: str | None,
        state: Any,
        normalize_answer: Callable[..., str],
        resolve_execution_attempt_from_payload: Callable[..., Any],
        resolve_execution_from_payload: Callable[..., Any],
        resolve_sql_from_payload: Callable[..., str | None],
        resolve_selected_connector_from_payload: Callable[..., str | None],
        runtime_context_payload_from_state: Callable[[Any], dict[str, Any]],
        successful_execution_attempts: Callable[[Any], list[Any]],
        build_capability_trace_from_state: Callable[..., Any],
        augment_payload_with_explainability: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        target = (
            parsed_payload.get("target")
            if isinstance(parsed_payload.get("target"), dict)
            else None
        )
        answer = self._to_optional_str(parsed_payload.get("answer"))
        if answer is None:
            answer = self._to_optional_str(fallback_answer)
        if answer is None:
            answer = "Model did not return a valid answer."
        answer = self._strip_thinking_tags(answer).strip() or answer
        answer = normalize_answer(answer=answer, state=state)

        execution_attempt = resolve_execution_attempt_from_payload(
            llm_payload=parsed_payload,
            state=state,
        )
        execution = resolve_execution_from_payload(
            llm_payload=parsed_payload,
            state=state,
        )
        payload = {
            "answer": answer,
            "sql": resolve_sql_from_payload(
                llm_payload=parsed_payload,
                state=state,
                execution_attempt=execution_attempt,
            ),
            "target": target,
            "selected_database_connector": resolve_selected_connector_from_payload(
                llm_payload=parsed_payload,
                state=state,
                execution_attempt=execution_attempt,
            ),
            "metadata_sources": sorted(state.metadata_sources),
            "context_tables": state.context_tables[:20],
            "runtime_context": runtime_context_payload_from_state(state),
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
                for attempt in successful_execution_attempts(state)
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
            "capability_trace": build_capability_trace_from_state(
                state=state,
                metadata_sources=sorted(state.metadata_sources),
                warnings=state.warnings,
                execution=execution,
            ).model_dump(mode="json"),
            "prompt_version": state.prompt_version,
            "warnings": state.warnings,
        }
        return augment_payload_with_explainability(
            payload=payload,
            state=state,
        )

    def finalize_direct_plain_text(
        self,
        *,
        raw_final_text: str,
        state: Any,
        uncertainty_flags: list[str] | None = None,
        normalize_answer: Callable[..., str],
        resolve_execution_attempt_from_payload: Callable[..., Any],
        resolve_execution_from_payload: Callable[..., Any],
        resolve_sql_from_payload: Callable[..., str | None],
        resolve_selected_connector_from_payload: Callable[..., str | None],
        runtime_context_payload_from_state: Callable[[Any], dict[str, Any]],
        successful_execution_attempts: Callable[[Any], list[Any]],
        build_capability_trace_from_state: Callable[..., Any],
        augment_payload_with_explainability: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        state.add_warning("deterministic_finalizer_used")
        answer = self._strip_thinking_tags(raw_final_text).strip() or raw_final_text
        return self.build_final_payload_from_parsed(
            parsed_payload=build_final_response_payload(
                answer_markdown=answer,
                sql=state.generated_sql,
                uncertainty_flags=list(uncertainty_flags or []),
            ),
            fallback_answer=answer,
            state=state,
            normalize_answer=normalize_answer,
            resolve_execution_attempt_from_payload=resolve_execution_attempt_from_payload,
            resolve_execution_from_payload=resolve_execution_from_payload,
            resolve_sql_from_payload=resolve_sql_from_payload,
            resolve_selected_connector_from_payload=resolve_selected_connector_from_payload,
            runtime_context_payload_from_state=runtime_context_payload_from_state,
            successful_execution_attempts=successful_execution_attempts,
            build_capability_trace_from_state=build_capability_trace_from_state,
            augment_payload_with_explainability=augment_payload_with_explainability,
        )

    def complete_non_tool_turn(
        self,
        *,
        completion_input: NonToolTurnCompletionInput,
        decision: FinalAnswerDecision | ClarificationDecision | AbortDecision | None,
        llm_client: LlmClient,
        request: ChatAskRequest,
        response_schema: dict[str, Any],
        state: Any,
        direct_plain_text_finalizer: Callable[..., dict[str, Any]],
    ) -> TurnCompletion:
        if isinstance(decision, FinalAnswerDecision):
            self._complete_final_answer_step(state)
            analysis_plan = getattr(state, "analysis_plan", None)
            if not self._analysis_plan_guard.can_finalize(analysis_plan):
                state.add_warning("analysis_plan_required_evidence_missing")
                incomplete_steps = self._analysis_plan_guard.incomplete_steps(analysis_plan)
                if incomplete_steps:
                    state.add_warning("analysis_plan_incomplete_steps:" + ",".join(incomplete_steps))
                missing = self._analysis_plan_guard.missing_evidence(analysis_plan)
                if missing:
                    state.add_warning("analysis_plan_missing_evidence:" + ",".join(missing))
                finalized_payload = self._finalize_decision_payload(
                    decision=decision,
                    llm_client=llm_client,
                    request=request,
                    state=state,
                    direct_plain_text_finalizer=direct_plain_text_finalizer,
                )
                return FallbackTurnCompletion(
                    reason="analysis_plan_incomplete",
                    payload=finalized_payload,
                )
            self._complete_analysis_runtime(state)
            finalized_payload = self._finalize_decision_payload(
                decision=decision,
                llm_client=llm_client,
                request=request,
                state=state,
                direct_plain_text_finalizer=direct_plain_text_finalizer,
            )
            return DecisionFinalizedTurnCompletion(
                decision_type="final_answer",
                payload=finalized_payload,
            )
        if isinstance(decision, ClarificationDecision):
            return DecisionFinalizedTurnCompletion(
                decision_type="clarification",
                payload=self._finalize_decision_payload(
                    decision=decision,
                    llm_client=llm_client,
                    request=request,
                    state=state,
                    direct_plain_text_finalizer=direct_plain_text_finalizer,
                ),
            )
        if isinstance(decision, AbortDecision):
            return FallbackTurnCompletion(
                reason="abort",
                payload=self._incomplete_tool_orchestration_payload(state),
            )

        soft_fallback = self._should_apply_metadata_only_soft_fallback(
            completion_input=completion_input,
            state=state,
        )
        if soft_fallback:
            state.add_warning("deterministic_finalizer_used")

        parsed_payload = self._validate_or_repair_final_payload(
            llm_client=llm_client,
            raw_content=completion_input.raw_content,
            state=state,
            response_schema=response_schema,
        )
        if self._has_needs_user_clarification_flag(parsed_payload):
            state.add_warning("llm_user_clarification_requested")
        if not self._is_acceptable_final_payload(
            llm_payload=parsed_payload,
            state=state,
            request=request,
        ):
            state.add_warning("llm_unacceptable_final_payload")
            fallback_payload = self._fallback_final_payload()
            if soft_fallback:
                fallback_payload = {
                    **fallback_payload,
                    "warnings": state.warnings,
                }
            return FallbackTurnCompletion(
                reason="unacceptable_final_payload",
                payload=fallback_payload,
                soft_fallback_applied=soft_fallback,
            )
        self._complete_final_answer_step(state)
        analysis_plan = getattr(state, "analysis_plan", None)
        if not self._analysis_plan_guard.can_finalize(analysis_plan):
            state.add_warning("analysis_plan_required_evidence_missing")
            incomplete_steps = self._analysis_plan_guard.incomplete_steps(analysis_plan)
            if incomplete_steps:
                state.add_warning("analysis_plan_incomplete_steps:" + ",".join(incomplete_steps))
            missing = self._analysis_plan_guard.missing_evidence(analysis_plan)
            if missing:
                state.add_warning("analysis_plan_missing_evidence:" + ",".join(missing))
            finalized_payload = self._build_final_payload_from_parsed(
                parsed_payload=parsed_payload,
                fallback_answer=completion_input.raw_content,
                state=state,
            )
            return FallbackTurnCompletion(
                reason="analysis_plan_incomplete",
                payload=finalized_payload,
            )
        self._complete_analysis_runtime(state)
        finalized_payload = self._build_final_payload_from_parsed(
            parsed_payload=parsed_payload,
            fallback_answer=completion_input.raw_content,
            state=state,
        )

        return SuccessfulTurnCompletion(
            payload=finalized_payload
        )

    def complete_loop_limit(self, *, state: Any) -> LoopLimitTurnCompletion:
        state.add_warning("llm_tool_loop_limit_reached")
        if state.generated_sql or state.execution:
            state.add_warning("llm_tool_loop_partial_result_available")
        return LoopLimitTurnCompletion(
            payload=self._incomplete_tool_orchestration_payload(state)
        )

    @staticmethod
    def _complete_analysis_runtime(state: Any) -> None:
        analysis_plan = getattr(state, "analysis_plan", None)
        if analysis_plan is not None and analysis_plan.status != "completed":
            state.analysis_plan = analysis_plan.model_copy(
                update={"status": "completed"}
            )
        checkpoint = getattr(state, "checkpoint", None)
        if checkpoint is not None and checkpoint.status != "completed":
            state.checkpoint = AnalysisCheckpointLifecycle().complete(
                checkpoint=checkpoint
            )
        persist = getattr(state, "checkpoint_persist", None)
        if callable(persist):
            persist(state)

    def _complete_final_answer_step(self, state: Any) -> None:
        plan = getattr(state, "analysis_plan", None)
        if plan is None:
            return
        step = next(
            (
                step
                for step in plan.steps
                if step.status in {"planned", "running", "blocked"}
                and "final_answer" in step.required_evidence
            ),
            None,
        )
        if step is None:
            return
        state.analysis_plan = self._analysis_plan_lifecycle.update_step(
            plan=plan,
            step_id=step.id,
            status="done",
            evidence_ids=["final_answer"],
        )
        checkpoint = getattr(state, "checkpoint", None)
        if checkpoint is not None:
            state.checkpoint = AnalysisCheckpointLifecycle().mark_step_completed(
                checkpoint=checkpoint,
                step_id=step.id,
                evidence_ids=["final_answer"],
            )

    def _finalize_decision_payload(
        self,
        *,
        decision: FinalAnswerDecision | ClarificationDecision,
        llm_client: LlmClient,
        request: ChatAskRequest,
        state: Any,
        direct_plain_text_finalizer: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        raw_text = (
            decision.raw_final_text
            if isinstance(decision, FinalAnswerDecision)
            else decision.answer_markdown
        )
        uncertainty_flags = list(decision.uncertainty_flags)
        if decision.finalization_mode == "direct":
            return direct_plain_text_finalizer(
                raw_final_text=raw_text,
                state=state,
                uncertainty_flags=uncertainty_flags,
            )
        if self._can_finalize_deterministically(raw_text=raw_text, state=state):
            direct_payload = direct_plain_text_finalizer(
                raw_final_text=raw_text,
                state=state,
                uncertainty_flags=uncertainty_flags,
            )
            if self._has_needs_user_clarification_flag(direct_payload):
                state.add_warning("llm_user_clarification_requested")
                return direct_payload
            if self._is_acceptable_final_payload(
                llm_payload=direct_payload,
                state=state,
                request=request,
            ):
                return direct_payload
            state.add_warning("deterministic_finalizer_unacceptable")
        return self._finalize_raw_answer(
            llm_client=llm_client,
            request=request,
            raw_final_text=raw_text,
            state=state,
            uncertainty_flags=uncertainty_flags,
        )

    @staticmethod
    def _can_finalize_deterministically(
        *,
        raw_text: str,
        state: Any,
    ) -> bool:
        answer = FinalPayloadPolicy.to_optional_str(
            FinalPayloadPolicy.strip_thinking_tags(raw_text)
        )
        if answer is None:
            return False
        if FinalPayloadPolicy.looks_like_nonfinal_raw_payload(answer):
            return False
        if FinalPayloadPolicy.looks_like_progress_text(answer):
            return False
        if FinalPayloadPolicy.has_structured_markdown(answer):
            return True
        if getattr(state, "visualization_candidate", None) is not None:
            return True
        return bool(FinalPayloadPolicy.successful_execution_attempts(state))

    @staticmethod
    def _should_apply_metadata_only_soft_fallback(
        *,
        completion_input: NonToolTurnCompletionInput,
        state: Any,
    ) -> bool:
        return (
            completion_input.has_current_tools
            and bool(completion_input.finalizable_content)
            and state.tool_call_count == 0
            and "run_sql" not in completion_input.current_tool_names
        )
