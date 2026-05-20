from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.analysis_artifacts import (
    AnalysisCheckpoint,
    AnalysisPlan,
    AnalysisPlanStep,
    AnalysisWorkerResult,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    ContinueToolResultDecision,
    DuplicateToolCallPlan,
    ExecuteToolCallPlan,
    LoopLimitTurnCompletion,
    NonToolTurnCompletionInput,
    StopToolResultDecision,
    SuccessfulTurnCompletion,
)
from rhetorica_backend.domain.chat.orchestration.runtime_loop_coordination import (
    RuntimeLoopCoordinationService,
    RuntimeLoopInput,
    RuntimeLoopPreparedContent,
    RuntimeLoopTransportAdapter,
    ToolBatchExecutionHooks,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


@dataclass
class _FakeCompletionResponse:
    message: dict[str, Any]


class _SequencedLlmClient:
    def __init__(
        self,
        responses: list[tuple[dict[str, Any], bool, bool]],
        *,
        error: Exception | None = None,
        planner_message: dict[str, Any] | None = None,
    ) -> None:
        self._responses = responses
        self._error = error
        self._planner_message = planner_message
        self.calls: list[dict[str, Any]] = []

    def chat_completion_with_fallback(self, **kwargs: Any):
        self.calls.append(kwargs)
        schema = kwargs.get("response_schema")
        if isinstance(schema, dict) and schema.get("required") == [
            "should_plan",
            "plan",
            "warnings",
        ]:
            return (
                _FakeCompletionResponse(
                    message=self._planner_message
                    or {
                        "content": json.dumps({
                            "should_plan": False,
                            "plan": None,
                            "warnings": [],
                        })
                    }
                ),
                False,
                False,
            )
        if self._error is not None:
            raise self._error
        if not self._responses:
            raise AssertionError
        message, tools_disabled, structured_disabled = self._responses.pop(0)
        return (
            _FakeCompletionResponse(message=message),
            tools_disabled,
            structured_disabled,
        )

    def collect_usages(self):
        """Fake collect_usages for test compatibility."""
        return []


class _FakeDecisionPolicy:
    def __init__(self, decision: Any = None) -> None:
        self.decision = decision
        self.calls: list[dict[str, Any]] = []

    def resolve_non_tool_response(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.decision


class _FakeCompletionPolicy:
    def __init__(self, completion: Any) -> None:
        self._completion = completion
        self.calls: list[dict[str, Any]] = []

    def complete_non_tool_turn(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._completion

    def complete_loop_limit(self, *, state: ToolOrchestrationState) -> LoopLimitTurnCompletion:
        return LoopLimitTurnCompletion(
            payload={"answer": "limit", "warnings": list(state.warnings)}
        )


class _FakeToolExecutionPolicy:
    def __init__(
        self,
        *,
        tool_result_decision: Any,
        execution_plan: Any | None = None,
    ) -> None:
        self._tool_result_decision = tool_result_decision
        self._execution_plan = execution_plan

    def plan_tool_call(self, **kwargs: Any) -> Any:
        if self._execution_plan is not None:
            return self._execution_plan
        return ExecuteToolCallPlan(
            tool_call_id=kwargs["tool_call_id"],
            call_signature=(kwargs["tool_name"], kwargs["args_json"]),
        )

    def resolve_tool_result(self, **_: Any) -> Any:
        return self._tool_result_decision


class _DuplicateAwareToolExecutionPolicy:
    def __init__(self) -> None:
        self.plans: list[tuple[str, str]] = []
        self.resolved: list[dict[str, Any]] = []

    def plan_tool_call(self, **kwargs: Any) -> Any:
        call_signature = (kwargs["tool_name"], kwargs["args_json"])
        if call_signature in self.plans:
            return DuplicateToolCallPlan(
                tool_call_id=kwargs["tool_call_id"],
                call_signature=call_signature,
                tool_result={"ok": True, "cached": True},
                cached_result_available=True,
            )
        self.plans.append(call_signature)
        return ExecuteToolCallPlan(
            tool_call_id=kwargs["tool_call_id"],
            call_signature=call_signature,
        )

    def resolve_tool_result(self, *, tool_result: dict[str, Any], **_: Any) -> Any:
        self.resolved.append(tool_result)
        return ContinueToolResultDecision()


class _FakeToolExecutor:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def execute_tool(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.result


def _service(
    *,
    decision_policy: Any,
    completion_policy: Any,
    tool_execution_policy: Any,
    llm_request_failed_payload=None,
) -> RuntimeLoopCoordinationService[ToolOrchestrationState, dict[str, Any]]:
    return RuntimeLoopCoordinationService(
        logger=__import__("logging").getLogger(__name__),
        emit_diagnostic_event=lambda *_args, **_kwargs: None,
        ensure_dynamic_tools=lambda **kwargs: kwargs["tools"],
        assistant_message_for_history=lambda message: {
            "role": "assistant",
            "content": message.get("content"),
        },
        extract_message_content=lambda message: str(message.get("content") or ""),
        extract_tool_name=lambda tool_call: (
            tool_call.get("function", {}).get("name")
            if isinstance(tool_call.get("function"), dict)
            else None
        ),
        extract_tool_arguments=lambda tool_call: json.loads(
            tool_call.get("function", {}).get("arguments") or "{}"
        ),
        tool_names=lambda tools: [
            function["name"]
            for tool in tools
            if isinstance(tool, dict)
            and isinstance(function := tool.get("function"), dict)
            and isinstance(function.get("name"), str)
        ],
        decision_policy=decision_policy,
        completion_policy=completion_policy,
        tool_execution_policy=tool_execution_policy,
        direct_plain_text_finalizer=lambda **_: {"answer": "direct"},
        llm_request_failed_payload=llm_request_failed_payload
        or (lambda state: {"answer": "failed", "warnings": list(state.warnings)}),
        attempt_duplicate_loop_recovery_finalization=lambda **_: None,
        attempt_execution_recovery_finalization=lambda **_: None,
        successful_execution_attempts=lambda _state: [],
    )


def _transport(
    *,
    completions: list[tuple[str, str | None]],
    failures: list[str],
    tool_events: list[tuple[str, str]],
    runtime_events: list[dict[str, Any]] | None = None,
) -> RuntimeLoopTransportAdapter[ToolOrchestrationState, dict[str, Any]]:
    runtime_events = runtime_events if runtime_events is not None else []
    return RuntimeLoopTransportAdapter(
        before_iteration=lambda _context: None,
        prepare_response_content=lambda raw_content: RuntimeLoopPreparedContent(
            raw_content=raw_content,
            completion_raw_content=raw_content,
            finalizable_content=raw_content.strip(),
        ),
        on_llm_failure=lambda exc, _state: failures.append(type(exc).__name__),
        on_completion=lambda completion, prepared: completions.append((
            completion.completion_type,
            None if prepared is None else prepared.finalizable_content,
        )),
        adapt_payload=lambda payload: payload,
        on_runtime_state_update=lambda state: runtime_events.append({
            "analysis_plan": (
                state.analysis_plan.model_dump(mode="json")
                if state.analysis_plan is not None
                else None
            ),
            "checkpoint": (
                state.checkpoint.digest_payload()
                if state.checkpoint is not None
                else None
            ),
        }),
        tool_batch_hooks=ToolBatchExecutionHooks(
            on_tool_call_start=lambda tool_request: tool_events.append((
                "start",
                tool_request.tool_call_id or "missing",
            )),
            on_tool_call_end=lambda tool_request, _tool_result: tool_events.append((
                "end",
                tool_request.tool_call_id or "missing",
            )),
            on_runtime_state_update=lambda state: runtime_events.append({
                "analysis_plan": (
                    state.analysis_plan.model_dump(mode="json")
                    if state.analysis_plan is not None
                    else None
                ),
                "checkpoint": (
                    state.checkpoint.digest_payload()
                    if state.checkpoint is not None
                    else None
                ),
            }),
        ),
    )


def test_runtime_loop_coordination_handles_disabled_features_before_completion() -> None:
    state = ToolOrchestrationState()
    decision_policy = _FakeDecisionPolicy()
    completion_policy = _FakeCompletionPolicy(
        SuccessfulTurnCompletion(payload={"answer": "done", "warnings": []})
    )
    service = _service(
        decision_policy=decision_policy,
        completion_policy=completion_policy,
        tool_execution_policy=_FakeToolExecutionPolicy(
            tool_result_decision=StopToolResultDecision(
                payload={"answer": "unused"},
            )
        ),
    )
    completions: list[tuple[str, str | None]] = []
    failures: list[str] = []
    tool_events: list[tuple[str, str]] = []

    result = service.run(
        runtime=RuntimeLoopInput(
            llm_client=_SequencedLlmClient([({"content": " plain final "}, True, True)]),
            request=ChatAskRequest(question="hi"),
            db_connectors=[],
            messages=[],
            tools=[{"function": {"name": "run_sql"}}],
            state=state,
            tool_executor=_FakeToolExecutor({"ok": True}),
        ),
        transport=_transport(
            completions=completions,
            failures=failures,
            tool_events=tool_events,
        ),
    )

    completion_input = completion_policy.calls[0]["completion_input"]
    assert isinstance(completion_input, NonToolTurnCompletionInput)
    assert completion_input.has_current_tools is False
    assert completion_input.current_tool_names == []
    assert state.warnings == [
        "llm_tools_not_supported",
        "llm_structured_output_not_supported",
    ]
    assert completions == [("success", "plain final")]
    assert failures == []
    assert tool_events == []
    assert result["answer"] == "done"


def test_runtime_loop_coordination_routes_llm_failure_through_shared_payload() -> None:
    state = ToolOrchestrationState()
    service = _service(
        decision_policy=_FakeDecisionPolicy(),
        completion_policy=_FakeCompletionPolicy(
            SuccessfulTurnCompletion(payload={"answer": "unused"})
        ),
        tool_execution_policy=_FakeToolExecutionPolicy(
            tool_result_decision=StopToolResultDecision(payload={"answer": "unused"})
        ),
        llm_request_failed_payload=lambda current_state: {
            "answer": "failed",
            "warnings": list(current_state.warnings),
        },
    )
    completions: list[tuple[str, str | None]] = []
    failures: list[str] = []
    tool_events: list[tuple[str, str]] = []

    result = service.run(
        runtime=RuntimeLoopInput(
            llm_client=_SequencedLlmClient([], error=RuntimeError("boom")),
            request=ChatAskRequest(question="hi"),
            db_connectors=[],
            messages=[],
            tools=[],
            state=state,
            tool_executor=_FakeToolExecutor({"ok": True}),
        ),
        transport=_transport(
            completions=completions,
            failures=failures,
            tool_events=tool_events,
        ),
    )

    assert result == {
        "answer": "failed",
        "warnings": ["llm_request_failed"],
    }
    assert failures == ["RuntimeError"]
    assert completions == []
    assert tool_events == []


def test_runtime_loop_coordination_dispatches_tool_batch_with_transport_hooks() -> None:
    state = ToolOrchestrationState()
    service = _service(
        decision_policy=_FakeDecisionPolicy(),
        completion_policy=_FakeCompletionPolicy(
            SuccessfulTurnCompletion(payload={"answer": "unused"})
        ),
        tool_execution_policy=_FakeToolExecutionPolicy(
            tool_result_decision=StopToolResultDecision(payload={"answer": "stopped"})
        ),
    )
    completions: list[tuple[str, str | None]] = []
    failures: list[str] = []
    tool_events: list[tuple[str, str]] = []
    tool_executor = _FakeToolExecutor({"ok": False, "error_code": "fatal"})

    result = service.run(
        runtime=RuntimeLoopInput(
            llm_client=_SequencedLlmClient([
                (
                    {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "run_sql",
                                    "arguments": '{"sql":"SELECT 1"}',
                                },
                            }
                        ],
                    },
                    False,
                    False,
                )
            ]),
            request=ChatAskRequest(question="sql"),
            db_connectors=[],
            messages=[],
            tools=[{"function": {"name": "run_sql"}}],
            state=state,
            tool_executor=tool_executor,
        ),
        transport=_transport(
            completions=completions,
            failures=failures,
            tool_events=tool_events,
        ),
    )

    assert result == {"answer": "stopped"}
    assert len(tool_executor.calls) == 1
    assert tool_events == [("start", "call-1"), ("end", "call-1")]
    assert completions == []
    assert failures == []


def test_failed_isolated_worker_result_does_not_stop_parent_loop() -> None:
    result = AnalysisWorkerResult(
        task_id="worker-1",
        goal="Execute independent SQL branch",
        status="failed",
        blocker="worker_sql_parse_failed:Invalid expression",
        warnings=["analysis_worker_failed"],
    )

    tool_result = RuntimeLoopCoordinationService._tool_result_from_worker_result(result)

    assert tool_result["ok"] is False
    assert tool_result["can_continue"] is True
    assert tool_result["error"] == "worker_sql_parse_failed:Invalid expression"


def test_runtime_loop_coordination_appends_only_prompt_artifact_for_tool_result() -> None:
    state = ToolOrchestrationState()
    service = _service(
        decision_policy=_FakeDecisionPolicy(),
        completion_policy=_FakeCompletionPolicy(
            SuccessfulTurnCompletion(payload={"answer": "unused"})
        ),
        tool_execution_policy=_FakeToolExecutionPolicy(
            tool_result_decision=StopToolResultDecision(payload={"answer": "stopped"})
        ),
    )
    messages: list[dict[str, Any]] = []
    raw_payload = "RAW_TOOL_PAYLOAD:" + ("x" * 20_000)
    tool_executor = _FakeToolExecutor({
        "ok": True,
        "execution_id": "exec-raw",
        "rows": [{"id": index, "payload": "value-" + ("x" * 2_000)} for index in range(12)],
        "debug_payload": raw_payload,
    })

    service.run(
        runtime=RuntimeLoopInput(
            llm_client=_SequencedLlmClient([
                (
                    {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-raw",
                                "type": "function",
                                "function": {
                                    "name": "run_sql",
                                    "arguments": '{"sql":"SELECT 1"}',
                                },
                            }
                        ],
                    },
                    False,
                    False,
                )
            ]),
            request=ChatAskRequest(question="sql"),
            db_connectors=[],
            messages=messages,
            tools=[{"function": {"name": "run_sql"}}],
            state=state,
            tool_executor=tool_executor,
        ),
        transport=_transport(completions=[], failures=[], tool_events=[]),
    )

    tool_message = messages[-1]
    prompt_payload = json.loads(tool_message["content"])
    assert tool_message["role"] == "tool"
    assert prompt_payload["artifact_type"] == "evidence_summary"
    run_sql = prompt_payload["compact_payload"]["run_sql"]
    assert run_sql["execution_id"] == "exec-raw"
    assert run_sql["prompt_rows_returned"] == 12
    assert "RAW_TOOL_PAYLOAD" not in tool_message["content"]
    assert "prompt_evidence_summary_raw_payload_omitted" in state.warnings


