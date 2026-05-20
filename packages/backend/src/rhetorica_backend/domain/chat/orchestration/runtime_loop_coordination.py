"""Shared runtime loop coordination for sync and streaming orchestration shells."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, cast

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.chat.orchestration.analysis_artifacts import (
    AnalysisCheckpointLifecycle,
    AnalysisRuntimeProgressService,
    AnalysisWorkerResult,
    AnalysisWorkerTask,
    LlmAnalysisPlanner,
)
from rhetorica_backend.domain.chat.orchestration.analysis_worker_runtime import (
    AnalysisWorkerRuntime,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    DuplicateToolCallPlan,
    ExecuteToolCallPlan,
    NonToolTurnCompletionInput,
    StopToolResultDecision,
    ToolBatchDecision,
    ToolCallRequest,
    ToolExecutorPort,
    TurnCompletion,
)
from rhetorica_backend.domain.chat.orchestration.prompt_artifact_serializer import (
    PromptArtifactSerializer,
    PromptArtifactType,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.tool_result_prompt_serializer import (
    ToolResultPromptSerializer,
)
from rhetorica_backend.domain.chat.orchestration.turn_engine import (
    TurnEngine,
    TurnEngineControl,
    TurnIterationContext,
    TurnResponseContext,
)

TState = TypeVar("TState", bound=ToolOrchestrationState)
TResult = TypeVar("TResult")


@dataclass(slots=True)
class ToolBatchExecutionHooks:
    """Transport hooks around concrete tool execution."""

    on_tool_call_start: Callable[[ToolCallRequest], None] | None = None
    on_tool_call_end: Callable[[ToolCallRequest, dict[str, Any]], None] | None = None
    on_runtime_state_update: Callable[[ToolOrchestrationState], None] | None = None


@dataclass(slots=True)
class RuntimeLoopPreparedContent:
    """Transport-specific content normalization for a single assistant turn."""

    raw_content: str
    completion_raw_content: str
    finalizable_content: str


@dataclass(slots=True)
class RuntimeLoopInput(Generic[TState]):
    """Focused runtime loop snapshot passed into the shared coordinator."""

    llm_client: LlmClient
    request: ChatAskRequest
    db_connectors: list[ConnectorItem]
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    state: TState
    tool_executor: ToolExecutorPort
    protocol_repair_enabled: bool = False


@dataclass(slots=True)
class RuntimeLoopTransportAdapter(Generic[TState, TResult]):
    """Transport-specific adaptation layer for the shared coordination flow."""

    before_iteration: Callable[[TurnIterationContext], TurnEngineControl[TResult] | None]
    prepare_response_content: Callable[[str], RuntimeLoopPreparedContent]
    on_llm_failure: Callable[[Exception, TState], None]
    on_completion: Callable[[TurnCompletion, RuntimeLoopPreparedContent | None], None]
    adapt_payload: Callable[[dict[str, Any]], TResult]
    before_tool_batch: Callable[[], TResult | None] | None = None
    tool_batch_hooks: ToolBatchExecutionHooks = field(default_factory=ToolBatchExecutionHooks)
    on_runtime_state_update: Callable[[ToolOrchestrationState], None] | None = None


class RuntimeLoopCoordinationService(Generic[TState, TResult]):
    """Owns the shared turn-engine callback wiring for runtime shells."""

    def __init__(
        self,
        *,
        logger: logging.Logger,
        emit_diagnostic_event: Callable[..., None],
        ensure_dynamic_tools: Callable[..., list[dict[str, Any]]],
        assistant_message_for_history: Callable[[dict[str, Any]], dict[str, Any]],
        extract_message_content: Callable[[dict[str, Any]], str],
        extract_tool_name: Callable[[dict[str, Any]], str | None],
        extract_tool_arguments: Callable[[dict[str, Any]], dict[str, Any]],
        tool_names: Callable[[list[dict[str, Any]]], list[str]],
        decision_policy: Any,
        completion_policy: Any,
        tool_execution_policy: Any,
        direct_plain_text_finalizer: Callable[..., dict[str, Any]],
        llm_request_failed_payload: Callable[[TState], dict[str, Any]],
        attempt_duplicate_loop_recovery_finalization: Callable[..., dict[str, Any] | None],
        attempt_execution_recovery_finalization: Callable[..., dict[str, Any] | None],
        successful_execution_attempts: Callable[[TState], list[Any]],
    ) -> None:
        self._logger = logger
        self._emit_diagnostic_event = emit_diagnostic_event
        self._ensure_dynamic_tools = ensure_dynamic_tools
        self._assistant_message_for_history = assistant_message_for_history
        self._extract_message_content = extract_message_content
        self._extract_tool_name = extract_tool_name
        self._extract_tool_arguments = extract_tool_arguments
        self._tool_names = tool_names
        self._decision_policy = decision_policy
        self._completion_policy = completion_policy
        self._tool_execution_policy = tool_execution_policy
        self._direct_plain_text_finalizer = direct_plain_text_finalizer
        self._llm_request_failed_payload = llm_request_failed_payload
        self._attempt_duplicate_loop_recovery_finalization = (
            attempt_duplicate_loop_recovery_finalization
        )
        self._attempt_execution_recovery_finalization = attempt_execution_recovery_finalization
        self._successful_execution_attempts = successful_execution_attempts
        self._tool_result_prompt_serializer = ToolResultPromptSerializer()
        self._runtime_artifact_serializer = PromptArtifactSerializer()
        self._analysis_progress = AnalysisRuntimeProgressService()
        self._analysis_planner = LlmAnalysisPlanner()

    def run(
        self,
        *,
        runtime: RuntimeLoopInput[TState],
        transport: RuntimeLoopTransportAdapter[TState, TResult],
    ) -> TResult:
        self._initialize_analysis_runtime(runtime=runtime, transport=transport)
        self._append_runtime_artifacts(messages=runtime.messages, state=runtime.state)

        def on_llm_failure(
            exc: Exception,
            _context: TurnIterationContext,
        ) -> TResult:
            self._logger.exception("LLM request failed")
            runtime.state.add_warning("llm_request_failed")
            transport.on_llm_failure(exc, runtime.state)
            return transport.adapt_payload(self._llm_request_failed_payload(runtime.state))

        def on_tools_disabled(
            _context: TurnIterationContext,
            *,
            tools: list[dict[str, Any]],
            response_schema: dict[str, Any] | None,
        ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
            runtime.state.add_warning("llm_tools_not_supported")
            return [], response_schema

        def on_structured_disabled(
            _context: TurnIterationContext,
            *,
            tools: list[dict[str, Any]],
            response_schema: dict[str, Any] | None,
        ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
            runtime.state.add_warning("llm_structured_output_not_supported")
            return tools, None

        def on_response(context: TurnResponseContext) -> TurnEngineControl[TResult]:
            message = context.response.message
            prepared_content = transport.prepare_response_content(
                self._extract_message_content(message)
            )

            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                self._log_tool_calls_detected(
                    iteration=context.iteration,
                    tool_calls=tool_calls,
                )
                if transport.before_tool_batch is not None:
                    pre_batch_payload = transport.before_tool_batch()
                    if pre_batch_payload is not None:
                        return TurnEngineControl(done=True, payload=pre_batch_payload)
                tool_batch = self.tool_batch_decision_from_message(message=message)
                if tool_batch is None:
                    return TurnEngineControl()
                terminal_payload = self.execute_tool_batch_decision(
                    messages=runtime.messages,
                    tool_batch=tool_batch,
                    llm_client=runtime.llm_client,
                    response_schema=context.response_schema,
                    tool_executor=runtime.tool_executor,
                    request=runtime.request,
                    db_connectors=runtime.db_connectors,
                    state=runtime.state,
                    hooks=transport.tool_batch_hooks,
                )
                if terminal_payload is not None:
                    return TurnEngineControl(
                        done=True,
                        payload=transport.adapt_payload(terminal_payload),
                    )
                return TurnEngineControl()

            self._log_no_tool_calls(
                iteration=context.iteration,
                raw_content=prepared_content.raw_content,
                visible_content=prepared_content.finalizable_content,
                state=runtime.state,
            )
            decision = self._decision_policy.resolve_non_tool_response(
                llm_client=runtime.llm_client,
                messages=runtime.messages,
                message=message,
                raw_content=prepared_content.raw_content,
                current_tools=context.tools,
                finalizable_content=prepared_content.finalizable_content,
                protocol_repair_enabled=runtime.protocol_repair_enabled,
                request=runtime.request,
                db_connectors=runtime.db_connectors,
                state=runtime.state,
            )
            if isinstance(decision, ToolBatchDecision):
                if transport.before_tool_batch is not None:
                    pre_batch_payload = transport.before_tool_batch()
                    if pre_batch_payload is not None:
                        return TurnEngineControl(done=True, payload=pre_batch_payload)
                terminal_payload = self.execute_tool_batch_decision(
                    messages=runtime.messages,
                    tool_batch=decision,
                    llm_client=runtime.llm_client,
                    response_schema=context.response_schema,
                    tool_executor=runtime.tool_executor,
                    request=runtime.request,
                    db_connectors=runtime.db_connectors,
                    state=runtime.state,
                    hooks=transport.tool_batch_hooks,
                )
                if terminal_payload is not None:
                    return TurnEngineControl(
                        done=True,
                        payload=transport.adapt_payload(terminal_payload),
                    )
                return TurnEngineControl()

            completion = self._completion_policy.complete_non_tool_turn(
                completion_input=NonToolTurnCompletionInput(
                    raw_content=prepared_content.completion_raw_content,
                    finalizable_content=prepared_content.finalizable_content,
                    has_current_tools=bool(context.tools),
                    current_tool_names=self._tool_names(context.tools),
                ),
                decision=decision,
                llm_client=runtime.llm_client,
                request=runtime.request,
                response_schema=context.response_schema,
                state=runtime.state,
                direct_plain_text_finalizer=self._direct_plain_text_finalizer,
            )
            transport.on_completion(completion, prepared_content)
            return TurnEngineControl(
                done=True,
                payload=transport.adapt_payload(completion.payload),
            )

        def on_limit_reached(_context: TurnIterationContext) -> TResult:
            completion = self._completion_policy.complete_loop_limit(state=runtime.state)
            transport.on_completion(completion, None)
            return transport.adapt_payload(completion.payload)

        return TurnEngine[TResult](
            llm_client=runtime.llm_client,
            request=runtime.request,
            db_connectors=runtime.db_connectors,
            messages=runtime.messages,
            tools=runtime.tools,
            state=runtime.state,
            ensure_dynamic_tools=self._ensure_dynamic_tools,
            before_iteration=transport.before_iteration,
            on_response=on_response,
            on_llm_failure=on_llm_failure,
            on_limit_reached=on_limit_reached,
            on_tools_disabled=on_tools_disabled,
            on_structured_disabled=on_structured_disabled,
        ).run()

    def tool_batch_decision_from_message(
        self,
        *,
        message: dict[str, Any],
    ) -> ToolBatchDecision | None:
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            return None
        tool_requests: list[ToolCallRequest] = []
        for tool_call in tool_calls:
            tool_name = self._extract_tool_name(tool_call) or "unknown_tool"
            tool_requests.append(
                ToolCallRequest.model_validate({
                    "tool_name": tool_name,
                    "arguments": self._extract_tool_arguments(tool_call),
                    "tool_call_id": tool_call.get("id") or "call_missing_id",
                })
            )
        return ToolBatchDecision(
            tool_requests=tool_requests,
            assistant_history_message=self._assistant_message_for_history(message),
        )

    def execute_tool_batch_decision(
        self,
        *,
        messages: list[dict[str, Any]],
        tool_batch: ToolBatchDecision,
        llm_client: LlmClient | None,
        response_schema: dict[str, Any] | None,
        tool_executor: ToolExecutorPort,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        state: TState,
        hooks: ToolBatchExecutionHooks | None = None,
    ) -> dict[str, Any] | None:
        hooks = hooks or ToolBatchExecutionHooks()
        if tool_batch.assistant_history_message is not None:
            messages.append(tool_batch.assistant_history_message)

        if self._should_execute_as_isolated_workers(
            tool_batch=tool_batch,
            state=state,
        ):
            return self._execute_isolated_worker_batch(
                messages=messages,
                tool_batch=tool_batch,
                llm_client=llm_client,
                response_schema=response_schema,
                tool_executor=tool_executor,
                request=request,
                db_connectors=db_connectors,
                state=state,
                hooks=hooks,
            )

        for tool_request in tool_batch.tool_requests:
            args_json = json.dumps(
                tool_request.arguments,
                sort_keys=True,
                ensure_ascii=False,
            )
            tool_call_id = tool_request.tool_call_id or f"textual_call_{state.tool_call_count + 1}"
            execution_plan = self._tool_execution_policy.plan_tool_call(
                state=state,
                tool_name=tool_request.tool_name,
                args_json=args_json,
                tool_call_id=tool_call_id,
            )

            if isinstance(execution_plan, DuplicateToolCallPlan):
                tool_result = execution_plan.tool_result
                if execution_plan.recovery_kind == "duplicate_loop":
                    recovered_payload = self._attempt_duplicate_loop_recovery_finalization(
                        llm_client=llm_client,
                        messages=messages,
                        tool_result=tool_result,
                        state=state,
                        response_schema=response_schema,
                    )
                    if recovered_payload is not None:
                        return recovered_payload
            elif isinstance(execution_plan, ExecuteToolCallPlan):
                if hooks.on_tool_call_start is not None:
                    hooks.on_tool_call_start(tool_request)
                state.tool_call_count += 1
                self._analysis_progress.start_next_step(
                    state=state,
                    tool_name=tool_request.tool_name,
                )
                self._notify_runtime_state_update(state=state, hooks=hooks)
                tool_result = tool_executor.execute_tool(
                    tool_name=tool_request.tool_name,
                    arguments=tool_request.arguments,
                    request=request,
                    db_connectors=db_connectors,
                    state=state,
                )
                self._analysis_progress.complete_current_step(
                    state=state,
                    tool_name=tool_request.tool_name,
                    tool_result=tool_result,
                )
                self._notify_runtime_state_update(state=state, hooks=hooks)
                state.tool_call_results[execution_plan.call_signature] = tool_result
                if hooks.on_tool_call_end is not None:
                    hooks.on_tool_call_end(tool_request, tool_result)
            else:
                raise TypeError

            prompt_artifact = self._tool_result_prompt_serializer.serialize_envelope(
                tool_name=tool_request.tool_name,
                tool_result=tool_result,
            )
            self._log_prompt_artifact_diagnostics(prompt_artifact)
            for warning in prompt_artifact.warnings:
                state.add_warning(warning)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_request.tool_name,
                "content": prompt_artifact.stable_json(),
            })

            result_decision = self._tool_execution_policy.resolve_tool_result(
                tool_result=tool_result,
                state=state,
            )
            if isinstance(result_decision, StopToolResultDecision):
                if (
                    result_decision.recovery_kind == "execution_error"
                    and llm_client is not None
                    and response_schema is not None
                ):
                    recovered_payload = self._attempt_execution_recovery_finalization(
                        llm_client=llm_client,
                        messages=messages,
                        tool_result=tool_result,
                        state=state,
                        response_schema=response_schema,
                    )
                    if recovered_payload is not None:
                        return recovered_payload
                return result_decision.payload
        return None

    def _should_execute_as_isolated_workers(
        self,
        *,
        tool_batch: ToolBatchDecision,
        state: TState,
    ) -> bool:
        if getattr(state, "analysis_plan", None) is None:
            return False
        if len(tool_batch.tool_requests) < 2:
            return False
        seen_signatures: set[tuple[str, str]] = set()
        for request in tool_batch.tool_requests:
            if request.tool_name != "run_sql":
                return False
            args_json = json.dumps(
                request.arguments,
                sort_keys=True,
                ensure_ascii=False,
            )
            signature = (request.tool_name, args_json)
            if signature in seen_signatures:
                return False
            seen_signatures.add(signature)
            previous_revision = state.tool_call_context_revisions.get(signature)
            current_revision = state.metadata_context_revision
            if previous_revision is not None and previous_revision == current_revision:
                return False
        return True

    def _execute_isolated_worker_batch(
        self,
        *,
        messages: list[dict[str, Any]],
        tool_batch: ToolBatchDecision,
        llm_client: LlmClient | None,
        response_schema: dict[str, Any] | None,
        tool_executor: ToolExecutorPort,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        state: TState,
        hooks: ToolBatchExecutionHooks,
    ) -> dict[str, Any] | None:
        plan = state.analysis_plan
        if plan is None:
            return None
        runtime = AnalysisWorkerRuntime(tool_executor=tool_executor)
        worker_tasks: list[
            tuple[ToolCallRequest, ExecuteToolCallPlan, AnalysisWorkerTask, str, dict[str, Any]]
        ] = []
        for index, tool_request in enumerate(tool_batch.tool_requests):
            args_json = json.dumps(
                tool_request.arguments,
                sort_keys=True,
                ensure_ascii=False,
            )
            tool_call_id = tool_request.tool_call_id or f"textual_call_{state.tool_call_count + 1}"
            execution_plan = self._tool_execution_policy.plan_tool_call(
                state=state,
                tool_name=tool_request.tool_name,
                args_json=args_json,
                tool_call_id=tool_call_id,
            )
            if not isinstance(execution_plan, ExecuteToolCallPlan):
                raise TypeError("isolated worker batch only accepts executable tool calls")
            if hooks.on_tool_call_start is not None:
                hooks.on_tool_call_start(tool_request)
            state.tool_call_count += 1
            worker_tasks.append(
                (
                    tool_request,
                    execution_plan,
                    AnalysisWorkerTask(
                        task_id=f"{plan.plan_id}-worker-{index + 1}",
                        plan_id=plan.plan_id,
                        step_id=f"worker-{index + 1}",
                        goal=f"Execute independent SQL branch {index + 1}",
                        allowed_tools=["run_sql"],
                        allowed_tables=list(state.context_tables),
                    ),
                    tool_request.tool_name,
                    tool_request.arguments,
                )
            )
        results = runtime.execute_independent_tool_tasks(
            tasks=[
                (task, tool_name, arguments)
                for _, _, task, tool_name, arguments in worker_tasks
            ],
            request=request,
            db_connectors=db_connectors,
            parent_state=state,
        )
        for (tool_request, execution_plan, _, _, _), result in zip(
            worker_tasks,
            results,
            strict=False,
        ):
            AnalysisWorkerRuntime.merge_result(parent_state=state, result=result)
            tool_result = self._tool_result_from_worker_result(result)
            state.tool_call_results[execution_plan.call_signature] = tool_result
            prompt_artifact = runtime.prompt_artifact(result=result)
            self._log_prompt_artifact_diagnostics(prompt_artifact)
            for warning in prompt_artifact.warnings:
                state.add_warning(warning)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_request.tool_call_id
                or f"worker_call_{state.tool_call_count}",
                "name": tool_request.tool_name,
                "content": prompt_artifact.stable_json(),
            })
            if result.status == "succeeded":
                execution_payload = None
                if isinstance(result.execution_outcome, dict):
                    execution_payload = result.execution_outcome.get("execution")
                self._analysis_progress.complete_current_step(
                    state=state,
                    tool_name="run_sql",
                    tool_result={
                        "ok": True,
                        "execution": execution_payload
                        if isinstance(execution_payload, dict)
                        else None,
                        "execution_id": result.evidence_ids[0]
                        if result.evidence_ids
                        else None,
                    },
                )
            else:
                self._analysis_progress.complete_current_step(
                    state=state,
                    tool_name="run_sql",
                    tool_result={
                        "ok": False,
                        "error": result.blocker or "analysis_worker_failed",
                    },
                )
            self._notify_runtime_state_update(state=state, hooks=hooks)
            if hooks.on_tool_call_end is not None:
                hooks.on_tool_call_end(
                    tool_request,
                    result.compact_payload(),
                )
            result_decision = self._tool_execution_policy.resolve_tool_result(
                tool_result=tool_result,
                state=state,
            )
            if isinstance(result_decision, StopToolResultDecision):
                if (
                    result_decision.recovery_kind == "execution_error"
                    and llm_client is not None
                    and response_schema is not None
                ):
                    recovered_payload = self._attempt_execution_recovery_finalization(
                        llm_client=llm_client,
                        messages=messages,
                        tool_result=tool_result,
                        state=state,
                        response_schema=response_schema,
                    )
                    if recovered_payload is not None:
                        return recovered_payload
                return result_decision.payload
        return None

    @staticmethod
    def _tool_result_from_worker_result(result: AnalysisWorkerResult) -> dict[str, Any]:
        execution_payload = None
        if isinstance(result.execution_outcome, dict):
            candidate = result.execution_outcome.get("execution")
            if isinstance(candidate, dict):
                execution_payload = candidate
        return {
            "ok": result.status == "succeeded",
            "worker_result": result.compact_payload(),
            "execution": execution_payload,
            "execution_id": result.evidence_ids[0] if result.evidence_ids else None,
            "error": result.blocker,
            "can_continue": True,
        }

    def _initialize_analysis_runtime(
        self,
        *,
        runtime: RuntimeLoopInput[TState],
        transport: RuntimeLoopTransportAdapter[TState, TResult],
    ) -> None:
        if runtime.state.analysis_plan is None:
            result = self._analysis_planner.plan(
                llm_client=runtime.llm_client,
                question=runtime.request.question,
                available_tools=self._tool_names(runtime.tools),
                context_tables=runtime.state.context_tables,
                metadata_sources=runtime.state.metadata_sources,
            )
            for warning in result.warnings:
                runtime.state.add_warning(warning)
            if result.plan is not None:
                runtime.state.analysis_plan = result.plan
                runtime.state.checkpoint = AnalysisCheckpointLifecycle().start(
                    run_id=f"run-{result.plan.plan_id.removeprefix('plan-')}",
                    plan_id=result.plan.plan_id,
                )
                self._persist_runtime_state(runtime.state)
        if runtime.state.analysis_plan is not None and transport.on_runtime_state_update is not None:
            transport.on_runtime_state_update(runtime.state)

    @staticmethod
    def _notify_runtime_state_update(
        *,
        state: TState,
        hooks: ToolBatchExecutionHooks,
    ) -> None:
        if hooks.on_runtime_state_update is not None:
            hooks.on_runtime_state_update(state)

    @staticmethod
    def _persist_runtime_state(state: ToolOrchestrationState) -> None:
        callback = getattr(state, "checkpoint_persist", None)
        if callable(callback):
            callback(state)

    def _append_runtime_artifacts(
        self,
        *,
        messages: list[dict[str, Any]],
        state: TState,
    ) -> None:
        for artifact_type, artifact in state.runtime_artifacts_for_prompt():
            envelope = self._runtime_artifact_serializer.serialize(
                artifact_type=cast(PromptArtifactType, artifact_type),
                artifact=artifact,
            )
            self._log_prompt_artifact_diagnostics(envelope)
            for warning in envelope.warnings:
                state.add_warning(warning)
            messages.append({
                "role": "assistant",
                "content": envelope.stable_json(),
            })

    def _log_prompt_artifact_diagnostics(self, envelope: Any) -> None:
        if not self._should_log_llm_content():
            return
        diagnostics = self._tool_result_prompt_serializer.diagnostics(
            envelope=envelope,
        )
        self._logger.info(
            "llm.prompt_artifact.compacted",
            extra=diagnostics.model_dump(mode="json"),
        )
        self._emit_diagnostic_event(
            "llm.prompt_artifact.compacted",
            **diagnostics.model_dump(mode="json"),
        )

    @staticmethod
    def _should_log_llm_content() -> bool:
        raw = os.getenv("LOG_LLM_CONTENT")
        return raw is not None and raw.strip().lower() in {"1", "true", "yes", "on"}

    def _log_tool_calls_detected(
        self,
        *,
        iteration: int,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        tool_names = [
            self._extract_tool_name(tool_call) or "unknown_tool" for tool_call in tool_calls
        ]
        self._logger.info(
            "llm.turn.tool_calls_detected",
            extra={
                "iteration": iteration + 1,
                "tool_names": tool_names,
            },
        )
        self._emit_diagnostic_event(
            "llm.turn.tool_calls_detected",
            iteration=iteration + 1,
            tool_names=tool_names,
        )

    def _log_no_tool_calls(
        self,
        *,
        iteration: int,
        raw_content: str,
        visible_content: str,
        state: TState,
    ) -> None:
        successful_execution_count = len(self._successful_execution_attempts(state))
        self._logger.info(
            "llm.turn.no_tool_calls",
            extra={
                "iteration": iteration + 1,
                "content_chars": len(raw_content),
                "visible_chars": len(visible_content),
                "tool_call_count": state.tool_call_count,
                "successful_execution_count": successful_execution_count,
            },
        )
        self._emit_diagnostic_event(
            "llm.turn.no_tool_calls",
            iteration=iteration + 1,
            content_chars=len(raw_content),
            visible_chars=len(visible_content),
            tool_call_count=state.tool_call_count,
            successful_execution_count=successful_execution_count,
        )
