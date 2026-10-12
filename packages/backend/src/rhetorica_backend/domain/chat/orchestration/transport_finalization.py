"""Shared post-assembly transport finalization for sync and streaming chat shells."""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

from rhetorica_backend.contracts.chat import ErrorEvent, StreamEvent
from rhetorica_backend.domain.chat.orchestration.models import (
    HistoryPersistenceDecision,
    HistoryPersistenceResult,
    PersistConversationHistory,
    SkipConversationHistory,
    StreamingDoneTerminalResult,
    StreamingErrorTerminalResult,
    StreamingRuntimeTransportArtifact,
    StreamingTransportFinalizationResult,
    SyncTransportFinalizationResult,
    TransportCompletionDiagnostics,
    TransportFinalizationInput,
)
from rhetorica_backend.domain.chat.orchestration.response_assembly import (
    ChatHistoryPersistenceService,
)

if TYPE_CHECKING:
    from rhetorica_backend.contracts.chat import ChatAskRequest
    from rhetorica_backend.domain.chat.orchestration.models import CompletedSessionFlow

TTransportResult = TypeVar("TTransportResult")


class TransportFinalizationAdapter(Protocol[TTransportResult]):
    """Builds a transport-specific final artifact from a finalized assembled turn."""

    def build_result(
        self,
        *,
        finalization_input: TransportFinalizationInput,
        history_result: HistoryPersistenceResult,
    ) -> TTransportResult: ...


class HistoryPersistencePolicy:
    """Decides whether an assembled turn should be written into history."""

    def decide(
        self,
        *,
        conversation_id: str | None,
        warnings: list[str],
    ) -> HistoryPersistenceDecision:
        if "cancelled" in warnings:
            return SkipConversationHistory(reason="cancelled")
        if conversation_id:
            return PersistConversationHistory(conversation_id=conversation_id)
        return SkipConversationHistory(reason="missing_conversation_id")


class SessionFlowTransportFinalizationInputFactory:
    """Maps completed shared session flow into transport finalization input."""

    def build(
        self,
        *,
        request: ChatAskRequest,
        flow: CompletedSessionFlow,
    ) -> TransportFinalizationInput:
        transport_events: list[StreamEvent] = []
        journal_events = None
        if isinstance(flow.transport_artifact, StreamingRuntimeTransportArtifact):
            transport_events = list(flow.transport_artifact.events)
            journal_events = flow.transport_artifact.journal_events
        return TransportFinalizationInput(
            request=request,
            conversation_id=flow.session.conversation_id,
            completion_kind=flow.completion_kind,
            turn=flow.turn,
            llm_usages=flow.llm_usages,
            history_persistence=flow.history_persistence,
            transport_events=transport_events,
            journal_events=journal_events,
            persisted_user_message_id=flow.persisted_user_message_id,
        )


class TransportCompletionDiagnosticsHelper:
    """Builds normalized completion diagnostics from finalized assembled turns."""

    @staticmethod
    def build(
        *,
        finalization_input: TransportFinalizationInput,
        history_result: HistoryPersistenceResult,
    ) -> TransportCompletionDiagnostics:
        execution = finalization_input.turn.execution
        llm_usages = finalization_input.llm_usages or []
        total_tokens = 0
        total_duration_ms = 0.0
        for usage in llm_usages:
            if isinstance(usage, dict):
                total_tokens += usage.get("total_tokens", 0)
                total_duration_ms += usage.get("duration_ms", 0.0)
        return TransportCompletionDiagnostics(
            conversation_id=finalization_input.conversation_id,
            assistant_message_id=history_result.assistant_message_id,
            selected_database_connector=finalization_input.turn.selected_database_connector,
            generated_sql_present=bool(finalization_input.turn.generated_sql),
            execution_status=execution.status if execution is not None else None,
            warnings=list(finalization_input.turn.warnings),
            llm_calls=llm_usages,
            llm_total_tokens=total_tokens,
            llm_total_duration_ms=total_duration_ms,
        )


