from __future__ import annotations

import asyncio
from types import SimpleNamespace

from rhetorica_backend.contracts.chat import (
    CapabilityTrace,
    ChatAskRequest,
    OptimizationTrace,
    ThinkingEvent,
)
from rhetorica_backend.domain.chat.orchestration.capability_registry import (
    WorkspaceCapabilitySurface,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    PersistConversationHistory,
    RuntimeExecutionResult,
    SkipConversationHistory,
    StreamingRuntimeTransportArtifact,
)
from rhetorica_backend.domain.chat.orchestration.response_assembly import (
    AssembledChatTurn,
)
from rhetorica_backend.domain.chat.orchestration.session import (
    OrchestrationSession,
)
from rhetorica_backend.domain.chat.orchestration.session_turn_flow import (
    OrchestrationSessionTurnFlowService,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.turn_builder import (
    WorkspaceToolingSnapshot,
)


class _RecordingAssembler:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.saved_user_messages: list[dict[str, object]] = []

    def assemble_turn(self, **kwargs):
        self.calls.append(kwargs)
        llm_payload = kwargs["llm_payload"]
        return AssembledChatTurn(
            answer=str(llm_payload["answer"]),
            generated_sql=llm_payload.get("sql"),
            selected_database_connector=kwargs["selected_connector_name"],
            metadata_sources=[],
            context_tables=[],
            runtime_context={},
            claims=[],
            evidence=[],
            claim_evidence_links=[],
            uncertainty_flags=[],
            capability_trace=CapabilityTrace(available_tools=["run_sql"]),
            optimization_trace=OptimizationTrace(),
            execution=None,
            analysis_document=None,
            visualization=None,
            warnings=list(kwargs["warnings"]),
        )

    def save_user_to_history(self, **kwargs) -> str | None:
        self.saved_user_messages.append(kwargs)
        if kwargs.get("conversation_id") is None:
            return None
        return f"user-{len(self.saved_user_messages)}"


def _session(
    *,
    request: ChatAskRequest,
    llm_connection: object | None,
    conversation_id: str | None = "conv-1",
    warnings: list[str] | None = None,
    llm_capability_profile: object | None = None,
    resolved_llm_connector_name: str | None = None,
) -> OrchestrationSession:
    return OrchestrationSession(
        request=request,
        conversation_id=conversation_id,
        llm_connection=llm_connection,
        db_connectors=[SimpleNamespace(name="warehouse")],
        workspace_snapshot=WorkspaceToolingSnapshot(
            has_db_connectors=True,
            rag_configured=False,
        ),
        capability_surface=WorkspaceCapabilitySurface(
            available_tools=["run_sql"],
            degraded_capabilities=[],
            policy_decisions=["prompt_mode:sql"],
        ),
        agent_session=SimpleNamespace(session_id=conversation_id),
        warnings=list(warnings or []),
        llm_capability_profile=llm_capability_profile,
        resolved_llm_connector_name=resolved_llm_connector_name,
    )


def test_session_turn_flow_routes_capability_question_through_runtime() -> None:
    request = ChatAskRequest(
        question="Что ты умеешь?",
        workspace_id="ws-1",
    )
    assembler = _RecordingAssembler()
    runtime_calls: list[object] = []
    prepared_turn = SimpleNamespace(
        messages=[{"role": "user", "content": request.question}],
        tools=[{"function": {"name": "list_tool_groups"}}],
        state=ToolOrchestrationState(),
        tool_executor=object(),
    )
    service = OrchestrationSessionTurnFlowService(
        session_factory=SimpleNamespace(
            build=lambda *_args, **_kwargs: _session(
                request=request,
                llm_connection=SimpleNamespace(model="gpt-5", extra_settings={}),
                warnings=["history_trimmed"],
            )
        ),
        turn_builder=SimpleNamespace(prepare_turn=lambda **_kwargs: prepared_turn),
        response_assembler=assembler,
        create_llm_client=lambda _connection: object(),
        requires_metadata_lookup=lambda _request: False,
    )

    result = service.run(
        request=request,
        create_conversation_without_llm=False,
        state_factory=ToolOrchestrationState,
        execute_runtime=lambda runtime: runtime_calls.append(runtime)
        or RuntimeExecutionResult(llm_payload={"answer": "Ответ модели"}),
    )

    assert result.completion_kind == "runtime_completed"
    assert result.turn.answer == "Ответ модели"
    assert result.turn.warnings == ["history_trimmed"]
    assert result.persisted_user_message_id == "user-1"
    assert result.history_persistence == PersistConversationHistory(conversation_id="conv-1")
    assert assembler.saved_user_messages[0]["conversation_id"] == "conv-1"
    assert len(runtime_calls) == 1
    assert runtime_calls[0].messages == prepared_turn.messages


def test_session_turn_flow_async_routes_capability_question_through_runtime() -> None:
    request = ChatAskRequest(question="What can you do?", workspace_id="ws-1")
    prepared_turn = SimpleNamespace(
        messages=[{"role": "user", "content": request.question}],
        tools=[{"function": {"name": "list_tool_groups"}}],
        state=ToolOrchestrationState(),
        tool_executor=object(),
    )
    runtime_calls: list[object] = []
    service = OrchestrationSessionTurnFlowService(
        session_factory=SimpleNamespace(
            build=lambda *_args, **_kwargs: _session(
                request=request,
                llm_connection=SimpleNamespace(model="gpt-5", extra_settings={}),
            )
        ),
        turn_builder=SimpleNamespace(prepare_turn=lambda **_kwargs: prepared_turn),
        response_assembler=_RecordingAssembler(),
        create_llm_client=lambda _connection: object(),
        requires_metadata_lookup=lambda _request: False,
    )

    async def execute_runtime(runtime):
        runtime_calls.append(runtime)
        return RuntimeExecutionResult(llm_payload={"answer": "Model capabilities"})

    result = asyncio.run(
        service.run_async(
            request=request,
            create_conversation_without_llm=False,
            state_factory=ToolOrchestrationState,
            execute_runtime=execute_runtime,
        )
    )

    assert result.completion_kind == "runtime_completed"
    assert result.turn.answer == "Model capabilities"
    assert len(runtime_calls) == 1


def test_session_turn_flow_short_circuits_missing_llm_before_runtime() -> None:
    request = ChatAskRequest(
        question="Покажи выручку",
        workspace_id="ws-1",
    )
    assembler = _RecordingAssembler()
    runtime_calls: list[object] = []
    service = OrchestrationSessionTurnFlowService(
        session_factory=SimpleNamespace(
            build=lambda *_args, **_kwargs: _session(
                request=request,
                llm_connection=None,
                conversation_id=None,
            )
        ),
        turn_builder=SimpleNamespace(prepare_turn=lambda **_kwargs: None),
        response_assembler=assembler,
        create_llm_client=lambda _connection: object(),
        requires_metadata_lookup=lambda _request: False,
    )

    result = service.run(
        request=request,
        create_conversation_without_llm=False,
        state_factory=ToolOrchestrationState,
        execute_runtime=lambda _runtime: runtime_calls.append(_runtime),
    )

    assert result.completion_kind == "llm_connector_missing"
    assert result.turn.answer == "LLM connector is not configured. Add it in admin."
    assert result.turn.warnings == ["llm_connector_missing"]
    assert result.persisted_user_message_id is None
    assert result.history_persistence == SkipConversationHistory(reason="missing_conversation_id")
    assert runtime_calls == []


def test_session_turn_flow_async_short_circuits_missing_llm_before_runtime() -> None:
    request = ChatAskRequest(question="Покажи выручку", workspace_id="ws-1")
    runtime_calls: list[object] = []
    service = OrchestrationSessionTurnFlowService(
        session_factory=SimpleNamespace(
            build=lambda *_args, **_kwargs: _session(
                request=request,
                llm_connection=None,
                conversation_id=None,
            )
        ),
        turn_builder=SimpleNamespace(prepare_turn=lambda **_kwargs: None),
        response_assembler=_RecordingAssembler(),
        create_llm_client=lambda _connection: object(),
        requires_metadata_lookup=lambda _request: False,
    )

    result = asyncio.run(
        service.run_async(
            request=request,
            create_conversation_without_llm=False,
            state_factory=ToolOrchestrationState,
            execute_runtime=lambda runtime: runtime_calls.append(runtime),
        )
    )

    assert result.completion_kind == "llm_connector_missing"
    assert result.turn.warnings == ["llm_connector_missing"]
    assert runtime_calls == []


def test_session_turn_flow_prepares_runtime_and_postprocesses_payload() -> None:
    request = ChatAskRequest(
        question="Покажи выручку",
        workspace_id="ws-1",
    )
    assembler = _RecordingAssembler()
    prepared_turn = SimpleNamespace(
        messages=[{"role": "system", "content": "sys"}],
        tools=[{"function": {"name": "run_sql"}}],
        state=ToolOrchestrationState(),
        tool_executor=object(),
    )
    seen_prepare_kwargs: dict[str, object] = {}
    seen_runtime: list[object] = []
    seen_postprocess: list[dict[str, object]] = []
    service = OrchestrationSessionTurnFlowService(
        session_factory=SimpleNamespace(
            build=lambda *_args, **_kwargs: _session(
                request=request,
                llm_connection=SimpleNamespace(
                    model="gpt-5",
                    endpoint_path="/responses",
                    extra_settings={"support_tool_calls": True},
                ),
                warnings=["history_trimmed"],
            )
        ),
        turn_builder=SimpleNamespace(
            prepare_turn=lambda **kwargs: seen_prepare_kwargs.update(kwargs) or prepared_turn
        ),
        response_assembler=assembler,
        create_llm_client=lambda connection: {"client_for": connection.model},
        requires_metadata_lookup=lambda current_request: current_request.run_sql,
        postprocess_llm_payload=lambda **kwargs: seen_postprocess.append({
            **kwargs,
            "warnings": list(kwargs["warnings"]),
        })
        or {
            **kwargs["llm_payload"],
            "answer": f"{kwargs['llm_payload']['answer']} [postprocessed]",
            "warnings": [*kwargs["llm_payload"].get("warnings", []), "payload_postprocessed"],
        },
    )

    result = service.run(
        request=request,
        create_conversation_without_llm=True,
        state_factory=ToolOrchestrationState,
        execute_runtime=lambda runtime: seen_runtime.append(runtime)
        or RuntimeExecutionResult(
            llm_payload={
                "answer": "runtime answer",
                "warnings": ["runtime_warning"],
            },
            transport_artifact=StreamingRuntimeTransportArtifact(
                events=[ThinkingEvent(content="evt-1")]
            ),
        ),
    )

    assert seen_prepare_kwargs["request"] == request
    assert seen_prepare_kwargs["workspace_snapshot"].has_db_connectors is True
    assert seen_prepare_kwargs["initial_warnings"] == ["history_trimmed"]
    assert seen_prepare_kwargs["state_factory"] is ToolOrchestrationState
    assert seen_runtime[0].llm_client == {"client_for": "gpt-5"}
    assert seen_runtime[0].request == request
    assert seen_runtime[0].db_connectors[0].name == "warehouse"
    assert seen_runtime[0].messages == [{"role": "system", "content": "sys"}]
    assert seen_runtime[0].tools == [{"function": {"name": "run_sql"}}]
    assert seen_runtime[0].state is prepared_turn.state
    assert seen_runtime[0].tool_executor is prepared_turn.tool_executor
    assert assembler.saved_user_messages[0]["conversation_id"] == "conv-1"
    assert assembler.saved_user_messages[0]["request"] == request
    assert seen_postprocess[0]["warnings"] == ["history_trimmed", "runtime_warning"]
    assert result.completion_kind == "runtime_completed"
    assert isinstance(result.transport_artifact, StreamingRuntimeTransportArtifact)
    assert len(result.transport_artifact.events) == 1
    assert isinstance(result.transport_artifact.events[0], ThinkingEvent)
    assert result.transport_artifact.events[0].content == "evt-1"
    assert result.turn.answer == "runtime answer [postprocessed]"
    assert result.turn.warnings == [
        "history_trimmed",
        "runtime_warning",
        "payload_postprocessed",
    ]
    assert result.persisted_user_message_id == "user-1"
    assert result.history_persistence == PersistConversationHistory(conversation_id="conv-1")


def test_session_turn_flow_does_not_preflight_structured_output_for_toolless_turn() -> None:
    request = ChatAskRequest(question="привет", workspace_id="ws-1", run_sql=False)
    prepared_turn = SimpleNamespace(
        messages=[],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=object(),
    )
    runtime_calls: list[object] = []
    service = OrchestrationSessionTurnFlowService(
        session_factory=SimpleNamespace(
            build=lambda *_args, **_kwargs: _session(
                request=request,
                llm_connection=SimpleNamespace(model="ExampleReasoningModel", extra_settings={}),
                llm_capability_profile=SimpleNamespace(supports=set()),
            )
        ),
        turn_builder=SimpleNamespace(prepare_turn=lambda **_kwargs: prepared_turn),
        response_assembler=_RecordingAssembler(),
        create_llm_client=lambda _connection: object(),
        requires_metadata_lookup=lambda _request: False,
    )

    result = service.run(
        request=request,
        create_conversation_without_llm=True,
        state_factory=ToolOrchestrationState,
        execute_runtime=lambda runtime: runtime_calls.append(runtime)
        or RuntimeExecutionResult(llm_payload={"answer": "unreachable"}),
    )

    assert len(runtime_calls) == 1
    assert result.turn.answer == "unreachable"
    assert result.turn.warnings == []


def test_session_turn_flow_does_not_fail_fast_on_llm_transport_contract() -> None:
    request = ChatAskRequest(question="пример строк из ft_order", workspace_id="ws-1")
    prepared_turn = SimpleNamespace(
        messages=[],
        tools=[{"function": {"name": "run_sql"}}],
        state=ToolOrchestrationState(),
        tool_executor=object(),
    )
    runtime_calls: list[object] = []
    service = OrchestrationSessionTurnFlowService(
        session_factory=SimpleNamespace(
            build=lambda *_args, **_kwargs: _session(
                request=request,
                llm_connection=SimpleNamespace(
                    model="ExampleReasoningModel",
                    endpoint_path="/chat/completions",
                    extra_settings={},
                ),
                llm_capability_profile=SimpleNamespace(supports={"structured_output"}),
                resolved_llm_connector_name="example-llm",
            )
        ),
        turn_builder=SimpleNamespace(prepare_turn=lambda **_kwargs: prepared_turn),
        response_assembler=_RecordingAssembler(),
        create_llm_client=lambda _connection: object(),
        requires_metadata_lookup=lambda _request: False,
    )

    result = service.run(
        request=request,
        create_conversation_without_llm=True,
        state_factory=ToolOrchestrationState,
        execute_runtime=lambda runtime: runtime_calls.append(runtime)
        or RuntimeExecutionResult(llm_payload={"answer": "runtime answer"}),
    )

    assert len(runtime_calls) == 1
    assert runtime_calls[0].request.llm_connector_name == "example-llm"
    assert result.turn.answer == "runtime answer"
    assert result.turn.warnings == []


def test_session_turn_flow_marks_cancelled_runtime_as_non_persistent() -> None:
    request = ChatAskRequest(
        question="Покажи выручку",
        workspace_id="ws-1",
    )
    assembler = _RecordingAssembler()
    prepared_turn = SimpleNamespace(
        messages=[],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=object(),
    )
    service = OrchestrationSessionTurnFlowService(
        session_factory=SimpleNamespace(
            build=lambda *_args, **_kwargs: _session(
                request=request,
                llm_connection=SimpleNamespace(model="gpt-5", extra_settings={}),
            )
        ),
        turn_builder=SimpleNamespace(prepare_turn=lambda **_kwargs: prepared_turn),
        response_assembler=assembler,
        create_llm_client=lambda _connection: object(),
        requires_metadata_lookup=lambda _request: False,
    )

    result = service.run(
        request=request,
        create_conversation_without_llm=False,
        state_factory=ToolOrchestrationState,
        execute_runtime=lambda _runtime: RuntimeExecutionResult(
            llm_payload={
                "answer": "Request was cancelled",
                "warnings": ["cancelled"],
            }
        ),
    )

    assert result.completion_kind == "cancelled"
    assert result.turn.warnings == ["cancelled"]
    assert result.persisted_user_message_id == "user-1"
    assert result.history_persistence == SkipConversationHistory(reason="cancelled")
