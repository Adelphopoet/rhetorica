"""Shared tool execution policy for duplicate handling and fatal loop stops."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rhetorica_backend.domain.chat.orchestration.models import (
    ContinueToolResultDecision,
    DuplicateToolCallPlan,
    ExecuteToolCallPlan,
    StopToolResultDecision,
    ToolCallExecutionPlan,
    ToolResultDecision,
)
from rhetorica_backend.domain.chat.orchestration.tool_error_policy import (
    ToolErrorPolicy,
)


class ToolExecutionPolicy:
    """Owns tool-loop decisions outside orchestrator runtime shells."""

    def __init__(
        self,
        *,
        latest_successful_execution_attempt: Callable[[Any], Any | None],
        runtime_context_payload_from_state: Callable[[Any], dict[str, Any]],
        build_capability_trace_from_state: Callable[..., Any],
        successful_execution_attempts: Callable[[Any], list[Any]],
    ) -> None:
        self._latest_successful_execution_attempt = latest_successful_execution_attempt
        self._runtime_context_payload_from_state = runtime_context_payload_from_state
        self._build_capability_trace_from_state = build_capability_trace_from_state
        self._successful_execution_attempts = successful_execution_attempts

    @staticmethod
    def is_duplicate_tool_call(
        state: Any,
        tool_name: str,
        args_json: str,
    ) -> bool:
        """Check if this exact tool+args call was already executed."""
        call_signature = (tool_name, args_json)
        previous_revision = state.tool_call_context_revisions.get(call_signature)
        current_revision = state.metadata_context_revision if tool_name == "run_sql" else -1
        if previous_revision is None:
            state.previous_tool_calls.append(call_signature)
            state.tool_call_context_revisions[call_signature] = current_revision
            return False
        if tool_name == "run_sql" and previous_revision != current_revision:
            state.tool_call_context_revisions[call_signature] = current_revision
            return False
        if call_signature in state.previous_tool_calls:
            return True
        state.previous_tool_calls.append(call_signature)
        return True

    @staticmethod
    def should_stop_after_tool_error(
        tool_result: dict[str, Any],
        state: Any,
    ) -> bool:
        return ToolErrorPolicy.should_stop_after_tool_error(
            tool_result=tool_result,
            state=state,
        )

    @staticmethod
    def record_duplicate_tool_error(
        *,
        state: Any,
        tool_result: dict[str, Any],
    ) -> None:
        ToolErrorPolicy.record_duplicate_tool_error(
            state=state,
            tool_result=tool_result,
        )

    @staticmethod
    def duplicate_tool_result(
        *,
        tool_name: str,
        args_json: str,
        cached_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Return a protocol-level result for an already executed exact tool call."""
        if cached_result is None:
            return {
                "ok": True,
                "duplicate": True,
                "new_evidence": False,
                "previous_result_reused": False,
                "tool_name": tool_name,
                "arguments_json": args_json,
                "instruction": (
                    "The same tool arguments were already executed in this turn, "
                    "but the cached result is unavailable. Change arguments, use "
                    "another tool, or answer from existing evidence."
                ),
                "next_allowed_actions": [
                    "change_arguments",
                    "use_existing_evidence",
                    "call_different_tool",
                    "ask_clarification",
                ],
            }

        return {
            "ok": True,
            "duplicate": True,
            "new_evidence": False,
            "previous_result_reused": True,
            "tool_name": tool_name,
            "arguments_json": args_json,
            "cached_result": cached_result,
            "instruction": (
                "The same tool arguments were already executed in this turn. "
                "This response adds no new evidence. Use the cached evidence, "
                "change query/offset/scope, call a different tool, run SQL with "
                "proven sources, or ask a clarification."
            ),
            "next_allowed_actions": [
                "use_existing_evidence",
                "change_query",
                "change_offset",
                "change_scope",
                "call_different_tool",
                "run_sql_with_proven_sources",
                "ask_clarification",
            ],
        }

    def plan_tool_call(
        self,
        *,
        state: Any,
        tool_name: str,
        args_json: str,
        tool_call_id: str,
    ) -> ToolCallExecutionPlan:
        call_signature = (tool_name, args_json)
        if not self.is_duplicate_tool_call(state, tool_name, args_json):
            return ExecuteToolCallPlan(
                tool_call_id=tool_call_id,
                call_signature=call_signature,
            )

        state.add_warning("llm_tool_loop_duplicate_detected")
        tool_result = state.tool_call_results.get(call_signature)
        if tool_result is None:
            state.add_warning("llm_tool_loop_duplicate_result_missing")
            return DuplicateToolCallPlan(
                tool_call_id=tool_call_id,
                call_signature=call_signature,
                tool_result=self.duplicate_tool_result(
                    tool_name=tool_name,
                    args_json=args_json,
                    cached_result=None,
                ),
                cached_result_available=False,
            )

        state.add_warning("llm_tool_loop_duplicate_reused_result")
        self.record_duplicate_tool_error(
            state=state,
            tool_result=tool_result,
        )
        recovery_kind = "duplicate_loop" if self._successful_execution_attempts(state) else "none"
        return DuplicateToolCallPlan(
            tool_call_id=tool_call_id,
            call_signature=call_signature,
            tool_result=self.duplicate_tool_result(
                tool_name=tool_name,
                args_json=args_json,
                cached_result=tool_result,
            ),
            cached_result_available=True,
            recovery_kind=recovery_kind,
        )

    def resolve_tool_result(
        self,
        *,
        tool_result: dict[str, Any],
        state: Any,
    ) -> ToolResultDecision:
        if not self.should_stop_after_tool_error(tool_result, state):
            return ContinueToolResultDecision()

        error_code = tool_result.get("error_code")
        if error_code == "sql_preflight_failed":
            state.add_warning("llm_tool_loop_preflight_fail_stop")
        if error_code == "sql_preflight_uncertain":
            state.add_warning("llm_tool_loop_preflight_uncertain_stop")
        if error_code == "database_connector_unavailable_for_context_table":
            state.add_warning("llm_tool_loop_unavailable_context_connector_stop")
        state.add_warning("llm_tool_loop_fatal_stop")

        recovery_kind = "execution_error" if self._successful_execution_attempts(state) else "none"
        return StopToolResultDecision(
            payload=self._build_fatal_stop_payload(
                tool_result=tool_result,
                state=state,
            ),
            recovery_kind=recovery_kind,
        )

    def build_incomplete_payload(
        self,
        state: Any,
        answer: str | None = None,
    ) -> dict[str, Any]:
        latest_successful_attempt = self._latest_successful_execution_attempt(state)
        has_successful_attempts = bool(self._successful_execution_attempts(state))
        if answer is None:
            if has_successful_attempts:
                answer = "Analysis incomplete but query results are available."
            else:
                answer = "Model did not finish tool orchestration."
        return {
            "answer": answer,
            "sql": (
                latest_successful_attempt.sql
                if latest_successful_attempt is not None
                else state.generated_sql
            ),
            "selected_database_connector": (
                latest_successful_attempt.connector_name
                if latest_successful_attempt is not None
                else state.selected_database_connector
            ),
            "metadata_sources": sorted(state.metadata_sources),
            "context_tables": state.context_tables[:20],
            "runtime_context": self._runtime_context_payload_from_state(state),
            "execution": (
                latest_successful_attempt.execution.model_dump(mode="json")
                if latest_successful_attempt is not None
                else (state.execution.model_dump(mode="json") if state.execution else None)
            ),
            "visualization": None,
            "optimization_trace": state.optimization_trace.model_dump(mode="json"),
            "capability_trace": self._build_capability_trace_from_state(
                state=state,
                metadata_sources=sorted(state.metadata_sources),
                warnings=state.warnings,
                execution=(
                    latest_successful_attempt.execution
                    if latest_successful_attempt is not None
                    else state.execution
                ),
            ).model_dump(mode="json"),
            "prompt_version": state.prompt_version,
            "warnings": state.warnings,
        }

    def _build_fatal_stop_payload(
        self,
        *,
        tool_result: dict[str, Any],
        state: Any,
    ) -> dict[str, Any]:
        latest_successful_attempt = self._latest_successful_execution_attempt(state)
        return {
            "answer": ToolErrorPolicy.tool_result_user_message(tool_result),
            "sql": (
                latest_successful_attempt.sql
                if latest_successful_attempt is not None
                else state.generated_sql
            ),
            "target": None,
            "selected_database_connector": (
                latest_successful_attempt.connector_name
                if latest_successful_attempt is not None
                else state.selected_database_connector
            ),
            "metadata_sources": sorted(state.metadata_sources),
            "context_tables": state.context_tables[:20],
            "runtime_context": self._runtime_context_payload_from_state(state),
            "execution": (
                latest_successful_attempt.execution.model_dump(mode="json")
                if latest_successful_attempt is not None
                else (state.execution.model_dump(mode="json") if state.execution else None)
            ),
            "visualization": None,
            "optimization_trace": state.optimization_trace.model_dump(mode="json"),
            "capability_trace": self._build_capability_trace_from_state(
                state=state,
                metadata_sources=sorted(state.metadata_sources),
                warnings=state.warnings,
                execution=(
                    latest_successful_attempt.execution
                    if latest_successful_attempt is not None
                    else state.execution
                ),
            ).model_dump(mode="json"),
            "prompt_version": state.prompt_version,
            "warnings": state.warnings,
        }
