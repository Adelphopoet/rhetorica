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
from typing import Any, Generic, TypeVar, cast

import httpx2
from openai import AsyncOpenAI, BadRequestError, NotFoundError, UnprocessableEntityError
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseReasoningItem,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseReasoningTextDeltaEvent,
    ResponseTextDeltaEvent,
)
from rhetorica_backend.domain.chat.error_contract import ErrorCategory, classify_exception
from rhetorica_backend.domain.chat.llm import sdk_models
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.chat.orchestration.agent_runtime import (
    AgentIterationContext,
    AgentRuntimeInput,
    AgentRuntimeRunItemEvent,
    AgentRuntimeTransport,
)
from rhetorica_backend.domain.chat.orchestration.final_response_contract import (
    ChatFinalResponseContractV1,
    final_response_json_schema,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    SuccessfulTurnCompletion,
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
    MaxTurnsExceeded,
    ModelBehaviorError,
    RunConfig,
    Runner,
    ToolExecutionConfig,
    UserError,
)
from agents.items import ModelResponse, ToolCallItem, ToolCallOutputItem
from agents.models.interface import Model

from .provider_capabilities import ProviderRuntimeCapabilities
from .sdk_input_controller import _SdkModelInputController
from .sdk_runtime_contracts import (
    AgentsSdkCapabilityError,
    _SdkCompactionRestart,
    _SdkTurnControl,
    _TransportStop,
)
from .sdk_tool_registry import _FunctionToolRegistry

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
            credential_switched=(previous_index is not None and selected_index != previous_index),
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
                        else "retryable_error"
                        if retryable
                        else "non_retryable_error"
                    ),
                    retryable=retryable,
                )
                if not replay_allowed or position + 1 >= len(indexes) or not retryable:
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
        if self._call is not None:
            self._call.diagnostics.context_manifest = self._turn_control.latest_manifest

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
        manifest = self._turn_control.latest_manifest
        if manifest and response.usage.input_tokens:
            estimated = manifest["estimated_input_tokens"]
            actual_ratio = (
                response.usage.input_tokens / max(1, estimated) * self._turn_control.estimate_ratio
            )
            # Calibrate conservatively; do not lower a safety bound within a turn.
            self._turn_control.estimate_ratio = max(
                self._turn_control.estimate_ratio, actual_ratio * 1.1
            )
            manifest["actual_input_tokens"] = response.usage.input_tokens
            manifest["calibration_ratio"] = self._turn_control.estimate_ratio
        available = {
            definition["function"]["name"] for definition in self._active_tool_definitions()
        }
        self._turn_control.pending_tool_calls = sum(
            isinstance(item, ResponseFunctionToolCall) and item.name in available
            for item in response.output
        )
        self._turn_control.completed_turns += 1

    async def on_tool_end(self, context: Any, agent: Any, tool: Any, result: Any) -> None:
        self._turn_control.pending_tool_calls = max(0, self._turn_control.pending_tool_calls - 1)

    @staticmethod
    def _reasoning_content(response: ModelResponse) -> str:
        blocks: list[str] = []
        for item in response.output:
            if not isinstance(item, ResponseReasoningItem):
                continue
            blocks.extend(part.text for part in item.summary if part.text)
            blocks.extend(part.text for part in item.content or [] if part.text)
        return "\n\n".join(blocks)


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
        base_url = sdk_models.sdk_base_url(endpoint)
        timeout = get_chat_settings().llm_request_timeout
        http_client = httpx2.AsyncClient(verify=connection.verify_tls, timeout=timeout)
        observer = SdkModelCallObserver(runtime, self._logger)
        try:
            api_keys = list(connection.api_keys or [])

            def build_model(selected_index: int) -> Model:
                api_key = api_keys[selected_index] if api_keys else connection.api_key
                openai_client = AsyncOpenAI(
                    api_key=api_key or "not-configured",
                    base_url=base_url,
                    default_headers=connection.headers,
                    timeout=timeout,
                    max_retries=0,
                    http_client=http_client,
                )
                return sdk_models.build_sdk_model(
                    connection=connection, openai_client=openai_client
                )

            if api_keys and connection.selected_api_key_index is not None:

                def observe_credential(**fields: Any) -> None:
                    observer.credential_attempt(**fields)
                    self._emit_diagnostic_event("llm.credential.attempt", **fields)

                retry_attempts = connection.extra_settings.get("llm_retry_attempts", 3)
                retry_attempts = (
                    int(retry_attempts) if isinstance(retry_attempts, int | float) else 3
                )
                model: Model = _CredentialAwareSdkModel(
                    connection=connection,
                    model_factory=build_model,
                    max_attempts=min(max(1, retry_attempts), len(api_keys)),
                    observe_credential=observe_credential,
                )
            else:
                selected_index = secrets.randbelow(len(api_keys)) if api_keys else 0
                model = build_model(selected_index)
            max_turns = get_max_iterations()
            turn_control = _SdkTurnControl(max_turns=max_turns)
            turn_control.tool_mode = (
                "dispatch" if get_chat_settings().tool_dispatch_enabled else "direct"
            )
            if runtime.agent_session is not None:
                read_metadata = getattr(runtime.agent_session, "get_context_metadata", None)
                if callable(read_metadata):
                    metadata = await read_metadata()
                    turn_control.projection.events = metadata.get("events", [])
                    turn_control.tool_mode = metadata.get(
                        "tool_mode",
                        "dispatch" if get_chat_settings().tool_dispatch_enabled else "direct",
                    )
                    await runtime.agent_session.set_context_metadata({
                        **metadata,
                        "tool_mode": turn_control.tool_mode,
                    })
            model_settings = sdk_models.build_model_settings(connection)
            explicit_final_answer_enabled = LlmClient._explicit_final_answer_enabled(connection)
            if explicit_final_answer_enabled:
                model_settings = replace(
                    model_settings,
                    tool_choice="required",
                    parallel_tool_calls=False,
                )
            capabilities = ProviderRuntimeCapabilities.from_settings(connection.extra_settings)
            if (
                get_chat_settings().certified_tool_concurrency_enabled
                and capabilities.parallel_tool_calls
            ):
                model_settings = replace(model_settings, parallel_tool_calls=True)
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
            if turn_control.tool_mode == "dispatch":
                from ..llm.prompt_contract import DISPATCH_CONTRACT

                instructions = DISPATCH_CONTRACT + "\n" + (instructions or "")
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
            run_config.tool_execution = ToolExecutionConfig(max_function_tool_concurrency=4)
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
                        # Explicit finalization already carries the schema in its tool.
                        response_schema=response_schema if sdk_structured_output_enabled else None,
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

                            async def cancel_on_signal(
                                active_result: Any = streamed_result,
                            ) -> None:
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
                            runner_input = restart.items
                        else:
                            replace_items = getattr(runtime.agent_session, "replace_items", None)
                            if not callable(replace_items):
                                raise AgentsSdkCapabilityError(
                                    "Persistent agent session cannot atomically store a compaction checkpoint"
                                ) from restart
                            if get_chat_settings().context_harness_enabled and hasattr(
                                runtime.agent_session, "set_context_metadata"
                            ):
                                await replace_items(
                                    restart.items,
                                    context_metadata={
                                        "version": 2,
                                        "events": [],
                                        "tool_mode": turn_control.tool_mode,
                                    },
                                )
                            else:
                                await replace_items(restart.items)
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