class PostAssemblyTransportFinalizationService(Generic[TTransportResult]):
    """Runs shared persistence and delegates final transport shaping to an adapter."""

    def __init__(
        self,
        *,
        history_persistence_service: ChatHistoryPersistenceService,
        adapter: TransportFinalizationAdapter[TTransportResult],
    ) -> None:
        self._history_persistence_service = history_persistence_service
        self._adapter = adapter

    def finalize(
        self,
        *,
        finalization_input: TransportFinalizationInput,
    ) -> TTransportResult:
        self._persist_token_usage(finalization_input=finalization_input)
        history_result = self._persist_history(finalization_input=finalization_input)
        return self._adapter.build_result(
            finalization_input=finalization_input,
            history_result=history_result,
        )

    def _persist_token_usage(
        self,
        *,
        finalization_input: TransportFinalizationInput,
    ) -> None:
        if not finalization_input.conversation_id or not finalization_input.llm_usages:
            return
        self._history_persistence_service.add_token_usage(
            conversation_id=finalization_input.conversation_id,
            llm_usages=finalization_input.llm_usages,
        )

    def _persist_history(
        self,
        *,
        finalization_input: TransportFinalizationInput,
    ) -> HistoryPersistenceResult:
        decision = finalization_input.history_persistence
        if not isinstance(decision, PersistConversationHistory):
            return HistoryPersistenceResult(decision=decision)

        assistant_message_id = self._history_persistence_service.save_turn(
            conversation_id=decision.conversation_id,
            request=finalization_input.request,
            turn=finalization_input.turn,
            journal_events=finalization_input.journal_events,
            persist_user_message=finalization_input.persisted_user_message_id is None,
            persisted_user_message_id=finalization_input.persisted_user_message_id,
        )
        return HistoryPersistenceResult(
            decision=decision,
            assistant_message_id=assistant_message_id,
        )


class SyncChatTransportAdapter:
    """Shapes the final sync chat response."""

    def __init__(
        self,
        *,
        diagnostics_helper: TransportCompletionDiagnosticsHelper | None = None,
    ) -> None:
        self._diagnostics_helper = (
            diagnostics_helper or TransportCompletionDiagnosticsHelper()
        )

    def build_result(
        self,
        *,
        finalization_input: TransportFinalizationInput,
        history_result: HistoryPersistenceResult,
    ) -> SyncTransportFinalizationResult:
        response = finalization_input.turn.to_chat_response(
            conversation_id=finalization_input.conversation_id,
            assistant_message_id=history_result.assistant_message_id,
        )
        return SyncTransportFinalizationResult(
            response=response,
            diagnostics=self._diagnostics_helper.build(
                finalization_input=finalization_input,
                history_result=history_result,
            )
        )


class StreamingChatTransportAdapter:
    """Shapes final streaming emission order and terminal event payload."""

    def __init__(
        self,
        *,
        diagnostics_helper: TransportCompletionDiagnosticsHelper | None = None,
    ) -> None:
        self._diagnostics_helper = (
            diagnostics_helper or TransportCompletionDiagnosticsHelper()
        )

    def build_result(
        self,
        *,
        finalization_input: TransportFinalizationInput,
        history_result: HistoryPersistenceResult,
    ) -> StreamingTransportFinalizationResult:
        if finalization_input.completion_kind == "llm_connector_missing":
            terminal_event = ErrorEvent(
                code="llm_connector_missing",
                message=finalization_input.turn.answer,
            )
            return StreamingTransportFinalizationResult(
                events=[terminal_event],
                terminal=StreamingErrorTerminalResult(event=terminal_event),
            )

        diagnostics = self._diagnostics_helper.build(
            finalization_input=finalization_input,
            history_result=history_result,
        )
        done_event = finalization_input.turn.to_done_event(
            conversation_id=finalization_input.conversation_id,
            assistant_message_id=history_result.assistant_message_id,
            cancelled=finalization_input.completion_kind == "cancelled",
        )
        return StreamingTransportFinalizationResult(
            events=[
                *finalization_input.transport_events,
                done_event,
            ],
            terminal=StreamingDoneTerminalResult(event=done_event),
            diagnostics=diagnostics,
        )
