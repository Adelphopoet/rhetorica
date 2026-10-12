from __future__ import annotations

from types import SimpleNamespace

from rhetorica_backend.contracts.chat import (
    CapabilityTrace,
    ChatAskRequest,
    DoneEvent,
    ErrorEvent,
    OptimizationTrace,
    ThinkingEvent,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    PersistConversationHistory,
    SkipConversationHistory,
    TransportFinalizationInput,
)
from rhetorica_backend.domain.chat.orchestration.response_assembly import (
    AssembledChatTurn,
    ChatHistoryPersistenceService,
)
from rhetorica_backend.domain.chat.orchestration.transport_finalization import (
    PostAssemblyTransportFinalizationService,
    StreamingChatTransportAdapter,
    SyncChatTransportAdapter,
)


def _turn(*, answer: str = "ok", warnings: list[str] | None = None) -> AssembledChatTurn:
    return AssembledChatTurn(
        answer=answer,
        generated_sql=None,
        selected_database_connector="warehouse",
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
        warnings=list(warnings or []),
    )


def test_sync_transport_finalization_persists_history_and_attaches_message_id() -> None:
    saved_messages: list[dict[str, object]] = []

    def save_message(**kwargs):
        saved_messages.append(kwargs)
        return SimpleNamespace(id="assistant-1" if kwargs["role"] == "assistant" else "user-1")

    service = PostAssemblyTransportFinalizationService(
        history_persistence_service=ChatHistoryPersistenceService(
            conversation_manager=SimpleNamespace(save_message=save_message)
        ),
        adapter=SyncChatTransportAdapter(),
    )

    result = service.finalize(
        finalization_input=TransportFinalizationInput(
            request=ChatAskRequest(question="Что по выручке?", workspace_id="ws-1"),
            conversation_id="conv-1",
            completion_kind="runtime_completed",
            turn=_turn(answer="Готово"),
            history_persistence=PersistConversationHistory(conversation_id="conv-1"),
        )
    )

    assert result.response.answer == "Готово"
    assert result.response.conversation_id == "conv-1"
    assert result.response.assistant_message_id == "assistant-1"
    assert result.diagnostics.assistant_message_id == "assistant-1"
    assert result.diagnostics.generated_sql_present is False
    assert [message["role"] for message in saved_messages] == ["user", "assistant"]


def test_sync_transport_finalization_does_not_duplicate_pre_persisted_user_message() -> None:
    saved_messages: list[dict[str, object]] = []

    def save_message(**kwargs):
        saved_messages.append(kwargs)
        return SimpleNamespace(id="assistant-1")

    service = PostAssemblyTransportFinalizationService(
        history_persistence_service=ChatHistoryPersistenceService(
            conversation_manager=SimpleNamespace(save_message=save_message)
        ),
        adapter=SyncChatTransportAdapter(),
    )

    result = service.finalize(
        finalization_input=TransportFinalizationInput(
            request=ChatAskRequest(question="Что по выручке?", workspace_id="ws-1"),
            conversation_id="conv-1",
            completion_kind="runtime_completed",
            turn=_turn(answer="Готово"),
            history_persistence=PersistConversationHistory(conversation_id="conv-1"),
            persisted_user_message_id="user-early",
        )
    )

    assert result.response.assistant_message_id == "assistant-1"
    assert [message["role"] for message in saved_messages] == ["assistant"]


def test_streaming_transport_finalization_keeps_cancelled_done_event_without_persist() -> None:
    persisted_usages: list[dict[str, object]] = []

    def add_token_usage(*, conversation_id, llm_usages):
        assert conversation_id == "conv-1"
        persisted_usages.extend(llm_usages)

    service = PostAssemblyTransportFinalizationService(
        history_persistence_service=ChatHistoryPersistenceService(
            conversation_manager=SimpleNamespace(
                add_token_usage=add_token_usage,
                save_message=lambda **_kwargs: (_ for _ in ()).throw(
                    AssertionError("history persistence must be skipped")
                )
            )
        ),
        adapter=StreamingChatTransportAdapter(),
    )

    result = service.finalize(
        finalization_input=TransportFinalizationInput(
            request=ChatAskRequest(question="Стоп", workspace_id="ws-1"),
            conversation_id="conv-1",
            completion_kind="cancelled",
            turn=_turn(answer="Request was cancelled", warnings=["cancelled"]),
            llm_usages=[
                {
                    "prompt_tokens": 80,
                    "completion_tokens": 10,
                    "total_tokens": 90,
                }
            ],
            history_persistence=SkipConversationHistory(reason="cancelled"),
            transport_events=[ThinkingEvent(content="working")],
        )
    )

    assert len(result.events) == 2
    assert isinstance(result.events[0], ThinkingEvent)
    assert isinstance(result.events[1], DoneEvent)
    assert result.events[1].cancelled is True
    assert result.events[1].assistant_message_id is None
    assert result.terminal.terminal_kind == "done"
    assert result.diagnostics is not None
    assert result.diagnostics.warnings == ["cancelled"]
    assert persisted_usages == [
        {"prompt_tokens": 80, "completion_tokens": 10, "total_tokens": 90}
    ]


def test_streaming_transport_finalization_emits_error_for_missing_llm() -> None:
    service = PostAssemblyTransportFinalizationService(
        history_persistence_service=ChatHistoryPersistenceService(
            conversation_manager=SimpleNamespace(
                save_message=lambda **_kwargs: (_ for _ in ()).throw(
                    AssertionError("history persistence must be skipped")
                )
            )
        ),
        adapter=StreamingChatTransportAdapter(),
    )

    result = service.finalize(
        finalization_input=TransportFinalizationInput(
            request=ChatAskRequest(question="Привет", workspace_id="ws-1"),
            conversation_id=None,
            completion_kind="llm_connector_missing",
            turn=_turn(answer="LLM connector is not configured. Add it in admin."),
            history_persistence=SkipConversationHistory(
                reason="missing_conversation_id"
            ),
        )
    )

    assert len(result.events) == 1
    assert isinstance(result.events[0], ErrorEvent)
    assert result.events[0].code == "llm_connector_missing"
    assert result.terminal.terminal_kind == "error"
    assert result.diagnostics is None


def test_stream_journal_is_saved_atomically_with_answer_before_done():
    saved = []
    def save_message(**kwargs):
        saved.append(kwargs)
        return SimpleNamespace(id="answer")
    journal = [ThinkingEvent(content="Inspecting metadata").model_dump(mode="json")]
    service = PostAssemblyTransportFinalizationService(
        history_persistence_service=ChatHistoryPersistenceService(conversation_manager=SimpleNamespace(save_message=save_message)),
        adapter=StreamingChatTransportAdapter(),
    )
    result = service.finalize(finalization_input=TransportFinalizationInput(
        request=ChatAskRequest(question="Inspect"), conversation_id="conv", completion_kind="runtime_completed",
        turn=_turn(), history_persistence=PersistConversationHistory(conversation_id="conv"),
        journal_events=journal,
    ))
    assert saved[-1]["role"] == "assistant"
    assert saved[-1]["journal_events"] == journal
    assert result.events[-1].assistant_message_id == "answer"
    assert len(result.events) == 1