def test_runtime_loop_coordination_appends_compact_runtime_artifacts_only() -> None:
    state = ToolOrchestrationState(
        analysis_plan=AnalysisPlan(
            plan_id="plan-1",
            goal="Explain revenue drop",
            status="running",
            steps=[
                AnalysisPlanStep(
                    id="baseline",
                    title="Build baseline",
                    status="done",
                    evidence_ids=["exec-1"],
                )
            ],
            required_evidence=["exec-1"],
        ),
        worker_results=[
            AnalysisWorkerResult(
                task_id="worker-1",
                goal="Check segment",
                status="succeeded",
                key_finding="Segment A dropped.",
                evidence_ids=["exec-2"],
            )
        ],
        checkpoint=AnalysisCheckpoint(
            run_id="run-1",
            plan_id="plan-1",
            status="running",
            active_step_id="segment",
        ),
    )
    service = _service(
        decision_policy=_FakeDecisionPolicy(),
        completion_policy=_FakeCompletionPolicy(
            SuccessfulTurnCompletion(payload={"answer": "done", "warnings": []})
        ),
        tool_execution_policy=_FakeToolExecutionPolicy(
            tool_result_decision=StopToolResultDecision(payload={"answer": "unused"})
        ),
    )
    messages: list[dict[str, Any]] = []

    service.run(
        runtime=RuntimeLoopInput(
            llm_client=_SequencedLlmClient([({"content": "final"}, False, False)]),
            request=ChatAskRequest(question="root cause"),
            db_connectors=[],
            messages=messages,
            tools=[],
            state=state,
            tool_executor=_FakeToolExecutor({"ok": True}),
        ),
        transport=_transport(completions=[], failures=[], tool_events=[]),
    )

    artifact_types = [
        json.loads(message["content"])["artifact_type"]
        for message in messages[:3]
    ]
    assert artifact_types == ["analysis_plan", "worker_result", "checkpoint_digest"]
    assert "raw_transcript" not in json.dumps(messages, ensure_ascii=False)


