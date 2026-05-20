"""Tests for Conversation Manager."""

from unittest.mock import MagicMock

import pytest
from rhetorica_backend.domain.chat.conversation.conversation_manager import (
    ConversationManager,
)


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
        )

    def test_create_conversation_no_repository(self):
        """Test conversation creation without repository."""
        manager = ConversationManager(chat_repository=None)
        result = manager.create_conversation(
            workspace_id="test-workspace",
            title="Test",
        )
        assert result is None

    def test_get_conversation_with_messages(
        self, conversation_manager, mock_repository
    ):
        """Test getting conversation with messages."""
        mock_conversation = MagicMock()
        mock_repository.get_conversation_with_messages.return_value = (
            mock_conversation
        )

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
            metadata_sources=["openmetadata"],
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
            metadata_sources=["openmetadata"],
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

    def test_format_history_for_context(self, conversation_manager, mock_repository):
        """Test formatting conversation history for LLM context."""
        mock_message1 = MagicMock()
        mock_message1.role = "user"
        mock_message1.content = "Hello"

        mock_message2 = MagicMock()
        mock_message2.role = "assistant"
        mock_message2.content = "Hi there!"

        mock_conversation = MagicMock()
        mock_conversation.messages = [mock_message1, mock_message2]
        mock_repository.get_conversation_with_messages.return_value = (
            mock_conversation
        )

        history = conversation_manager.format_history_for_context(
            conversation_id="test-id",
            message_limit=20,
        )

        assert len(history) == 2
        assert history[0] == {"role": "user", "content": "Hello"}
        assert history[1] == {"role": "assistant", "content": "Hi there!"}

    def test_format_history_no_repository(self, conversation_manager):
        """Test formatting history without repository."""
        conversation_manager._chat_repository = None
        history = conversation_manager.format_history_for_context(
            conversation_id="test-id",
        )
        assert history == []

    def test_format_history_conversation_not_found(
        self, conversation_manager, mock_repository
    ):
        """Test formatting history when conversation not found."""
        mock_repository.get_conversation_with_messages.return_value = None
        history = conversation_manager.format_history_for_context(
            conversation_id="non-existent-id",
        )
        assert history == []

    def test_format_history_includes_context_warnings_and_digest(
        self, conversation_manager, mock_repository
    ):
        """Test context builder emits warnings and digest under budget logic."""
        mock_message1 = MagicMock()
        mock_message1.role = "user"
        mock_message1.content = "A" * 6_000
        mock_message1.generated_sql = None
        mock_message1.execution_id = None
        mock_message1.selected_connector = None
        mock_message1.context_tables = []
        mock_message1.warnings = []

        mock_message2 = MagicMock()
        mock_message2.role = "assistant"
        mock_message2.content = "ok"
        mock_message2.generated_sql = "SELECT 1"
        mock_message2.execution_id = "exec-1"
        mock_message2.selected_connector = "clickhouse-prod"
        mock_message2.context_tables = ["svc.db.schema.sales"]
        mock_message2.capability_trace = {
            "runtime_context": {
                "metadata_table_columns": {
                    "svc.db.schema.sales": ["order_id", "gmv", "created_at"]
                },
                "semantic_grounding": {
                    "chosen_table_fqn": "svc.db.schema.sales",
                    "selection_rationale": "Selected `svc.db.schema.sales` because it has strong governance signals.",
                    "candidates": [
                        {
                            "fqn": "svc.db.schema.sales",
                            "service": "svc",
                            "database": "db",
                            "schema": "schema",
                            "relevance_score": 99.0,
                            "matched_on": ["name_exact"],
                            "reason_codes": ["governance_strength"],
                            "certification": "Certification.Gold",
                            "tier": "Tier.Tier1",
                            "owners": ["analytics"],
                            "domains": ["sales"],
                            "usage_count": 120,
                            "suspicious": False,
                        }
                    ],
                },
            }
        }
        mock_message2.warnings = []

        mock_conversation = MagicMock()
        mock_conversation.messages = [mock_message1, mock_message2]
        mock_repository.get_conversation_with_messages.return_value = (
            mock_conversation
        )

        history, warnings = conversation_manager.format_history_for_context(
            conversation_id="test-id",
            include_warnings=True,
            model_context_window_tokens=2_048,
        )

        assert isinstance(history, list)
        assert isinstance(warnings, list)
        assert "context_compacted" in warnings
        assert any("Context digest:" in item["content"] for item in history)
        assert any("table_columns.svc.db.schema.sales=created_at|gmv|order_id" in item["content"] for item in history)
        assert any("semantic_chosen_table=svc.db.schema.sales" in item["content"] for item in history)

    def test_format_history_warns_when_context_window_is_unconfirmed(
        self, conversation_manager, mock_repository
    ):
        mock_message = MagicMock()
        mock_message.role = "user"
        mock_message.content = "Hello"
        mock_message.generated_sql = None
        mock_message.execution_id = None
        mock_message.selected_connector = None
        mock_message.context_tables = []
        mock_message.warnings = []

        mock_conversation = MagicMock()
        mock_conversation.messages = [mock_message]
        mock_repository.get_conversation_with_messages.return_value = mock_conversation

        _history, warnings = conversation_manager.format_history_for_context(
            conversation_id="test-id",
            model_name="local/unknown-model",
            include_warnings=True,
        )

        assert "context_window_unconfirmed" in warnings

    def test_format_history_uses_persisted_semantic_state_after_long_history_compaction(
        self, conversation_manager, mock_repository
    ):
        messages = []
        for index in range(55):
            msg = MagicMock()
            msg.role = "user" if index % 2 == 0 else "assistant"
            msg.content = f"turn {index}"
            msg.generated_sql = None
            msg.execution_id = None
            msg.selected_connector = None
            msg.context_tables = []
            msg.warnings = []
            msg.capability_trace = {}
            messages.append(msg)

        mock_conversation = MagicMock()
        mock_conversation.messages = messages[-20:]
        mock_conversation.conversation_state = {
            "active_metrics": ["revenue"],
            "time_range": "2026-Q1",
            "selected_connector": "clickhouse-prod",
            "last_successful_execution_id": "exec-50",
            "state_revision": 50,
            "transitions": ["metric_added:revenue"],
        }
        mock_repository.get_conversation_with_messages.return_value = mock_conversation

        history = conversation_manager.format_history_for_context(
            conversation_id="conv-1",
            message_limit=20,
        )

        digest = "\n".join(item["content"] for item in history)
        assert "active_metrics=revenue" in digest
        assert "time_range=2026-Q1" in digest
        assert "session_selected_connector=clickhouse-prod" in digest
        assert "last_successful_execution_id=exec-50" in digest

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

    def test_update_conversation_state_overrides_conflicting_recent_time_range(
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
        assert saved_state["active_metrics"] == ["revenue"]
        assert saved_state["time_range"] == "2026-04-25"
        assert saved_state["state_revision"] == 8

    def test_format_history_digest_prioritizes_recent_table_columns(
        self, conversation_manager, mock_repository
    ):
        earlier = MagicMock()
        earlier.role = "assistant"
        earlier.content = "definition"
        earlier.generated_sql = None
        earlier.execution_id = None
        earlier.selected_connector = "clickhouse"
        earlier.context_tables = [
            "Sample-ClickHouse.default.dm.ft_order",
            "Sample-ClickHouse.default.imp_prod.ft_order",
            "Sample-ClickHouse.default.dm.ft_order_sber",
            "Sample-ClickHouse.default.dm.ft_order_rte",
            "Sample-ClickHouse.default.dm.ft_order_oms",
        ]
        earlier.capability_trace = {
            "runtime_context": {
                "metadata_table_columns": {
                    "sample-clickhouse.default.dm.ft_order": ["order_id"],
                    "sample-clickhouse.default.imp_prod.ft_order": ["order_id"],
                }
            }
        }
        earlier.warnings = []

        later = MagicMock()
        later.role = "assistant"
        later.content = "preview"
        later.generated_sql = "SELECT * FROM dm.ft_order LIMIT 5"
        later.execution_id = "exec-1"
        later.selected_connector = "clickhouse"
        later.context_tables = [
            "Sample-ClickHouse.default.dm.ft_order",
            "Sample-ClickHouse.default.imp_prod.ft_order",
            "Sample-ClickHouse.default.dm.ft_order_sber",
            "Sample-ClickHouse.default.dm.ft_order_rte",
            "Sample-ClickHouse.default.dm.ft_order_oms",
            "dm.ft_order",
        ]
        later.capability_trace = {
            "runtime_context": {
                "metadata_table_columns": {
                    "dm.ft_order": ["order_create_dttm", "order_id"],
                }
            }
        }
        later.warnings = []

        mock_conversation = MagicMock()
        mock_conversation.messages = [earlier, later]
        mock_repository.get_conversation_with_messages.return_value = mock_conversation

        history = conversation_manager.format_history_for_context(
            conversation_id="test-id",
            message_limit=20,
        )

        digest = next(item["content"] for item in history if "Context digest:" in item["content"])
        assert "recent_tables=dm.ft_order," in digest
        assert "table_columns.dm.ft_order=order_create_dttm|order_id" in digest
