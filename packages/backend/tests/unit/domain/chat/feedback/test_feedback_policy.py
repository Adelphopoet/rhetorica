from __future__ import annotations

from rhetorica_backend.contracts.chat import (
    AssistantFeedbackValue,
    FeedbackReason,
)
from rhetorica_backend.domain.chat.feedback import (
    FeedbackContext,
    FeedbackPipelineService,
    FeedbackPolicy,
    FeedbackRoutingAction,
)


class TestFeedbackPolicyValidation:
    """Test feedback request validation."""

    def test_negative_feedback_requires_reason(self) -> None:
        """Negative feedback types must specify a reason."""
        policy = FeedbackPolicy()

        # WRONG requires reason
        is_valid, error = policy.validate_feedback_request(
            feedback=AssistantFeedbackValue.WRONG,
            reason=None,
        )
        assert is_valid is False
        assert "reason" in error.lower()

        # INCOMPLETE requires reason
        is_valid, error = policy.validate_feedback_request(
            feedback=AssistantFeedbackValue.INCOMPLETE,
            reason=None,
        )
        assert is_valid is False

        # UNSAFE requires reason
        is_valid, error = policy.validate_feedback_request(
            feedback=AssistantFeedbackValue.UNSAFE,
            reason=None,
        )
        assert is_valid is False

    def test_useful_feedback_does_not_require_reason(self) -> None:
        """USEFUL feedback does not require a reason."""
        policy = FeedbackPolicy()
        is_valid, error = policy.validate_feedback_request(
            feedback=AssistantFeedbackValue.USEFUL,
            reason=None,
        )
        assert is_valid is True
        assert error == ""

    def test_negative_feedback_with_reason_is_valid(self) -> None:
        """Negative feedback with valid reason is accepted."""
        policy = FeedbackPolicy()
        is_valid, error = policy.validate_feedback_request(
            feedback=AssistantFeedbackValue.WRONG,
            reason=FeedbackReason.WRONG_SOURCE,
        )
        assert is_valid is True
        assert error == ""