def test_runtime_loop_coordination_emits_llm_planned_runtime_state() -> None:
    state = ToolOrchestrationState()
    service = _service(
        decision_policy=_FakeDecisionPolicy(),
        completion_policy=_FakeCompletionPolicy(
            SuccessfulTurnCompletion(payload={"answer": "done", "warnings": []})
        ),
        tool_execution_policy=_FakeToolExecutionPolicy(
            tool_result_decision=ContinueToolResultDecision()
        ),
    )
    runtime_events: list[dict[str, Any]] = []

    service.run(
        runtime=RuntimeLoopInput(
            llm_client=_SequencedLlmClient(
                [({"content": "final"}, False, False)],
                planner_message={
                    "content": json.dumps({
                        "should_plan": True,
                        "plan": {
                            "goal": "Explain revenue drop",
                            "steps": [
                                {
                                    "id": "baseline",
                                    "title": "Build baseline",
                                    "required_evidence": ["execution_result"],
                                }
                            ],
                            "required_evidence": ["execution_result"],
                            "open_questions": [],
                        },
                        "warnings": [],
                    })
                },
            ),
            request=ChatAskRequest(question="Explain revenue drop"),
            db_connectors=[],
            messages=[],
            tools=[{"type": "function", "function": {"name": "run_sql"}}],
            state=state,
            tool_executor=_FakeToolExecutor({"ok": True}),
        ),
        transport=_transport(
            completions=[],
            failures=[],
            tool_events=[],
            runtime_events=runtime_events,
        ),
    )

    assert runtime_events
    assert runtime_events[0]["analysis_plan"]["steps"][0]["id"] == "baseline"
    assert runtime_events[0]["checkpoint"]["status"] == "planned"
    assert state.checkpoint is not None


