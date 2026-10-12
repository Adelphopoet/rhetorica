"""OpenAI Agents SDK runtime adapter for Rhetorica chat turns."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import replace
from typing import Any, Generic, Literal, TypeVar, cast
from urllib.parse import urlsplit, urlunsplit

import httpx2
from openai import AsyncOpenAI, BadRequestError, NotFoundError, UnprocessableEntityError
from openai.types.responses import (
    ResponseReasoningItem,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseReasoningTextDeltaEvent,
    ResponseTextDeltaEvent,
)
from openai.types.shared import Reasoning
from rhetorica_backend.domain.chat.error_contract import ErrorCategory, classify_exception
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.chat.llm.prompt_builder import PromptBuilder
from rhetorica_backend.domain.chat.orchestration.agent_runtime import (
    AgentIterationContext,
    AgentRuntimeInput,
    AgentRuntimeRunItemEvent,
    AgentRuntimeTransport,
)
from rhetorica_backend.domain.chat.orchestration.final_response_contract import (
    SUBMIT_FINAL_ANSWER_TOOL_NAME,
    ChatFinalResponseContractV1,
    final_response_json_schema,
    final_response_tool_schema,
)
from rhetorica_backend.domain.chat.orchestration.loop_limit_awareness import (
    loop_limit_notice_content,
    should_force_finalization,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    ExecuteToolCallPlan,
    RejectRepeatedToolCallPlan,
    SuccessfulTurnCompletion,
    ToolCallRequest,
)
from rhetorica_backend.domain.chat.orchestration.sdk_model_observer import (
    SdkModelCallObserver,
    SdkModelObservationHooks,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.tool_result_prompt_serializer import (
    ToolResultPromptSerializer,
)
from rhetorica_backend.domain.chat.settings import get_chat_settings, get_max_iterations
from rhetorica_backend.infra.langfuse import (
    langfuse_agent_turn,
    langfuse_capture_content,
    langfuse_enabled,
)

from agents import (
    Agent,
    AgentOutputSchema,
    FunctionTool,
    FunctionToolResult,
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelSettings,
    RunConfig,
    Runner,
    ToolsToFinalOutputResult,
    UserError,
)
from agents.items import ModelResponse, ToolCallItem, ToolCallOutputItem
from agents.models.interface import Model
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from agents.models.reasoning_content_replay import ReasoningContentReplayContext
from agents.run_config import CallModelData, ModelInputData
from agents.tool_context import ToolContext

TState = TypeVar("TState", bound=ToolOrchestrationState)
TResult = TypeVar("TResult")

_TOOL_ARTIFACT_TOOL_NAMES = frozenset({
    "inspect_tool_artifact",
    "read_tool_artifact",
    "find_tool_artifact_text",
    "list_recent_tool_artifacts",
    "remember_tool_artifact",
    "read_tool_evidence",
    "forget_tool_evidence",
})


def build_agent_run_config() -> RunConfig:
    """Emit SDK callbacks only when the exclusive OpenInference processor is ready."""
    return RunConfig(
        workflow_name="Rhetorica chat turn",
        tracing_disabled=not langfuse_enabled(),
        trace_include_sensitive_data=langfuse_capture_content(),
        tool_not_found_behavior="return_error_to_model",
    )


def _replay_provider_reasoning_content(_context: ReasoningContentReplayContext) -> bool:
    """Preserve explicit provider thinking context across SDK-managed tool turns."""
    return True


class AgentsSdkCapabilityError(RuntimeError):
    """Configured connector cannot satisfy the required Agents SDK contract."""


class _TransportStop(Exception):
    def __init__(self, payload: Any) -> None:
        super().__init__("transport requested runtime stop")
        self.payload = payload


class _SdkCompactionRestart(Exception):
    """Restart the public SDK run from a compacted, self-contained checkpoint."""

    def __init__(self, checkpoint: dict[str, Any]) -> None:
        super().__init__("restart Agents SDK run from compacted checkpoint")
        self.checkpoint = checkpoint


class _SdkTurnControl:
    """Exact SDK model-call counter shared by hooks, tools and input filtering."""

    def __init__(self, *, max_turns: int) -> None:
        self.max_turns = max_turns
        self.completed_turns = 0

    @property
    def current_iteration(self) -> int:
        return self.completed_turns

    @property
    def tools_enabled(self) -> bool:
        return not should_force_finalization(
            completed_turns=self.completed_turns,
            max_iterations=self.max_turns,
        )


class _CredentialAwareSdkModel(Model):
    """Retry one SDK model call with the next pinned connector credential."""

    def __init__(
        self,
        *,
        connection: Any,
        model_factory: Callable[[int], Model],
        max_attempts: int,
        observe_credential: Callable[..., None] | None = None,
    ) -> None:
        self._connection = connection
        self._model_factory = model_factory
        self._max_attempts = max(1, max_attempts)
        self._selected_index = connection.selected_api_key_index
        self._observe_credential = observe_credential

    def _candidate_indexes(self) -> list[int]:
        credentials = self._connection.api_keys or []
        if not credentials:
            return [0]
        start = self._selected_index
        if start is None or not 0 <= start < len(credentials):
            start = 0
        count = min(self._max_attempts, len(credentials))
        return [(start + offset) % len(credentials) for offset in range(count)]

    @staticmethod
    def _retryable(error: Exception) -> bool:
        category, _, _, _ = classify_exception(error)
        return category == ErrorCategory.TRANSIENT

    def _commit_success(self, selected_index: int, previous_index: int | None) -> None:
        if previous_index is not None and selected_index != previous_index:
            self._connection.commit_credential_retry(
                selected_api_key_index=selected_index,
                expected_index=previous_index,
            )
        self._selected_index = selected_index

    def _observe_attempt(
        self,
        *,
        attempt: int,
        selected_index: int,
        previous_index: int | None,
        outcome: str,
        retryable: bool | None = None,
    ) -> None:
        """Emit content-free credential lifecycle diagnostics."""
        if self._observe_credential is None:
            return
        self._observe_credential(
            attempt=attempt,
            credential_reused=selected_index == previous_index,
            credential_switched=(
                previous_index is not None and selected_index != previous_index
            ),
            outcome=outcome,
            retryable=retryable,
        )

    @staticmethod
    def _stream_event_allows_replay(event: Any) -> bool:
        """Allow replay only before the two SDK-documented lifecycle events."""
        data = getattr(event, "data", event)
        return getattr(data, "type", None) in {
            "response.created",
            "response.in_progress",
        }

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        indexes = self._candidate_indexes()
        previous_index = self._selected_index
        for position, selected_index in enumerate(indexes):
            model = self._model_factory(selected_index)
            try:
                response = await model.get_response(*args, **kwargs)
            except Exception as error:
                retryable = self._retryable(error)
                self._observe_attempt(
                    attempt=position + 1,
                    selected_index=selected_index,
                    previous_index=previous_index,
                    outcome="retryable_error" if retryable else "non_retryable_error",
                    retryable=retryable,
                )
                if position + 1 >= len(indexes) or not retryable:
                    raise
                continue
            self._commit_success(selected_index, previous_index)
            self._observe_attempt(
                attempt=position + 1,
                selected_index=selected_index,
                previous_index=previous_index,
                outcome="succeeded",
            )
            return response
        raise RuntimeError("credential retry chain produced no model response")

    async def _stream_response(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        indexes = self._candidate_indexes()
        previous_index = self._selected_index
        for position, selected_index in enumerate(indexes):
            model = self._model_factory(selected_index)
            replay_allowed = True
            try:
                async for event in model.stream_response(*args, **kwargs):
                    replay_allowed = replay_allowed and self._stream_event_allows_replay(event)
                    yield event
            except Exception as error:
                retryable = self._retryable(error)
                self._observe_attempt(
                    attempt=position + 1,
                    selected_index=selected_index,
                    previous_index=previous_index,
                    outcome=(
                        "stream_started_error"
                        if not replay_allowed
                        else "retryable_error" if retryable else "non_retryable_error"
                    ),
                    retryable=retryable,
                )
                if (
                    not replay_allowed
                    or position + 1 >= len(indexes)
                    or not retryable
                ):
                    raise
                continue
            self._commit_success(selected_index, previous_index)
            self._observe_attempt(
                attempt=position + 1,
                selected_index=selected_index,
                previous_index=previous_index,
                outcome="succeeded",
            )
            return
        raise RuntimeError("credential retry chain produced no stream")

    def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        return self._stream_response(*args, **kwargs)


class _SdkRunHooks(SdkModelObservationHooks):
    """Bridge model-turn lifecycle and usage into the existing transport contract."""

    def __init__(
        self,
        *,
        runtime: AgentRuntimeInput[Any],
        transport: AgentRuntimeTransport[Any, Any],
        active_tool_definitions: Any,
        response_schema: dict[str, Any] | None,
        turn_control: _SdkTurnControl,
        on_iteration_start: Callable[[int], None],
        observer: SdkModelCallObserver,
    ) -> None:
        super().__init__(observer, phase="tool_loop", streamed=runtime.streamed)
        self._runtime = runtime
        self._transport = transport
        self._active_tool_definitions = active_tool_definitions
        self._response_schema = response_schema
        self._turn_control = turn_control
        self._on_iteration_start = on_iteration_start

    async def on_llm_start(
        self,
        context: Any,
        agent: Any,
        system_prompt: str | None,
        input_items: list[Any],
    ) -> None:
        _ = context, agent, system_prompt, input_items
        max_iterations = get_max_iterations()
        iteration = self._turn_control.current_iteration
        control = self._transport.before_iteration(
            AgentIterationContext(
                iteration=iteration,
                max_iterations=max_iterations,
                request=self._runtime.request,
                db_connectors=self._runtime.db_connectors,
                messages=self._runtime.messages,
                tools=self._active_tool_definitions(),
                response_schema=self._response_schema,
                state=self._runtime.state,
            )
        )
        if control is not None and control.done:
            raise _TransportStop(control.payload)
        self._on_iteration_start(iteration)
        await super().on_llm_start(context, agent, system_prompt, input_items)

    async def on_llm_end(
        self,
        context: Any,
        agent: Any,
        response: ModelResponse,
    ) -> None:
        _ = context, agent
        reasoning_content = self._reasoning_content(response)
        if reasoning_content and not self._runtime.streamed:
            self._transport.prepare_response_content("", reasoning_content)
        await super().on_llm_end(context, agent, response)
        self._turn_control.completed_turns += 1

    @staticmethod
    def _reasoning_content(response: ModelResponse) -> str:
        blocks: list[str] = []
        for item in response.output:
            if not isinstance(item, ResponseReasoningItem):
                continue
            blocks.extend(part.text for part in item.summary if part.text)
            blocks.extend(part.text for part in item.content or [] if part.text)
        return "\n\n".join(blocks)


class _SdkModelInputController:
    """Expose turn budget and compact a repeated-tool loop inside SDK execution."""

    _CHECKPOINT_MARKER = "Rhetorica context checkpoint v1"

    def __init__(
        self,
        *,
        runtime: AgentRuntimeInput[Any],
        model: Model,
        model_settings: ModelSettings,
        turn_control: _SdkTurnControl,
        run_config: RunConfig,
        active_tool_definitions: Callable[[], list[dict[str, Any]]] | None = None,
        response_schema: dict[str, Any] | None = None,
        observer: SdkModelCallObserver | None = None,
    ) -> None:
        self._runtime = runtime
        self._observer = observer or SdkModelCallObserver(runtime, logging.getLogger(__name__))
        self._model = model
        self._model_settings = model_settings
        self._turn_control = turn_control
        self._run_config = run_config
        self._active_tool_definitions = active_tool_definitions or (lambda: [])
        self._response_schema = response_schema
        self._prompt_budget = LlmClient(runtime.llm_client.connection).build_prompt_budget_service()

    async def __call__(self, data: CallModelData[Any]) -> ModelInputData:
        model_input = list(data.model_data.input)
        instructions = data.model_data.instructions or ""
        if self._should_compact(
            model_input=model_input,
            instructions=instructions,
        ):
            checkpoint = await self._compact(current_view=model_input)
            if checkpoint is not None:
                raise _SdkCompactionRestart(checkpoint)

        iteration = self._turn_control.current_iteration
        budget_notice = loop_limit_notice_content(
            iteration=iteration,
            max_iterations=self._turn_control.max_turns,
        )
        if not self._turn_control.tools_enabled:
            self._runtime.state.add_warning("llm_forced_finalization_requested")
        endpoint = LlmClient._build_endpoint(
            self._runtime.llm_client.connection.base_url,
            self._runtime.llm_client.connection.endpoint_path,
        )
        configured_context_role = self._runtime.llm_client.connection.extra_settings.get(
            "runtime_context_role"
        )
        context_role: Literal["developer", "user"]
        if configured_context_role == "developer":
            context_role = "developer"
        elif configured_context_role == "user":
            context_role = "user"
        else:
            context_role = (
                "user"
                if urlsplit(endpoint).path.rstrip("/").endswith("/chat/completions")
                else "developer"
            )
        return ModelInputData(
            input=[
                *model_input,
                {
                    "role": context_role,
                    "content": (
                        "<runtime_context>\n"
                        + PromptBuilder.build_time_context()
                        + "Agent loop context:\n"
                        + f"- current_iteration={iteration + 1}\n"
                        + f"- max_iterations={self._turn_control.max_turns}\n"
                        + f"- {budget_notice}"
                        + "\nThis is runtime metadata, not a new task. Continue the preceding "
                        "request and do not quote this block.\n</runtime_context>"
                    ),
                },
            ],
            instructions=instructions,
        )

    def _should_compact(
        self,
        *,
        model_input: list[Any],
        instructions: str,
    ) -> bool:
        settings = get_chat_settings()
        if not settings.context_compaction_enabled:
            return False
        if self._runtime.state.force_context_compaction:
            return True

        latest_sequence = len(self._runtime.state.executed_tool_call_ledger)
        if self._contains_checkpoint(model_input) and (
            latest_sequence <= self._runtime.state.last_compacted_tool_call_sequence
        ):
            return False

        messages: list[dict[str, Any]] = []
        if instructions.strip():
            messages.append({"role": "system", "content": instructions})
        for item in model_input:
            normalized = self._jsonable(item)
            if isinstance(normalized, dict):
                messages.append(normalized)
            else:
                messages.append({"role": "user", "content": normalized})
        estimated_tokens = self._prompt_budget.estimate_request(
            messages=messages,
            tools=self._active_tool_definitions(),
            response_schema=self._response_schema,
        ).tokens
        trigger_tokens = int(
            self._prompt_budget.prompt_budget_tokens() * settings.context_compaction_trigger_ratio
        )
        return estimated_tokens >= trigger_tokens

    async def _compact(
        self,
        *,
        current_view: list[Any],
    ) -> dict[str, Any] | None:
        try:
            summary = await self._summarize(current_view)
        except Exception:
            self._runtime.state.add_warning("context_compaction_failed")
            return None
        if not summary.strip():
            self._runtime.state.add_warning("context_compaction_empty_summary")
            return None

        ledger = [
            entry.model_dump(mode="json") for entry in self._runtime.state.executed_tool_call_ledger
        ]
        checkpoint = {
            "role": "user",
            "content": (
                f"{self._CHECKPOINT_MARKER}\n\n"
                "Continue the current user request from this compact checkpoint. "
                "The summary preserves conversational continuity. The "
                "executed_tool_calls ledger is authoritative for calls the backend "
                "actually executed; do not claim a blocked call ran. Read an artifact "
                "when exact prior output is needed.\n\n"
                f"Summary:\n{summary.strip()}\n\n"
                "executed_tool_calls:\n" + json.dumps(ledger, ensure_ascii=False, sort_keys=True)
            ),
        }
        self._runtime.state.last_compacted_tool_call_sequence = len(ledger)
        self._runtime.state.blocked_tool_call_turn_count = 0
        self._runtime.state.force_context_compaction = False
        self._runtime.state.add_warning("context_compacted")
        return checkpoint

    @classmethod
    def _contains_checkpoint(cls, model_input: list[Any]) -> bool:
        for item in model_input:
            normalized = cls._jsonable(item)
            if not isinstance(normalized, dict):
                continue
            content = normalized.get("content")
            if isinstance(content, str) and content.startswith(cls._CHECKPOINT_MARKER):
                return True
        return False

    async def _summarize(self, model_input: list[Any]) -> str:
        serialized = json.dumps(
            [self._jsonable(item) for item in model_input],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        compactor = Agent[Any](
            name="Rhetorica context compactor",
            instructions=(
                "Create a concise continuation checkpoint for the supplied conversation "
                "prefix. Preserve the user's current goal, explicit constraints, "
                "decisions, concrete facts, exact identifiers, exact SQL when present, "
                "errors, unresolved questions and the next step. Do not call tools. Do "
                "not invent facts. Do not reproduce bulky tool outputs. Return only the "
                "checkpoint text."
            ),
            model=self._model,
            model_settings=replace(
                self._model_settings,
                tool_choice="none",
                parallel_tool_calls=False,
                context_management=None,
            ),
            tools=[],
            output_type=None,
        )
        try:
            result = await Runner.run(
                compactor,
                input=[{"role": "user", "content": serialized}],
                max_turns=1,
                hooks=SdkModelObservationHooks(
                    self._observer,
                    phase="context_compaction",
                    streamed=False,
                ),
                run_config=replace(
                    self._run_config,
                    workflow_name="Rhetorica context compaction",
                    call_model_input_filter=None,
                ),
            )
        except BaseException as error:
            self._observer.finish(error)
            raise
        if not isinstance(result.final_output, str):
            raise ModelBehaviorError("Context compactor returned non-text output")
        return result.final_output

    @staticmethod
    def _jsonable(item: Any) -> Any:
        if isinstance(item, dict):
            return item
        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode="json")
        return str(item)


class _FunctionToolRegistry:
    """Expose existing Rhetorica tool definitions as dynamically enabled SDK tools."""

    def __init__(
        self,
        *,
        coordinator: Any,
        runtime: AgentRuntimeInput[Any],
        transport: AgentRuntimeTransport[Any, Any],
        turn_control: _SdkTurnControl,
        explicit_final_answer_enabled: bool = False,
    ) -> None:
        self._coordinator = coordinator
        self._runtime = runtime
        self._transport = transport
        self._turn_control = turn_control
        self._explicit_final_answer_enabled = explicit_final_answer_enabled
        self._initial_names = set(self._tool_names(runtime.tools))
        self._tools_by_name: dict[str, FunctionTool] = {}
        self._definitions_by_name: dict[str, dict[str, Any]] = {}
        self._results_by_call_id: dict[str, dict[str, Any]] = {}
        self._tool_names_by_call_id: dict[str, str] = {}
        self._current_model_turn = 0
        self._blocked_noted_turn: int | None = None
        self._lock = asyncio.Lock()
        if explicit_final_answer_enabled:
            self._register_final_answer_tool()
        self.refresh()

    @property
    def tools(self) -> list[FunctionTool]:
        return list(self._tools_by_name.values())

    def active_definitions(self) -> list[dict[str, Any]]:
        return [
            definition
            for name, definition in self._definitions_by_name.items()
            if self._is_enabled(name)
        ]

    def begin_model_turn(self, iteration: int) -> None:
        self._current_model_turn = iteration

    def tool_result(self, call_id: str | None) -> dict[str, Any] | None:
        if call_id is None:
            return None
        return self._results_by_call_id.get(call_id)

    def tool_name(self, call_id: str | None) -> str | None:
        if call_id is None:
            return None
        return self._tool_names_by_call_id.get(call_id)

    def refresh(self, agent: Agent[Any] | None = None) -> None:
        definitions = [
            *self._runtime.tools,
            *self._runtime.state.deferred_tool_definitions.values(),
            *self._runtime.state.loaded_tool_definitions.values(),
            *self._runtime.state.loaded_mcp_tool_definitions.values(),
        ]
        for definition in definitions:
            parsed = self._parse_definition(definition)
            if parsed is None:
                continue
            name, description, parameters = parsed
            if name == SUBMIT_FINAL_ANSWER_TOOL_NAME or name in self._tools_by_name:
                continue
            self._definitions_by_name[name] = definition

            def is_enabled(
                _ctx: Any,
                _agent: Any,
                *,
                tool_name: str = name,
            ) -> bool:
                return self._is_enabled(tool_name)

            self._tools_by_name[name] = FunctionTool(
                name=name,
                description=description,
                params_json_schema=parameters,
                strict_json_schema=False,
                is_enabled=is_enabled,
                on_invoke_tool=self._invoke,
            )
        if agent is not None:
            known = {tool.name for tool in agent.tools if isinstance(tool, FunctionTool)}
            agent.tools.extend(
                tool for name, tool in self._tools_by_name.items() if name not in known
            )

    def _register_final_answer_tool(self) -> None:
        definition = final_response_tool_schema()
        parsed = self._parse_definition(definition)
        if parsed is None:
            raise AgentsSdkCapabilityError("Invalid final answer tool definition")
        name, description, parameters = parsed
        self._definitions_by_name[name] = definition
        self._tools_by_name[name] = FunctionTool(
            name=name,
            description=description,
            params_json_schema=parameters,
            strict_json_schema=True,
            is_enabled=True,
            on_invoke_tool=self._invoke_final_answer,
        )

    async def _invoke_final_answer(
        self,
        context: ToolContext[Any],
        arguments_json: str,
    ) -> str:
        call_id = context.tool_call_id or "call_missing_id"
        self._tool_names_by_call_id[call_id] = SUBMIT_FINAL_ANSWER_TOOL_NAME
        try:
            final_answer = ChatFinalResponseContractV1.model_validate_json(arguments_json)
        except Exception as exc:
            error_result = {
                "ok": False,
                "error_code": "llm_final_payload_invalid",
                "can_continue": True,
                "user_message": "submit_final_answer arguments violate the final contract.",
                "validation_error": str(exc)[:2_000],
            }
            self._results_by_call_id[call_id] = error_result
            return json.dumps(error_result, ensure_ascii=False, sort_keys=True)
        # The SDK receives the final contract; stream events receive tool status.
        self._results_by_call_id[call_id] = {"ok": True, "status": "succeeded"}
        return final_answer.model_dump_json()

    async def final_output_from_tools(
        self,
        _context: Any,
        tool_results: list[FunctionToolResult],
    ) -> ToolsToFinalOutputResult:
        for tool_result in tool_results:
            if tool_result.tool.name != SUBMIT_FINAL_ANSWER_TOOL_NAME:
                continue
            output = tool_result.output
            if not isinstance(output, str):
                return ToolsToFinalOutputResult(is_final_output=False)
            try:
                ChatFinalResponseContractV1.model_validate_json(output)
            except Exception:
                return ToolsToFinalOutputResult(is_final_output=False)
            return ToolsToFinalOutputResult(is_final_output=True, final_output=output)
        return ToolsToFinalOutputResult(is_final_output=False)

    async def _invoke(self, context: ToolContext[Any], arguments_json: str) -> str:
        try:
            arguments = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError as exc:
            call_id = context.tool_call_id or "call_missing_id"
            error_result = {
                "ok": False,
                "error_code": "tool_arguments_json_invalid",
                "can_continue": True,
                "user_message": (
                    f"Tool {context.tool_name} received invalid JSON arguments. "
                    "Retry the call with one complete JSON object."
                ),
                "validation_error": exc.msg,
            }
            self._results_by_call_id[call_id] = error_result
            self._tool_names_by_call_id[call_id] = context.tool_name
            return json.dumps(error_result, ensure_ascii=False, sort_keys=True)
        if not isinstance(arguments, dict):
            raise ModelBehaviorError(f"Tool {context.tool_name} arguments must be a JSON object")

        async with self._lock:
            request = ToolCallRequest(
                tool_name=context.tool_name,
                arguments=arguments,
                tool_call_id=context.tool_call_id,
            )
            args_json = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
            execution_plan = self._coordinator._tool_execution_policy.plan_tool_call(
                state=self._runtime.state,
                tool_name=request.tool_name,
                args_json=args_json,
                tool_call_id=request.tool_call_id or "call_missing_id",
            )

            if isinstance(execution_plan, ExecuteToolCallPlan):
                self._runtime.state.tool_call_count += 1
                tool_result = await asyncio.to_thread(
                    self._runtime.tool_executor.execute_tool,
                    tool_name=request.tool_name,
                    arguments=request.arguments,
                    request=self._runtime.request,
                    db_connectors=self._runtime.db_connectors,
                    state=self._runtime.state,
                )
                self._runtime.state.tool_call_results[execution_plan.call_signature] = tool_result
                executed = True
            elif isinstance(execution_plan, RejectRepeatedToolCallPlan):
                tool_result = execution_plan.tool_result
                executed = False
                self._note_blocked_call()
            else:
                raise TypeError(f"Unsupported tool execution plan: {type(execution_plan).__name__}")
            call_id = request.tool_call_id or "call_missing_id"
            self._results_by_call_id[call_id] = tool_result
            self._tool_names_by_call_id[call_id] = request.tool_name

            prompt_tool_result = self._coordinator._tool_result_prompt_view(
                tool_name=request.tool_name,
                arguments=request.arguments,
                tool_result=tool_result,
                state=self._runtime.state,
            )
            if executed:
                self._runtime.state.record_executed_tool_call(
                    tool_call_id=request.tool_call_id or "call_missing_id",
                    tool_name=request.tool_name,
                    arguments=request.arguments,
                    result=tool_result,
                    prompt_result=prompt_tool_result,
                )
            prompt_artifact = self._coordinator._tool_result_prompt_serializer.serialize_envelope(
                tool_name=request.tool_name,
                tool_result=prompt_tool_result,
            )
            self._coordinator._log_prompt_artifact_diagnostics(prompt_artifact)
            for warning in prompt_artifact.warnings:
                self._runtime.state.add_warning(warning)

            self.refresh(context.agent if isinstance(context.agent, Agent) else None)
            return cast(str, prompt_artifact.stable_json())

    def _note_blocked_call(self) -> None:
        if self._blocked_noted_turn == self._current_model_turn:
            return
        self._blocked_noted_turn = self._current_model_turn
        self._runtime.state.blocked_tool_call_turn_count += 1
        if self._runtime.state.blocked_tool_call_turn_count >= 2:
            self._runtime.state.force_context_compaction = True

    def _is_enabled(self, name: str) -> bool:
        if name == SUBMIT_FINAL_ANSWER_TOOL_NAME:
            return self._explicit_final_answer_enabled
        return self._turn_control.tools_enabled and (
            name in self._initial_names
            or name in self._runtime.state.loaded_tool_definitions
            or name in self._runtime.state.loaded_mcp_tool_definitions
        )

    @staticmethod
    def _parse_definition(
        definition: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]] | None:
        if not isinstance(definition, dict) or definition.get("type") != "function":
            return None
        function = definition.get("function")
        if not isinstance(function, dict):
            return None
        name = function.get("name")
        parameters = function.get("parameters")
        if not isinstance(name, str) or not name or not isinstance(parameters, dict):
            raise AgentsSdkCapabilityError("Invalid function tool definition")
        description = function.get("description")
        return name, description if isinstance(description, str) else "", parameters

    @staticmethod
    def _tool_names(tools: list[dict[str, Any]]) -> list[str]:
        names: list[str] = []
        for definition in tools:
            parsed = _FunctionToolRegistry._parse_definition(definition)
            if parsed is not None:
                names.append(parsed[0])
        return names


class AgentsSdkTurnRuntime(Generic[TState, TResult]):
    """Run a complete turn through OpenAI Agents SDK without backend semantic routing."""

    def __init__(
        self,
        *,
        logger: logging.Logger,
        emit_diagnostic_event: Callable[..., None],
        tool_execution_policy: Any,
        build_final_payload_from_parsed: Callable[..., dict[str, Any]],
        llm_request_failed_payload: Callable[[TState, str | None, Exception], dict[str, Any]],
    ) -> None:
        self._logger = logger
        self._emit_diagnostic_event = emit_diagnostic_event
        self._tool_execution_policy = tool_execution_policy
        self._build_final_payload_from_parsed = build_final_payload_from_parsed
        self._llm_request_failed_payload = llm_request_failed_payload
        self._tool_result_prompt_serializer = ToolResultPromptSerializer()

    async def run(
        self,
        *,
        runtime: AgentRuntimeInput[TState],
        transport: AgentRuntimeTransport[TState, TResult],
    ) -> TResult:
        try:
            return await self._run_sdk(runtime=runtime, transport=transport)
        except _TransportStop as exc:
            return cast(TResult, exc.payload)
        except MaxTurnsExceeded:
            runtime.state.add_warning("agent_runtime_turn_limit_reached")
            return self._failure_result(
                runtime=runtime,
                transport=transport,
                error=RuntimeError(
                    f"Agents SDK stopped after the configured {get_max_iterations()} turn limit"
                ),
            )
        except (
            AgentsSdkCapabilityError,
            BadRequestError,
            NotFoundError,
            UnprocessableEntityError,
        ) as exc:
            runtime.state.add_warning("agent_runtime_capability_mismatch")
            return self._failure_result(runtime=runtime, transport=transport, error=exc)
        except (ModelBehaviorError, UserError) as exc:
            runtime.state.add_warning("agent_runtime_model_behavior_error")
            return self._failure_result(runtime=runtime, transport=transport, error=exc)
        except Exception as exc:
            runtime.state.add_warning("agent_runtime_failed")
            return self._failure_result(runtime=runtime, transport=transport, error=exc)

    async def _run_sdk(
        self,
        *,
        runtime: AgentRuntimeInput[TState],
        transport: AgentRuntimeTransport[TState, TResult],
    ) -> TResult:
        connection = runtime.llm_client.connection
        endpoint = LlmClient._build_endpoint(connection.base_url, connection.endpoint_path)
        base_url = self._sdk_base_url(endpoint)
        timeout = get_chat_settings().llm_request_timeout
        http_client = httpx2.AsyncClient(verify=connection.verify_tls, timeout=timeout)
        observer = SdkModelCallObserver(runtime, self._logger)
        try:
            api_keys = list(connection.api_keys or [])

            def build_model(selected_index: int) -> Model:
                api_key = (
                    api_keys[selected_index]
                    if api_keys
                    else connection.api_key
                )
                openai_client = AsyncOpenAI(
                    api_key=api_key or "not-configured",
                    base_url=base_url,
                    default_headers=connection.headers,
                    timeout=timeout,
                    max_retries=0,
                    http_client=http_client,
                )
                return self._sdk_model(connection=connection, openai_client=openai_client)

            if api_keys and connection.selected_api_key_index is not None:
                retry_attempts = connection.extra_settings.get("llm_retry_attempts", 3)
                retry_attempts = (
                    int(retry_attempts)
                    if isinstance(retry_attempts, int | float)
                    else 3
                )
                model: Model = _CredentialAwareSdkModel(
                    connection=connection,
                    model_factory=build_model,
                    max_attempts=min(max(1, retry_attempts), len(api_keys)),
                    observe_credential=lambda **fields: self._emit_diagnostic_event(
                        "llm.credential.attempt",
                        **fields,
                    ),
                )
            else:
                selected_index = secrets.randbelow(len(api_keys)) if api_keys else 0
                model = build_model(selected_index)
            max_turns = get_max_iterations()
            turn_control = _SdkTurnControl(max_turns=max_turns)
            model_settings = self._model_settings(connection)
            explicit_final_answer_enabled = LlmClient._explicit_final_answer_enabled(connection)
            if explicit_final_answer_enabled:
                model_settings = replace(
                    model_settings,
                    tool_choice="required",
                    parallel_tool_calls=False,
                )
            registry = _FunctionToolRegistry(
                coordinator=self,
                runtime=runtime,
                transport=transport,
                turn_control=turn_control,
                explicit_final_answer_enabled=explicit_final_answer_enabled,
            )
            structured_output_enabled = LlmClient._structured_output_enabled(connection)
            sdk_structured_output_enabled = (
                structured_output_enabled and not explicit_final_answer_enabled
            )
            output_type = self._agent_output_type(
                structured_output_enabled=sdk_structured_output_enabled
            )
            instructions, input_items = self._instructions_and_input(runtime.messages)
            agent: Agent[Any] = Agent(
                name="Rhetorica Analytics Agent",
                instructions=instructions,
                model=model,
                model_settings=model_settings,
                tools=cast(Any, registry.tools),
                output_type=output_type,
                tool_use_behavior=(
                    registry.final_output_from_tools
                    if explicit_final_answer_enabled
                    else "run_llm_again"
                ),
                reset_tool_choice=not explicit_final_answer_enabled,
            )
            run_config = build_agent_run_config()
            response_schema = (
                final_response_json_schema()
                if structured_output_enabled or explicit_final_answer_enabled
                else None
            )
            hooks = _SdkRunHooks(
                runtime=runtime,
                transport=transport,
                active_tool_definitions=registry.active_definitions,
                response_schema=response_schema,
                turn_control=turn_control,
                on_iteration_start=registry.begin_model_turn,
                observer=observer,
            )
            runner_input: list[dict[str, Any]] = input_items
            result: Any = None
            langfuse_trace_id: str | None = None
            with langfuse_agent_turn(runtime.request, streamed=runtime.streamed) as langfuse_turn:
                while result is None:
                    remaining_turns = max_turns - turn_control.completed_turns
                    if remaining_turns <= 0:
                        raise MaxTurnsExceeded(  # noqa: TRY301 - outer handler records diagnostics then re-raises
                            f"Max turns ({max_turns}) exceeded before SDK run restart"
                        )
                    input_controller = _SdkModelInputController(
                        runtime=runtime,
                        model=model,
                        model_settings=model_settings,
                        turn_control=turn_control,
                        run_config=run_config,
                        active_tool_definitions=registry.active_definitions,
                        response_schema=response_schema,
                        observer=observer,
                    )
                    run_config.call_model_input_filter = input_controller
                    try:
                        if runtime.streamed:
                            streamed_kwargs: dict[str, Any] = {
                                "input": cast(Any, runner_input),
                                "max_turns": remaining_turns,
                                "hooks": hooks,
                                "run_config": run_config,
                            }
                            if runtime.agent_session is not None:
                                streamed_kwargs["session"] = runtime.agent_session
                            streamed_result = Runner.run_streamed(agent, **streamed_kwargs)
                            cancelled = False

                            async def cancel_on_signal(active_result: Any = streamed_result) -> None:
                                nonlocal cancelled
                                if runtime.cancel_event is None:
                                    return
                                await runtime.cancel_event.wait()
                                if active_result.is_complete:
                                    return
                                cancelled = True
                                active_result.cancel(mode="immediate")

                            cancel_task = asyncio.create_task(cancel_on_signal())
                            try:
                                async for event in streamed_result.stream_events():
                                    observer.observe(event)
                                    self._consume_stream_event(
                                        event=event,
                                        transport=transport,
                                        registry=registry,
                                        emit_text=(
                                            not sdk_structured_output_enabled
                                            and not explicit_final_answer_enabled
                                        ),
                                    )
                            finally:
                                cancel_task.cancel()
                                with suppress(asyncio.CancelledError):
                                    await cancel_task
                            if cancelled:
                                observer.finish(asyncio.CancelledError())
                                if transport.on_cancelled is None:
                                    raise AgentsSdkCapabilityError(
                                        "Streaming runtime was cancelled without a transport result"
                                    )
                                return transport.on_cancelled()
                            result = streamed_result
                        else:
                            run_kwargs: dict[str, Any] = {
                                "input": cast(Any, runner_input),
                                "max_turns": remaining_turns,
                                "hooks": hooks,
                                "run_config": run_config,
                            }
                            if runtime.agent_session is not None:
                                run_kwargs["session"] = runtime.agent_session
                            result = await Runner.run(agent, **run_kwargs)
                    except _SdkCompactionRestart as restart:
                        if runtime.agent_session is None:
                            runner_input = [restart.checkpoint]
                        else:
                            replace_items = getattr(runtime.agent_session, "replace_items", None)
                            if not callable(replace_items):
                                raise AgentsSdkCapabilityError(
                                    "Persistent agent session cannot atomically store a compaction checkpoint"
                                ) from restart
                            await replace_items([restart.checkpoint])
                            runner_input = []
                        result = None
                final_output = self._final_output_contract(
                    result.final_output,
                    structured_output_enabled=sdk_structured_output_enabled,
                    explicit_final_answer_enabled=explicit_final_answer_enabled,
                )
                langfuse_turn.record_final_output(final_output.answer_markdown)
                langfuse_trace_id = langfuse_turn.trace_id
            prepared = transport.prepare_response_content(final_output.answer_markdown, "")
            payload = self._build_final_payload_from_parsed(
                    parsed_payload=final_output.to_payload(),
                    fallback_answer=final_output.answer_markdown,
                    state=runtime.state,
                )
            if langfuse_trace_id:
                runtime_context = payload.get("runtime_context")
                if isinstance(runtime_context, dict):
                    runtime_context["langfuse_trace_id"] = langfuse_trace_id
                else:
                    payload["runtime_context"] = {"langfuse_trace_id": langfuse_trace_id}
            runtime_context = payload.setdefault("runtime_context", {})
            if isinstance(runtime_context, dict):
                ledger = runtime.state.executed_tool_call_ledger
                runtime_context["review_tool_call_count"] = len(ledger)
                runtime_context["review_tool_error_count"] = sum(
                    1 for entry in ledger if not entry.outcome.ok
                )
            completion = SuccessfulTurnCompletion(payload=payload)
            if "llm_forced_finalization_requested" in runtime.state.warnings:
                runtime.state.add_warning("llm_forced_finalization_completed")
            transport.on_completion(completion, prepared)
            return transport.adapt_payload(completion.payload)
        except BaseException as error:
            observer.finish(error)
            raise
        finally:
            observer.finish()
            await http_client.aclose()

    @classmethod
    def _consume_stream_event(
        cls,
        *,
        event: Any,
        transport: AgentRuntimeTransport[Any, Any],
        registry: _FunctionToolRegistry,
        emit_text: bool,
    ) -> None:
        if event.type == "raw_response_event":
            data = event.data
            if (
                emit_text
                and isinstance(data, ResponseTextDeltaEvent)
                and transport.on_text_delta is not None
            ):
                transport.on_text_delta(data.delta)
            elif (
                isinstance(
                    data,
                    ResponseReasoningTextDeltaEvent | ResponseReasoningSummaryTextDeltaEvent,
                )
                and transport.on_reasoning_delta is not None
            ):
                transport.on_reasoning_delta(data.delta)
            return
        if event.type != "run_item_stream_event" or transport.on_run_item is None:
            return
        item = event.item
        if isinstance(item, ToolCallItem):
            transport.on_run_item(
                AgentRuntimeRunItemEvent(
                    kind="tool_called",
                    tool_name=item.tool_name,
                    tool_call_id=item.call_id,
                    arguments=cls._tool_call_arguments(item.raw_item),
                )
            )
        elif isinstance(item, ToolCallOutputItem):
            transport.on_run_item(
                AgentRuntimeRunItemEvent(
                    kind="tool_output",
                    tool_name=registry.tool_name(item.call_id),
                    tool_call_id=item.call_id,
                    tool_result=registry.tool_result(item.call_id),
                )
            )

    @staticmethod
    def _tool_call_arguments(raw_item: Any) -> dict[str, Any] | None:
        if isinstance(raw_item, dict):
            raw_arguments = raw_item.get("arguments")
        else:
            raw_arguments = getattr(raw_item, "arguments", None)
        if isinstance(raw_arguments, dict):
            return raw_arguments
        if not isinstance(raw_arguments, str):
            return None
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _failure_result(
        self,
        *,
        runtime: AgentRuntimeInput[TState],
        transport: AgentRuntimeTransport[TState, TResult],
        error: Exception,
    ) -> TResult:
        self._logger.error(
            "Agents SDK runtime failed: %s: %s",
            type(error).__name__,
            str(error),
        )
        transport.on_llm_failure(error, runtime.state)
        return transport.adapt_payload(
            self._llm_request_failed_payload(
                runtime.state,
                runtime.request.llm_connector_name,
                error,
            )
        )

    @staticmethod
    def _tool_result_prompt_view(
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tool_result: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        """Register exact tool results as artifacts before prompt compaction."""
        if tool_name in _TOOL_ARTIFACT_TOOL_NAMES or "tool_artifact" in tool_result:
            return tool_result
        prompt_result = dict(tool_result)
        artifact_result = tool_result
        if tool_result.get("duplicate") is True:
            cached_result = tool_result.get("cached_result")
            if isinstance(cached_result, dict):
                artifact_result = cached_result
                prompt_result = dict(cached_result)
                prompt_result.update({
                    "duplicate": True,
                    "new_evidence": False,
                    "previous_result_reused": True,
                })

        prompt_result["tool_artifact"] = state.tool_result_artifacts.register(
            tool_name=tool_name,
            arguments=arguments,
            result=artifact_result,
        )
        return prompt_result

    def _log_prompt_artifact_diagnostics(self, envelope: Any) -> None:
        raw = os.getenv("LOG_LLM_CONTENT")
        if raw is None or raw.strip().lower() not in {"1", "true", "yes", "on"}:
            return
        diagnostics = self._tool_result_prompt_serializer.diagnostics(envelope=envelope)
        fields = diagnostics.model_dump(mode="json")
        self._logger.info("llm.prompt_artifact.compacted", extra=fields)
        self._emit_diagnostic_event("llm.prompt_artifact.compacted", **fields)

    @staticmethod
    def _sdk_base_url(endpoint: str) -> str:
        parsed = urlsplit(endpoint)
        normalized_path = parsed.path.rstrip("/")
        suffix = next(
            (
                candidate
                for candidate in ("/chat/completions", "/responses")
                if normalized_path.endswith(candidate)
            ),
            None,
        )
        if suffix is None:
            raise AgentsSdkCapabilityError(f"Unsupported Agents SDK endpoint: {endpoint}")
        base_path = normalized_path[: -len(suffix)]
        return urlunsplit((parsed.scheme, parsed.netloc, base_path, "", ""))

    @staticmethod
    def _sdk_model(*, connection: Any, openai_client: AsyncOpenAI) -> Model:
        endpoint = LlmClient._build_endpoint(connection.base_url, connection.endpoint_path)
        normalized_path = urlsplit(endpoint).path.rstrip("/")
        if normalized_path.endswith("/responses"):
            return OpenAIResponsesModel(
                model=connection.model,
                openai_client=openai_client,
                model_is_explicit=True,
            )
        if normalized_path.endswith("/chat/completions"):
            thinking_enabled = connection.extra_settings.get("chat_template_enable_thinking")
            return OpenAIChatCompletionsModel(
                model=connection.model,
                openai_client=openai_client,
                should_replay_reasoning_content=(
                    _replay_provider_reasoning_content if thinking_enabled is True else None
                ),
            )
        raise AgentsSdkCapabilityError(f"Unsupported Agents SDK endpoint: {endpoint}")

    @staticmethod
    def _agent_output_type(*, structured_output_enabled: bool) -> AgentOutputSchema | None:
        if not structured_output_enabled:
            return None
        return AgentOutputSchema(
            ChatFinalResponseContractV1,
            strict_json_schema=False,
        )

    @staticmethod
    def _final_output_contract(
        final_output: Any,
        *,
        structured_output_enabled: bool,
        explicit_final_answer_enabled: bool = False,
    ) -> ChatFinalResponseContractV1:
        if explicit_final_answer_enabled:
            if not isinstance(final_output, str):
                raise ModelBehaviorError("Explicit final answer tool returned a non-text contract")
            try:
                return ChatFinalResponseContractV1.model_validate_json(final_output)
            except Exception as exc:
                raise ModelBehaviorError(
                    "Connector did not finish through the explicit final answer tool"
                ) from exc
        if not structured_output_enabled:
            if not isinstance(final_output, str):
                raise ModelBehaviorError("Plain-text connector returned a non-text final output")
            return ChatFinalResponseContractV1(answer_markdown=final_output)
        if isinstance(final_output, ChatFinalResponseContractV1):
            return final_output
        return ChatFinalResponseContractV1.model_validate(final_output)

    @staticmethod
    def _instructions_and_input(
        messages: list[dict[str, Any]],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        instructions: list[str] = []
        input_items: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if not isinstance(content, str):
                continue
            if role in {"system", "developer"}:
                instructions.append(content)
            elif role in {"user", "assistant"}:
                input_items.append({"role": role, "content": content})
        if not input_items:
            raise AgentsSdkCapabilityError("Agent turn has no user or assistant input")
        return ("\n\n".join(instructions) if instructions else None), input_items

    @staticmethod
    def _model_settings(connection: Any) -> ModelSettings:
        extra_body: dict[str, Any] = {}
        extra_args: dict[str, Any] = {}
        if LlmClient._reasoning_split_enabled(connection):
            extra_body["reasoning_split"] = True
        enable_thinking = connection.extra_settings.get("chat_template_enable_thinking")
        if isinstance(enable_thinking, bool):
            extra_body["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
        endpoint = LlmClient._build_endpoint(connection.base_url, connection.endpoint_path)
        responses_endpoint = urlsplit(endpoint).path.rstrip("/").endswith("/responses")
        use_max_completion_tokens = (
            not responses_endpoint
            and urlsplit(endpoint).path.rstrip("/").endswith("/chat/completions")
            and connection.max_tokens_parameter == "max_completion_tokens"
        )
        if use_max_completion_tokens and connection.max_tokens is not None:
            extra_args["max_completion_tokens"] = connection.max_tokens
        reasoning = (
            Reasoning(
                effort=connection.reasoning_effort,
                summary="auto" if responses_endpoint and enable_thinking else None,
            )
            if connection.reasoning_effort is not None or (responses_endpoint and enable_thinking)
            else None
        )
        return ModelSettings(
            temperature=connection.temperature,
            top_p=connection.top_p,
            max_tokens=None if use_max_completion_tokens else connection.max_tokens,
            truncation="auto",
            reasoning=reasoning,
            context_management=None,
            include_usage=True,
            preserve_raw_usage=True,
            timeout=get_chat_settings().llm_request_timeout,
            extra_body=extra_body or None,
            extra_args=extra_args or None,
        )
