from __future__ import annotations

from types import SimpleNamespace

from rhetorica_backend.contracts.chat import (
    CapabilityTrace,
    ChatAskRequest,
    OptimizationTrace,
    ThinkingEvent,
)
from rhetorica_backend.domain.chat.orchestration.analysis_artifacts import (
    AnalysisCheckpointLifecycle,
    AnalysisPlan,
    AnalysisPlanStep,
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
            metadata_configured=True,
            metadata_connector_names=["ometa"],
            rag_configured=False,
            confluence_configured=False,
            confluence_connector_names=[],
            jira_configured=False,
            jira_connector_names=[],
        ),
        capability_surface=WorkspaceCapabilitySurface(
            available_tools=["run_sql"],
            degraded_capabilities=[],
            policy_decisions=["prompt_mode:sql"],
        ),
        conversation_history=[{"role": "assistant", "content": "prev"}],
        warnings=list(warnings or []),
        llm_capability_profile=llm_capability_profile,
        resolved_llm_connector_name=resolved_llm_connector_name,
    )


def test_session_turn_flow_short_circuits_capability_question() -> None:
    request = ChatAskRequest(
        question="Что ты умеешь?",
        workspace_id="ws-1",
    )
    assembler = _RecordingAssembler()
    runtime_calls: list[object] = []
    service = OrchestrationSessionTurnFlowService(
        session_factory=SimpleNamespace(
            build=lambda *_args, **_kwargs: _session(
                request=request,
                llm_connection=None,
                warnings=["history_trimmed"],
            )
        ),
        turn_builder=SimpleNamespace(prepare_turn=lambda **_kwargs: None),
        response_assembler=assembler,
        create_llm_client=lambda _connection: object(),
        is_capabilities_question=lambda question: "умеешь" in question.lower(),
        build_capabilities_answer=lambda **_kwargs: "Я умею:\n- выполнять SQL",
        requires_metadata_lookup=lambda _request: False,
    )

    result = service.run(
        request=request,
        create_conversation_without_llm=False,
        state_factory=ToolOrchestrationState,
        execute_runtime=lambda _runtime: runtime_calls.append(_runtime),
    )

    assert result.completion_kind == "capability_question"
    assert result.turn.answer == "Я умею:\n- выполнять SQL"
    assert result.turn.warnings == ["history_trimmed"]
    assert result.history_persistence == PersistConversationHistory(
        conversation_id="conv-1"
    )
    assert runtime_calls == []


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
        is_capabilities_question=lambda _question: False,
        build_capabilities_answer=lambda **_kwargs: "unused",
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
    assert result.history_persistence == SkipConversationHistory(
        reason="missing_conversation_id"
    )
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
        protocol_repair_enabled=True,
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
        is_capabilities_question=lambda _question: False,
        build_capabilities_answer=lambda **_kwargs: "unused",
        requires_metadata_lookup=lambda current_request: current_request.run_sql,
        postprocess_llm_payload=lambda **kwargs: seen_postprocess.append(
            {
                **kwargs,
                "warnings": list(kwargs["warnings"]),
            }
        )
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
    assert seen_prepare_kwargs["workspace_snapshot"].metadata_connector_names == ["ometa"]
    assert seen_prepare_kwargs["initial_warnings"] == ["history_trimmed"]
    assert seen_prepare_kwargs["state_factory"] is ToolOrchestrationState
    assert seen_runtime[0].llm_client == {"client_for": "gpt-5"}
    assert seen_runtime[0].request == request
    assert seen_runtime[0].db_connectors[0].name == "warehouse"
    assert seen_runtime[0].messages == [{"role": "system", "content": "sys"}]
    assert seen_runtime[0].tools == [{"function": {"name": "run_sql"}}]
    assert seen_runtime[0].state is prepared_turn.state
    assert seen_runtime[0].tool_executor is prepared_turn.tool_executor
    assert seen_runtime[0].protocol_repair_enabled is True
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
    assert result.history_persistence == PersistConversationHistory(
        conversation_id="conv-1"
    )


