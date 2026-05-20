"""Shared protocol-repair and turn-decision policy for chat orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.chat.orchestration.models import (
    AbortDecision,
    ClarificationDecision,
    FinalAnswerDecision,
    OrchestrationDecision,
    ToolBatchDecision,
    ToolCallRequest,
)
from rhetorica_backend.domain.chat.orchestration.protocol_repair import (
    parse_protocol_repair_response,
    protocol_repair_json_schema,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.textual_tool_calls import (
    extract_textual_tool_call,
)
from rhetorica_backend.infra.logging import sanitize_for_logging


class TurnDecisionPolicy:
    """Decides next orchestration action after a non-tool assistant turn."""

    def __init__(
        self,
        *,
        logger: logging.Logger,
        ensure_dynamic_tools: Callable[..., list[dict[str, Any]]],
        assistant_message_for_history: Callable[[dict[str, Any]], dict[str, Any]],
        synthetic_assistant_tool_message: Callable[..., dict[str, Any]],
        protocol_repair_message: Callable[[list[str]], dict[str, str]],
        forced_tool_repair_message: Callable[[], dict[str, str]],
        extract_message_content: Callable[[dict[str, Any]], str],
        extract_tool_name: Callable[[dict[str, Any]], str | None],
        extract_tool_arguments: Callable[[dict[str, Any]], dict[str, Any]],
        emit_diagnostic_event: Callable[..., None],
        decorate_final_answer: Callable[..., str],
    ) -> None:
        self._logger = logger
        self._ensure_dynamic_tools = ensure_dynamic_tools
        self._assistant_message_for_history = assistant_message_for_history
        self._synthetic_assistant_tool_message = synthetic_assistant_tool_message
        self._protocol_repair_message = protocol_repair_message
        self._forced_tool_repair_message = forced_tool_repair_message
        self._extract_message_content = extract_message_content
        self._extract_tool_name = extract_tool_name
        self._extract_tool_arguments = extract_tool_arguments
        self._emit_diagnostic_event = emit_diagnostic_event
        self._decorate_final_answer = decorate_final_answer

    def resolve_non_tool_response(
        self,
        *,
        llm_client: LlmClient,
        messages: list[dict[str, Any]],
        message: dict[str, Any],
        raw_content: str,
        current_tools: list[dict[str, Any]],
        finalizable_content: str,
        protocol_repair_enabled: bool,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> OrchestrationDecision | None:
        textual_tool_call, content_for_history = extract_textual_tool_call(raw_content)
        if textual_tool_call is not None:
            state.add_warning("llm_textual_tool_request_detected")
            tool_call_id = f"textual_call_{state.tool_call_count + 1}"
            return ToolBatchDecision(
                tool_requests=[
                    ToolCallRequest.model_validate(
                        {
                            "tool_name": textual_tool_call.tool_name,
                            "arguments": textual_tool_call.arguments,
                            "tool_call_id": tool_call_id,
                        }
                    )
                ],
                assistant_history_message=self._synthetic_assistant_tool_message(
                    content_for_history=content_for_history,
                    tool_name=textual_tool_call.tool_name,
                    arguments=textual_tool_call.arguments,
                    tool_call_id=tool_call_id,
                ),
            )

        current_tool_names = self._tool_names(current_tools)
        policy_decisions = set(state.capability_state.policy_decisions)
        if not current_tools and finalizable_content and state.tool_call_count == 0:
            if self._looks_like_structured_final_payload(finalizable_content):
                return None
            return FinalAnswerDecision(
                raw_final_text=finalizable_content,
                finalization_mode="direct",
            )
        if (
            finalizable_content
            and state.tool_call_count == 0
            and "non_analytic_turn_tools_suppressed" in policy_decisions
        ):
            if self._looks_like_structured_final_payload(finalizable_content):
                return None
            return FinalAnswerDecision(
                raw_final_text=finalizable_content,
                finalization_mode="direct",
            )

        if (
            current_tools
            and finalizable_content
            and ("run_sql" in current_tool_names or state.tool_call_count > 0)
        ):
            return FinalAnswerDecision(raw_final_text=finalizable_content)

        if (
            current_tools
            and protocol_repair_enabled
            and ("run_sql" in current_tool_names or state.tool_call_count > 0)
        ):
            state.add_warning("llm_protocol_repair_requested")
            return self._attempt_protocol_repair(
                llm_client=llm_client,
                messages=messages,
                message=message,
                visible_content=finalizable_content,
                tools=current_tools,
                request=request,
                db_connectors=db_connectors,
                state=state,
            )

        return None

    @staticmethod
    def _looks_like_structured_final_payload(content: str) -> bool:
        normalized = content.lstrip()
        if not normalized.startswith("{"):
            return False
        lowered = normalized.casefold()
        return any(
            marker in lowered
            for marker in (
                '"answer_markdown"',
                '"schema_version"',
                '"claim_evidence_links"',
                '"uncertainty_flags"',
            )
        )

    def _attempt_protocol_repair(
        self,
        *,
        llm_client: LlmClient,
        messages: list[dict[str, Any]],
        message: dict[str, Any],
        visible_content: str,
        tools: list[dict[str, Any]],
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> OrchestrationDecision:
        tools = self._ensure_dynamic_tools(
            tools=tools,
            request=request,
            db_connectors=db_connectors,
            state=state,
        )
        tool_names = self._tool_names(tools)
        if not tool_names:
            state.add_warning("llm_protocol_repair_failed")
            return AbortDecision(reason="protocol_repair_no_tools")

        self._logger.info(
            "llm.protocol_repair.requested",
            extra={
                "attempt": state.protocol_repair_attempts + 1,
                "tool_names": tool_names,
                "visible_content": sanitize_for_logging(visible_content),
            },
        )
        self._emit_diagnostic_event(
            "llm.protocol_repair.requested",
            attempt=state.protocol_repair_attempts + 1,
            tool_names=tool_names,
            visible_content=sanitize_for_logging(visible_content),
        )

        repair_messages = [
            *messages,
            self._assistant_message_for_history(message),
            self._protocol_repair_message(tool_names),
        ]
        try:
            repair_response, tools_disabled, structured_disabled = (
                llm_client.chat_completion_with_fallback(
                    messages=repair_messages,
                    tools=[],
                    response_schema=protocol_repair_json_schema(tool_names),
                )
            )
            if tools_disabled:
                state.add_warning("llm_tools_not_supported")
            if structured_disabled:
                state.add_warning("llm_structured_output_not_supported")
        except Exception:
            self._logger.exception("LLM protocol repair call failed")
            state.add_warning("llm_protocol_repair_failed")
            return AbortDecision(reason="protocol_repair_call_failed")

        repair_content = self._extract_message_content(repair_response.message)
        decision = parse_protocol_repair_response(
            repair_content,
            tool_names=set(tool_names),
        )
        if decision is None:
            self._logger.warning(
                "llm.protocol_repair.parse_failed",
                extra={"repair_content": sanitize_for_logging(repair_content)},
            )
            return self._attempt_required_tool_repair(
                llm_client=llm_client,
                messages=messages,
                message=message,
                visible_content=visible_content,
                tools=tools,
                state=state,
            )

        self._logger.info(
            "llm.protocol_repair.decided",
            extra={
                "next_action": decision.next_action,
                "tool_name": decision.tool_name,
                "arguments": sanitize_for_logging(decision.arguments),
                "uncertainty_flags": decision.uncertainty_flags,
            },
        )
        self._emit_diagnostic_event(
            "llm.protocol_repair.decided",
            next_action=decision.next_action,
            tool_name=decision.tool_name,
            arguments=sanitize_for_logging(decision.arguments),
            uncertainty_flags=decision.uncertainty_flags,
        )
        state.protocol_repair_attempts += 1

        if decision.requests_tool_call:
            state.add_warning("llm_protocol_repair_tool_request")
            tool_call_id = f"textual_call_{state.tool_call_count + 1}"
            return ToolBatchDecision(
                tool_requests=[
                    ToolCallRequest.model_validate(
                        {
                            "tool_name": decision.tool_name,
                            "arguments": decision.arguments,
                            "tool_call_id": tool_call_id,
                        }
                    )
                ],
                assistant_history_message=self._synthetic_assistant_tool_message(
                    content_for_history=visible_content,
                    tool_name=decision.tool_name,
                    arguments=decision.arguments,
                    tool_call_id=tool_call_id,
                ),
            )

        uncertainty_flags = list(decision.uncertainty_flags)
        answer = self._decorate_final_answer(
            answer=decision.answer_markdown or visible_content,
            state=state,
        )
        self._logger.info(
            "llm.protocol_repair.finalization_decided",
            extra={
                "next_action": decision.next_action,
                "answer_chars": len(answer),
            },
        )
        self._emit_diagnostic_event(
            "llm.protocol_repair.finalization_decided",
            next_action=decision.next_action,
            answer_chars=len(answer),
        )
        if decision.next_action == "needs_user_clarification":
            if "needs_user_clarification" not in uncertainty_flags:
                uncertainty_flags.append("needs_user_clarification")
            return ClarificationDecision(
                answer_markdown=answer,
                uncertainty_flags=uncertainty_flags,
            )
        return FinalAnswerDecision(
            raw_final_text=answer,
            uncertainty_flags=uncertainty_flags,
        )

    def _attempt_required_tool_repair(
        self,
        *,
        llm_client: LlmClient,
        messages: list[dict[str, Any]],
        message: dict[str, Any],
        visible_content: str,
        tools: list[dict[str, Any]],
        state: ToolOrchestrationState,
    ) -> OrchestrationDecision:
        try:
            repair_response, tools_disabled, structured_disabled = (
                llm_client.chat_completion_with_fallback(
                    messages=[
                        *messages,
                        self._assistant_message_for_history(message),
                        self._forced_tool_repair_message(),
                    ],
                    tools=tools,
                    tool_choice="required",
                )
            )
            if tools_disabled:
                state.add_warning("llm_tools_not_supported")
            if structured_disabled:
                state.add_warning("llm_structured_output_not_supported")
        except Exception:
            self._logger.exception("LLM forced tool repair call failed")
            state.add_warning("llm_protocol_repair_failed")
            return AbortDecision(reason="forced_tool_repair_call_failed")

        tool_calls = repair_response.message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            state.add_warning("llm_protocol_repair_forced_tool_choice")
            tool_requests: list[ToolCallRequest] = []
            for tool_call in tool_calls:
                tool_name = self._extract_tool_name(tool_call) or "unknown_tool"
                arguments = self._extract_tool_arguments(tool_call)
                tool_requests.append(
                    ToolCallRequest.model_validate(
                        {
                            "tool_name": tool_name,
                            "arguments": arguments,
                            "tool_call_id": tool_call.get("id") or "call_missing_id",
                        }
                    )
                )
            return ToolBatchDecision(
                tool_requests=tool_requests,
                assistant_history_message=self._assistant_message_for_history(
                    repair_response.message
                ),
            )

        content = self._extract_message_content(repair_response.message)
        textual_tool_call, _ = extract_textual_tool_call(content)
        if textual_tool_call is None:
            state.add_warning("llm_protocol_repair_failed")
            return AbortDecision(reason="forced_tool_repair_missing_tool_call")

        state.add_warning("llm_protocol_repair_forced_tool_choice")
        tool_call_id = f"textual_call_{state.tool_call_count + 1}"
        return ToolBatchDecision(
            tool_requests=[
                ToolCallRequest.model_validate(
                    {
                        "tool_name": textual_tool_call.tool_name,
                        "arguments": textual_tool_call.arguments,
                        "tool_call_id": tool_call_id,
                    }
                )
            ],
            assistant_history_message=self._synthetic_assistant_tool_message(
                content_for_history=visible_content,
                tool_name=textual_tool_call.tool_name,
                arguments=textual_tool_call.arguments,
                tool_call_id=tool_call_id,
            ),
        )

    @staticmethod
    def _tool_names(tools: list[dict[str, Any]]) -> list[str]:
        names: list[str] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if isinstance(name, str) and name:
                names.append(name)
        return names
