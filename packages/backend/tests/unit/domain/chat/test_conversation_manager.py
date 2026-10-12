"""Tests for Conversation Manager."""

from unittest.mock import MagicMock

import pytest
from rhetorica_backend.domain.chat.conversation.conversation_manager import (
    ConversationManager,
)
from rhetorica_backend.domain.chat.token_usage import ConversationTokenUsage


@pytest.fixture
def mock_repository():
    """Create mock chat repository."""
    return MagicMock()


@pytest.fixture
def conversation_manager(mock_repository):
    """Create conversation manager with mock repository."""
    return ConversationManager(chat_repository=mock_repository)


class TestConversationManager:
    """Tests for ConversationManager."""

    def test_create_conversation(self, conversation_manager, mock_repository):
        """Test conversation creation."""
        mock_conversation = MagicMock()
        mock_conversation.id = "test-conversation-id"
        mock_repository.create_conversation.return_value = mock_conversation

        result = conversation_manager.create_conversation(
            workspace_id="test-workspace",
            title="Test Conversation",
        )

        assert result is not None
        assert result.id == "test-conversation-id"
        mock_repository.create_conversation.assert_called_once_with(
            workspace_id="test-workspace",
            title="Test Conversation",
            owner_user_id=None,
            agent_id=None,
        )

    def test_create_conversation_no_repository(self):
        """Test conversation creation without repository."""
        manager = ConversationManager(chat_repository=None)
        result = manager.create_conversation(
            workspace_id="test-workspace",
            title="Test",
        )
        assert result is None

    def test_get_conversation_with_messages(self, conversation_manager, mock_repository):
        """Test getting conversation with messages."""
        mock_conversation = MagicMock()
        mock_repository.get_conversation_with_messages.return_value = mock_conversation

        result = conversation_manager.get_conversation_with_messages(
            conversation_id="test-id",
            message_limit=10,
        )

        assert result == mock_conversation
        mock_repository.get_conversation_with_messages.assert_called_once_with(
            conversation_id="test-id",
            message_limit=10,
            owner_user_id=None,
        )

    def test_get_conversation_no_repository(self):
        """Test getting conversation without repository."""
        manager = ConversationManager(chat_repository=None)
        result = manager.get_conversation_with_messages(
            conversation_id="test-id",
        )
        assert result is None

    def test_save_message(self, conversation_manager, mock_repository):
        """Test saving message to conversation."""
        conversation_manager.save_message(
            conversation_id="test-id",
            role="user",
            content="Hello",
            generated_sql="SELECT 1",
            execution_id="exec-123",
            context_tables=["table1", "table2"],
            metadata_sources=["mcp"],
            selected_connector="db1",
            warnings=["warning1"],
        )

        mock_repository.save_message.assert_called_once_with(
            conversation_id="test-id",
            role="user",
            content="Hello",
            generated_sql="SELECT 1",
            execution_id="exec-123",
            context_tables=["table1", "table2"],
            metadata_sources=["mcp"],
            claims=[],
            evidence=[],
            claim_evidence_links=[],
            uncertainty_flags=[],
            capability_trace={},
            optimization_trace={},
            selected_connector="db1",
            analysis_document=None,
            visualization_spec=None,
            warnings=["warning1"],
            owner_user_id=None,
            agent_id=None,
            source_provider=None,
            source_message_id=None,
            journal_events=None,
        )

    def test_save_message_no_repository(self):
        """Test saving message without repository."""
        manager = ConversationManager(chat_repository=None)
        # Should not raise exception
        manager.save_message(
            conversation_id="test-id",
            role="user",
            content="Hello",
        )

    def test_explicit_reset_clears_agent_owned_tool_memory(self, mock_repository):
        agent_session_store = MagicMock()
        conversation_manager = ConversationManager(
            chat_repository=mock_repository,
            agent_session_store=agent_session_store,
        )
        user_message = MagicMock(
            role="user",
            content="reset context",
            generated_sql=None,
            execution_id=None,
        )
        mock_repository.get_conversation_state.return_value = {}
        mock_repository.update_conversation_state.return_value = {"state_revision": 1}

        conversation_manager.update_conversation_state_from_turn(
            conversation_id="conv-1",
            messages=[user_message],
        )

        mock_repository.clear_tool_evidence.assert_called_once_with(
            conversation_id="conv-1",
            owner_user_id=None,
        )
        mock_repository.clear_tool_artifacts.assert_called_once_with(
            conversation_id="conv-1",
            owner_user_id=None,
        )
        agent_session_store.clear_session.assert_called_once_with("conv-1")

    def test_update_conversation_state_from_turn_resets_without_deleting_history(
        self, conversation_manager, mock_repository
    ):
        user_message = MagicMock()
        user_message.role = "user"
        user_message.content = "reset context"
        user_message.generated_sql = None
        user_message.execution_id = None
        user_message.selected_connector = None
        user_message.context_tables = []
        user_message.warnings = []

        assistant_message = MagicMock()
        assistant_message.role = "assistant"
        assistant_message.content = "ok"
        assistant_message.generated_sql = None
        assistant_message.execution_id = None
        assistant_message.selected_connector = None
        assistant_message.context_tables = []
        assistant_message.warnings = []
        mock_repository.get_conversation_state.return_value = {
            "active_metrics": ["old_revenue"],
            "state_revision": 10,
        }
        mock_repository.update_conversation_state.return_value = {"state_revision": 1}

        result = conversation_manager.update_conversation_state_from_turn(
            conversation_id="conv-1",
            messages=[user_message, assistant_message],
        )

        assert result == {"state_revision": 1}
        saved_state = mock_repository.update_conversation_state.call_args.kwargs[
            "conversation_state"
        ]
        assert "old_revenue" not in saved_state.get("active_metrics", [])
        assert "reset_by_user_command" in saved_state["transitions"]

    def test_update_conversation_state_drops_legacy_semantic_fields(
        self, conversation_manager, mock_repository
    ):
        user_message = MagicMock()
        user_message.role = "user"
        user_message.content = "show revenue for 2026-04-25"
        user_message.generated_sql = None
        user_message.execution_id = None
        user_message.selected_connector = None
        user_message.context_tables = []
        user_message.warnings = []
        mock_repository.get_conversation_state.return_value = {
            "active_metrics": ["revenue"],
            "time_range": "2026-Q1",
            "state_revision": 7,
        }
        mock_repository.update_conversation_state.return_value = {"state_revision": 8}

        conversation_manager.update_conversation_state_from_turn(
            conversation_id="conv-1",
            messages=[user_message],
        )

        saved_state = mock_repository.update_conversation_state.call_args.kwargs[
            "conversation_state"
        ]
        assert "active_metrics" not in saved_state
        assert "time_range" not in saved_state
        assert saved_state["state_revision"] == 8

    def test_add_token_usage_uses_only_current_flow_usage(
        self, conversation_manager, mock_repository
    ):
        result = conversation_manager.add_token_usage(
            conversation_id="conv-1",
            llm_usages=[
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "cached_input_tokens": 80,
                    "cache_write_input_tokens": 10,
                    "cache_usage_reported": True,
                },
                {"input_tokens": 30, "output_tokens": 5},
            ],
        )

        expected = ConversationTokenUsage(
            input_tokens=130,
            output_tokens=25,
            cached_input_tokens=80,
            cache_write_input_tokens=10,
            cache_usage_reported_calls=1,
        )
        assert result == expected
        call = mock_repository.add_token_usage.call_args.kwargs
        assert call["token_usage"] == expected