def test_session_turn_flow_does_not_preflight_structured_output_for_toolless_turn() -> None:
    request = ChatAskRequest(question="привет", workspace_id="ws-1", run_sql=False)
    prepared_turn = SimpleNamespace(
        messages=[],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=object(),
        protocol_repair_enabled=False,
    )
    runtime_calls: list[object] = []
    service = OrchestrationSessionTurnFlowService(
        session_factory=SimpleNamespace(
            build=lambda *_args, **_kwargs: _session(
                request=request,
                llm_connection=SimpleNamespace(model="MagnitCopilot", extra_settings={}),
                llm_capability_profile=SimpleNamespace(supports=set()),
            )
        ),
        turn_builder=SimpleNamespace(prepare_turn=lambda **_kwargs: prepared_turn),
        response_assembler=_RecordingAssembler(),
        create_llm_client=lambda _connection: object(),
        is_capabilities_question=lambda _question: False,
        build_capabilities_answer=lambda **_kwargs: "unused",
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
        protocol_repair_enabled=True,
    )
    runtime_calls: list[object] = []
    service = OrchestrationSessionTurnFlowService(
        session_factory=SimpleNamespace(
            build=lambda *_args, **_kwargs: _session(
                request=request,
                llm_connection=SimpleNamespace(
                    model="MagnitCopilot",
                    endpoint_path="/chat/completions",
                    extra_settings={},
                ),
                llm_capability_profile=SimpleNamespace(
                    supports={"structured_output"}
                ),
                resolved_llm_connector_name="default-gpt",
            )
        ),
        turn_builder=SimpleNamespace(prepare_turn=lambda **_kwargs: prepared_turn),
        response_assembler=_RecordingAssembler(),
        create_llm_client=lambda _connection: object(),
        is_capabilities_question=lambda _question: False,
        build_capabilities_answer=lambda **_kwargs: "unused",
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
    assert result.turn.answer == "runtime answer"
    assert result.turn.warnings == []


def test_session_turn_flow_persists_compact_checkpoint_before_runtime() -> None:
    request = ChatAskRequest(question="Почему упала выручка?", workspace_id="ws-1")
    plan = AnalysisPlan(
        plan_id="plan-1",
        goal="Почему упала выручка?",
        status="planned",
        steps=[AnalysisPlanStep(id="baseline", title="Build baseline")],
        required_evidence=["execution_result"],
    )
    state = ToolOrchestrationState(
        analysis_plan=plan,
        checkpoint=AnalysisCheckpointLifecycle().start(
            run_id="run-1",
            plan_id="plan-1",
        ),
    )
    prepared_turn = SimpleNamespace(
        messages=[],
        tools=[],
        state=state,
        tool_executor=object(),
        protocol_repair_enabled=False,
    )
    persisted: list[dict[str, object]] = []
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
        is_capabilities_question=lambda _question: False,
        build_capabilities_answer=lambda **_kwargs: "unused",
        requires_metadata_lookup=lambda _request: False,
        checkpoint_persistence_service=SimpleNamespace(
            persist_runtime_state=lambda **kwargs: persisted.append(kwargs)
        ),
    )

    service.run(
        request=request,
        create_conversation_without_llm=True,
        state_factory=ToolOrchestrationState,
        execute_runtime=lambda _runtime: RuntimeExecutionResult(
            llm_payload={"answer": "done"}
        ),
    )

    assert persisted[0]["conversation_id"] == "conv-1"
    assert persisted[0]["state"] is state
    assert state.conversation_id == "conv-1"


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
        protocol_repair_enabled=False,
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
        is_capabilities_question=lambda _question: False,
        build_capabilities_answer=lambda **_kwargs: "unused",
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
    assert result.history_persistence == SkipConversationHistory(reason="cancelled")
