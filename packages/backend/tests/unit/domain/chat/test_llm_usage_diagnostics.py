"""Tests for LLM usage tracking in diagnostics and persistence."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from rhetorica_backend.contracts.chat import (
    CapabilityTrace,
    ChatAskRequest,
    OptimizationTrace,
)
from rhetorica_backend.domain.chat.conversation.conversation_manager import (
    ConversationManager,
)
from rhetorica_backend.domain.chat.llm.llm_client import LlmCallUsage
from rhetorica_backend.domain.chat.orchestration.models import (
    PersistConversationHistory,
    TransportFinalizationInput,
)
from rhetorica_backend.domain.chat.orchestration.response_assembly import (
    AssembledChatTurn,
    ChatHistoryPersistenceService,
    ChatResponseAssembler,
)
from rhetorica_backend.domain.chat.orchestration.transport_finalization import (
    TransportCompletionDiagnosticsHelper,
)


class TestDiagnosticsAggregation:
    """Tests for LLM usage aggregation in diagnostics."""

    def test_diagnostics_aggregates_single_usage(self):
        """Test aggregation with a single LLM call."""
        turn = AssembledChatTurn(
            answer="Test answer",
            generated_sql="SELECT 1",
            selected_database_connector="test-connector",
            metadata_sources=[],
            context_tables=[],
            runtime_context={},
            claims=[],
            evidence=[],
            claim_evidence_links=[],
            uncertainty_flags=[],
            capability_trace=CapabilityTrace(),
            optimization_trace=OptimizationTrace(),
            execution=None,
            analysis_document=None,
            visualization=None,
            warnings=[],
            llm_usages=[],
        )
        llm_usages = [
            {
                                "model": "gpt-4",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "duration_ms": 500.0,
                "retries": 0,
            }
        ]

        finalization_input = TransportFinalizationInput(
            request=ChatAskRequest(question="test", workspace_id="ws-1"),
            conversation_id="conv-1",
            completion_kind="runtime_completed",
            turn=turn,
            llm_usages=llm_usages,
            history_persistence=PersistConversationHistory(conversation_id="conv-1"),
            transport_events=[],
        )
        history_result = SimpleNamespace(assistant_message_id="msg-1")

        diagnostics = TransportCompletionDiagnosticsHelper.build(
            finalization_input=finalization_input,
            history_result=history_result,
        )

        assert diagnostics.conversation_id == "conv-1"
        assert diagnostics.assistant_message_id == "msg-1"
        assert diagnostics.llm_total_tokens == 150
        assert diagnostics.llm_total_duration_ms == 500.0
        assert len(diagnostics.llm_calls) == 1
        assert diagnostics.llm_calls[0]["model"] == "gpt-4"
        assert diagnostics.llm_calls[0]["total_tokens"] == 150

    def test_diagnostics_aggregates_multiple_usages(self):
        """Test aggregation with multiple LLM calls (tool loop, finalizer, repair)."""
        turn = AssembledChatTurn(
            answer="Test answer",
            generated_sql="SELECT 1",
            selected_database_connector="test-connector",
            metadata_sources=[],
            context_tables=[],
            runtime_context={},
            claims=[],
            evidence=[],
            claim_evidence_links=[],
            uncertainty_flags=[],
            capability_trace=CapabilityTrace(),
            optimization_trace=OptimizationTrace(),
            execution=None,
            analysis_document=None,
            visualization=None,
            warnings=[],
            llm_usages=[],
        )
        llm_usages = [
            {
                                "model": "gpt-4",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "duration_ms": 500.0,
                "retries": 0,
            },
            {
                                "model": "gpt-4",
                "prompt_tokens": 200,
                "completion_tokens": 100,
                "total_tokens": 300,
                "duration_ms": 800.0,
                "retries": 1,
            },
            {
                                "model": "gpt-4",
                "prompt_tokens": 300,
                "completion_tokens": 150,
                "total_tokens": 450,
                "duration_ms": 600.0,
                "retries": 0,
            },
        ]

        finalization_input = TransportFinalizationInput(
            request=ChatAskRequest(question="test", workspace_id="ws-1"),
            conversation_id="conv-1",
            completion_kind="runtime_completed",
            turn=turn,
            llm_usages=llm_usages,
            history_persistence=PersistConversationHistory(conversation_id="conv-1"),
            transport_events=[],
        )
        history_result = SimpleNamespace(assistant_message_id="msg-1")

        diagnostics = TransportCompletionDiagnosticsHelper.build(
            finalization_input=finalization_input,
            history_result=history_result,
        )

        # Aggregated totals
        assert diagnostics.llm_total_tokens == 150 + 300 + 450  # 900
        assert diagnostics.llm_total_duration_ms == 500.0 + 800.0 + 600.0  # 1900.0
        assert len(diagnostics.llm_calls) == 3

    def test_diagnostics_empty_usages(self):
        """Test diagnostics with no LLM usages."""
        turn = AssembledChatTurn(
            answer="Test answer",
            generated_sql="SELECT 1",
            selected_database_connector="test-connector",
            metadata_sources=[],
            context_tables=[],
            runtime_context={},
            claims=[],
            evidence=[],
            claim_evidence_links=[],
            uncertainty_flags=[],
            capability_trace=CapabilityTrace(),
            optimization_trace=OptimizationTrace(),
            execution=None,
            analysis_document=None,
            visualization=None,
            warnings=[],
            llm_usages=[],
        )
        finalization_input = TransportFinalizationInput(
            request=ChatAskRequest(question="test", workspace_id="ws-1"),
            conversation_id="conv-1",
            completion_kind="runtime_completed",
            turn=turn,
            llm_usages=[],
            history_persistence=PersistConversationHistory(conversation_id="conv-1"),
            transport_events=[],
        )
        history_result = SimpleNamespace(assistant_message_id="msg-1")

        diagnostics = TransportCompletionDiagnosticsHelper.build(
            finalization_input=finalization_input,
            history_result=history_result,
        )

        assert diagnostics.llm_total_tokens == 0
        assert diagnostics.llm_total_duration_ms == 0.0
        assert len(diagnostics.llm_calls) == 0

    def test_diagnostics_with_none_usages(self):
        """Test diagnostics with None LLM usages (not a valid input, should use empty list)."""
        # Pydantic model requires llm_usages to be a list, not None.
        # This test documents that None is not allowed and empty list should be used.
        turn = AssembledChatTurn(
            answer="Test answer",
            generated_sql="SELECT 1",
            selected_database_connector="test-connector",
            metadata_sources=[],
            context_tables=[],
            runtime_context={},
            claims=[],
            evidence=[],
            claim_evidence_links=[],
            uncertainty_flags=[],
            capability_trace=CapabilityTrace(),
            optimization_trace=OptimizationTrace(),
            execution=None,
            analysis_document=None,
            visualization=None,
            warnings=[],
            llm_usages=[],
        )
        # Use empty list instead of None for llm_usages
        finalization_input = TransportFinalizationInput(
            request=ChatAskRequest(question="test", workspace_id="ws-1"),
            conversation_id="conv-1",
            completion_kind="runtime_completed",
            turn=turn,
            llm_usages=[],
            history_persistence=PersistConversationHistory(conversation_id="conv-1"),
            transport_events=[],
        )
        history_result = SimpleNamespace(assistant_message_id="msg-1")

        diagnostics = TransportCompletionDiagnosticsHelper.build(
            finalization_input=finalization_input,
            history_result=history_result,
        )

        assert diagnostics.llm_total_tokens == 0
        assert len(diagnostics.llm_calls) == 0


class TestPersistence:
    """Tests for LLM usage persistence."""

    def test_assembled_turn_includes_llm_usages(self):
        """Test that AssembledChatTurn includes llm_usages."""
        usages = [
            {
                                "model": "gpt-4",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "duration_ms": 500.0,
                "retries": 0,
            }
        ]
        turn = AssembledChatTurn(
            answer="Test answer",
            generated_sql="SELECT 1",
            selected_database_connector="test-connector",
            metadata_sources=[],
            context_tables=[],
            runtime_context={},
            claims=[],
            evidence=[],
            claim_evidence_links=[],
            uncertainty_flags=[],
            capability_trace=CapabilityTrace(),
            optimization_trace=OptimizationTrace(),
            execution=None,
            analysis_document=None,
            visualization=None,
            warnings=[],
            llm_usages=usages,
        )
        assert turn.llm_usages == usages

    def test_persist_llm_usages_to_conversation_state(self):
        """Test that llm_usages are persisted to conversation_state."""
        saved_conversation_state = {}
        update_called = False
        update_call_kwargs = {}
        saved_assistant = {}

        def mock_save_message(**kwargs):
            if kwargs["role"] == "assistant":
                saved_assistant.update(kwargs)
                return SimpleNamespace(id="assistant-1")
            return SimpleNamespace(id="user-1")

        def mock_update_conversation_state(
            conversation_id, messages, conversation_state=None, **kwargs
        ):
            nonlocal saved_conversation_state, update_called, update_call_kwargs
            update_called = True
            update_call_kwargs = kwargs
            if conversation_state is not None:
                saved_conversation_state = conversation_state
            return conversation_state if conversation_state else {}

        conversation_manager = MagicMock(spec=ConversationManager)
        conversation_manager.save_message = mock_save_message
        conversation_manager.update_conversation_state_from_turn = mock_update_conversation_state

        persistence_service = ChatHistoryPersistenceService(
            conversation_manager=conversation_manager
        )
        llm_usages = [
            {
                                "model": "gpt-4",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "duration_ms": 500.0,
                "retries": 0,
            }
        ]
        turn = AssembledChatTurn(
            answer="Test answer",
            generated_sql="SELECT 1",
            selected_database_connector="test-connector",
            metadata_sources=[],
            context_tables=[],
            runtime_context={},
            claims=[],
            evidence=[],
            claim_evidence_links=[],
            uncertainty_flags=[],
            capability_trace=CapabilityTrace(),
            optimization_trace=OptimizationTrace(),
            execution=None,
            analysis_document=None,
            visualization=None,
            warnings=[],
            llm_usages=llm_usages,
        )
        request = ChatAskRequest(question="test question", workspace_id="ws-1")

        result = persistence_service.save_turn(
            conversation_id="conv-1",
            request=request,
            turn=turn,
        )

        assert result == "assistant-1"
        assert saved_assistant["capability_trace"]["runtime_context"]["llm_calls"] == llm_usages
        assert update_called, "update_conversation_state_from_turn should have been called"
        assert "llm_usages" in update_call_kwargs
        assert len(update_call_kwargs["llm_usages"]) == 1
        assert update_call_kwargs["llm_usages"][0]["model"] == "gpt-4"
        assert update_call_kwargs["llm_usages"][0]["total_tokens"] == 150


class TestSerializeLlmUsages:
    """Tests for LLM usage serialization helper."""

    def test_serialize_llm_usage_dataclass(self):
        """Test serializing LlmCallUsage dataclass."""
        usage = LlmCallUsage(
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            duration_ms=500.0,
            retries=0,
        )
        result = ChatResponseAssembler._serialize_llm_usages([usage])
        assert len(result) == 1
        assert result[0]["model"] == "gpt-4"
        assert result[0]["total_tokens"] == 150

    def test_serialize_llm_usage_dict(self):
        """Test serializing dict-style usage."""
        usage = {
            "model": "gpt-4",
            "prompt_tokens": 200,
            "completion_tokens": 100,
            "total_tokens": 300,
            "duration_ms": 1000.0,
            "retries": 1,
        }
        result = ChatResponseAssembler._serialize_llm_usages([usage])
        assert len(result) == 1
        assert result[0]["total_tokens"] == 300

    def test_serialize_mixed_usages(self):
        """Test serializing mixed usage types."""
        dataclass_usage = LlmCallUsage(
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            duration_ms=500.0,
            retries=0,
        )
        dict_usage = {
            "model": "gpt-4",
            "prompt_tokens": 200,
            "completion_tokens": 100,
            "total_tokens": 300,
            "duration_ms": 1000.0,
            "retries": 1,
        }
        result = ChatResponseAssembler._serialize_llm_usages([dataclass_usage, dict_usage])
        assert len(result) == 2

    def test_serialize_none_usages(self):
        """Test serializing None usages."""
        result = ChatResponseAssembler._serialize_llm_usages(None)
        assert result == []

    def test_serialize_empty_usages(self):
        """Test serializing empty usages list."""
        result = ChatResponseAssembler._serialize_llm_usages([])
        assert result == []