class TestFeedbackPolicyRouting:
    """Test feedback routing decisions."""

    def _make_context(
        self,
        feedback: AssistantFeedbackValue = AssistantFeedbackValue.USEFUL,
        reason: FeedbackReason | None = None,
        context_tables: list[str] | None = None,
    ) -> FeedbackContext:
        return FeedbackContext(
            message_id="msg-123",
            conversation_id="conv-456",
            workspace_id="default",
            feedback=feedback,
            reason=reason,
            comment="Test comment",
            context_tables=context_tables or [],
            selected_connector="om",
        )

    def test_useful_feedback_routes_to_rag(self) -> None:
        """USEFUL feedback should be routed to RAG pipeline."""
        policy = FeedbackPolicy()
        context = self._make_context(feedback=AssistantFeedbackValue.USEFUL)
        decision = policy.decide_routing(context)

        assert FeedbackRoutingAction.RECORD_TO_RAG in decision.actions
        assert FeedbackRoutingAction.RECORD_NEGATIVE_METADATA_SIGNAL not in decision.actions
        assert FeedbackRoutingAction.TRIGGER_REPAIR_TURN not in decision.actions

    def test_wrong_feedback_with_source_reason_routes_to_metadata_signal(self) -> None:
        """WRONG feedback with WRONG_SOURCE reason should trigger metadata signal."""
        policy = FeedbackPolicy()
        context = self._make_context(
            feedback=AssistantFeedbackValue.WRONG,
            reason=FeedbackReason.WRONG_SOURCE,
            context_tables=["table1", "table2"],
        )
        decision = policy.decide_routing(context)

        assert FeedbackRoutingAction.RECORD_NEGATIVE_METADATA_SIGNAL in decision.actions
        assert FeedbackRoutingAction.TRIGGER_REPAIR_TURN in decision.actions
        assert decision.table_fqns_to_downrank == ["table1", "table2"]

    def test_wrong_feedback_with_metric_reason_routes_to_metadata_signal(self) -> None:
        """WRONG feedback with WRONG_METRIC reason should trigger metadata signal."""
        policy = FeedbackPolicy()
        context = self._make_context(
            feedback=AssistantFeedbackValue.WRONG,
            reason=FeedbackReason.WRONG_METRIC,
            context_tables=["table1"],
        )
        decision = policy.decide_routing(context)

        assert FeedbackRoutingAction.RECORD_NEGATIVE_METADATA_SIGNAL in decision.actions
        assert decision.table_fqns_to_downrank == ["table1"]

    def test_wrong_feedback_other_reason_has_signal_but_no_table_list(self) -> None:
        """WRONG feedback with OTHER reason triggers signal but no table downranking."""
        policy = FeedbackPolicy()
        context = self._make_context(
            feedback=AssistantFeedbackValue.WRONG,
            reason=FeedbackReason.OTHER,
            context_tables=["table1"],
        )
        decision = policy.decide_routing(context)

        # OTHER triggers metadata signal but with empty table list
        assert FeedbackRoutingAction.RECORD_NEGATIVE_METADATA_SIGNAL in decision.actions
        assert decision.table_fqns_to_downrank == []  # No specific tables to downrank

    def test_incomplete_feedback_triggers_repair_turn(self) -> None:
        """INCOMPLETE feedback should trigger repair turn."""
        policy = FeedbackPolicy()
        context = self._make_context(
            feedback=AssistantFeedbackValue.INCOMPLETE,
            reason=FeedbackReason.INCOMPLETE,
        )
        decision = policy.decide_routing(context)

        assert FeedbackRoutingAction.TRIGGER_REPAIR_TURN in decision.actions
        assert decision.repair_prompt_context is not None
        assert decision.repair_prompt_context["feedback_reason"] == "incomplete"

    def test_unsafe_feedback_is_logged_only(self) -> None:
        """UNSAFE feedback should not trigger any active routing."""
        policy = FeedbackPolicy()
        context = self._make_context(
            feedback=AssistantFeedbackValue.UNSAFE,
            reason=FeedbackReason.UNSAFE_SQL,
        )
        decision = policy.decide_routing(context)

        # UNSAFE should only be logged, not processed
        assert FeedbackRoutingAction.LOG_AND_IGNORE in decision.actions
        assert FeedbackRoutingAction.RECORD_TO_RAG not in decision.actions
        assert FeedbackRoutingAction.TRIGGER_REPAIR_TURN not in decision.actions

    def test_repair_context_contains_original_data(self) -> None:
        """Repair turn context should contain original message data."""
        policy = FeedbackPolicy()
        context = FeedbackContext(
            message_id="msg-123",
            conversation_id="conv-456",
            workspace_id="default",
            feedback=AssistantFeedbackValue.WRONG,
            reason=FeedbackReason.WRONG_SOURCE,
            comment="Should use different table",
            context_tables=["old.table"],
            selected_connector="om",
        )
        decision = policy.decide_routing(context)

        assert decision.repair_prompt_context is not None
        assert decision.repair_prompt_context["original_message_id"] == "msg-123"
        assert decision.repair_prompt_context["feedback_comment"] == "Should use different table"
        assert decision.repair_prompt_context["context_tables"] == ["old.table"]


class TestFeedbackPipelineService:
    """Test feedback pipeline service."""

    def test_process_feedback_validates_request(self) -> None:
        """Pipeline should validate feedback before processing."""
        service = FeedbackPipelineService()

        # Invalid request (negative feedback without reason)
        context = FeedbackContext(
            message_id="msg-123",
            conversation_id="conv-456",
            workspace_id="default",
            feedback=AssistantFeedbackValue.WRONG,
            reason=None,
            comment=None,
            context_tables=[],
            selected_connector=None,
        )
        decision = service.process_feedback(context)

        # Should log and ignore invalid request
        assert FeedbackRoutingAction.LOG_AND_IGNORE in decision.actions
        assert "validation_failed" in decision.reason

    def test_process_feedback_routes_correctly(self) -> None:
        """Pipeline should route valid feedback to correct handlers."""
        service = FeedbackPipelineService()

        context = FeedbackContext(
            message_id="msg-123",
            conversation_id="conv-456",
            workspace_id="default",
            feedback=AssistantFeedbackValue.WRONG,
            reason=FeedbackReason.WRONG_SOURCE,
            comment="Test",
            context_tables=["table1"],
            selected_connector="om",
        )
        decision = service.process_feedback(context)

        assert FeedbackRoutingAction.RECORD_NEGATIVE_METADATA_SIGNAL in decision.actions
        assert FeedbackRoutingAction.TRIGGER_REPAIR_TURN in decision.actions
        assert decision.table_fqns_to_downrank == ["table1"]