def test_runtime_loop_coordination_executes_parallel_sql_calls_as_workers_without_raw_leak() -> None:
    state = ToolOrchestrationState(
        analysis_plan=AnalysisPlan(
            plan_id="plan-1",
            goal="Explain root cause",
            status="running",
            steps=[
                AnalysisPlanStep(id="baseline", title="Build baseline"),
                AnalysisPlanStep(id="breakdown", title="Breakdown"),
            ],
            required_evidence=["execution_result"],
        ),
    )
    service = _service(
        decision_policy=_FakeDecisionPolicy(),
        completion_policy=_FakeCompletionPolicy(
            SuccessfulTurnCompletion(payload={"answer": "unused"})
        ),
        tool_execution_policy=_FakeToolExecutionPolicy(
            tool_result_decision=ContinueToolResultDecision()
        ),
    )
    messages: list[dict[str, Any]] = []
    tool_executor = _FakeToolExecutor({
        "ok": True,
        "execution": {
            "execution_id": "exec-worker",
            "status": "succeeded",
            "row_count": 1,
            "preview": {"columns": ["value"], "rows": [[1]]},
        },
        "raw_transcript": "RAW_WORKER_TRANSCRIPT",
    })

    service.run(
        runtime=RuntimeLoopInput(
            llm_client=_SequencedLlmClient([
                (
                    {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "run_sql",
                                    "arguments": '{"sql":"SELECT 1"}',
                                },
                            },
                            {
                                "id": "call-2",
                                "type": "function",
                                "function": {
                                    "name": "run_sql",
                                    "arguments": '{"sql":"SELECT 2"}',
                                },
                            },
                        ],
                    },
                    False,
                    False,
                ),
                ({"content": "final"}, False, False),
            ]),
            request=ChatAskRequest(question="root cause"),
            db_connectors=[],
            messages=messages,
            tools=[{"function": {"name": "run_sql"}}],
            state=state,
            tool_executor=tool_executor,
        ),
        transport=_transport(completions=[], failures=[], tool_events=[]),
    )

    assert len(tool_executor.calls) == 2
    assert len(state.tool_call_results) == 2
    assert [result.status for result in state.worker_results] == [
        "succeeded",
        "succeeded",
    ]
    assert "RAW_WORKER_TRANSCRIPT" not in json.dumps(messages, ensure_ascii=False)
    parsed_messages = [
        json.loads(message["content"])
        for message in messages
        if str(message.get("content") or "").strip().startswith("{")
    ]
    worker_artifacts = [
        message
        for message in parsed_messages
        if message.get("artifact_type") == "worker_result"
    ]
    assert len(worker_artifacts) == 2


def test_runtime_loop_coordination_does_not_worker_duplicate_sql_calls() -> None:
    state = ToolOrchestrationState(
        analysis_plan=AnalysisPlan(
            plan_id="plan-1",
            goal="Explain root cause",
            status="running",
            steps=[AnalysisPlanStep(id="baseline", title="Build baseline")],
            required_evidence=["execution_result"],
        ),
    )
    tool_policy = _DuplicateAwareToolExecutionPolicy()
    service = _service(
        decision_policy=_FakeDecisionPolicy(),
        completion_policy=_FakeCompletionPolicy(
            SuccessfulTurnCompletion(payload={"answer": "done"})
        ),
        tool_execution_policy=tool_policy,
    )
    messages: list[dict[str, Any]] = []
    tool_executor = _FakeToolExecutor({
        "ok": True,
        "execution": {
            "execution_id": "exec-1",
            "status": "succeeded",
            "row_count": 1,
            "preview": {"columns": ["value"], "rows": [[1]]},
        },
    })

    service.run(
        runtime=RuntimeLoopInput(
            llm_client=_SequencedLlmClient([
                (
                    {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "run_sql",
                                    "arguments": '{"sql":"SELECT 1"}',
                                },
                            },
                            {
                                "id": "call-2",
                                "type": "function",
                                "function": {
                                    "name": "run_sql",
                                    "arguments": '{"sql":"SELECT 1"}',
                                },
                            },
                        ],
                    },
                    False,
                    False,
                ),
                ({"content": "final"}, False, False),
            ]),
            request=ChatAskRequest(question="root cause"),
            db_connectors=[],
            messages=messages,
            tools=[{"function": {"name": "run_sql"}}],
            state=state,
            tool_executor=tool_executor,
        ),
        transport=_transport(completions=[], failures=[], tool_events=[]),
    )

    assert len(tool_executor.calls) == 1
    assert state.worker_results == []
