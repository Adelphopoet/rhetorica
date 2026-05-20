from __future__ import annotations

from rhetorica_backend.contracts.chat import (
    AnswerClaim,
    AnswerEvidence,
    ClaimEvidenceLink,
    EvidenceType,
    FeedbackReason,
    RepairTurnRequest,
)
from rhetorica_backend.domain.chat.feedback.repair_turn_service import RepairTurnService


class TestRepairTurnService:
    """Test repair turn service."""

    def _make_request(
        self,
        feedback_reason: FeedbackReason = FeedbackReason.WRONG_SOURCE,
        comment: str | None = None,
    ) -> RepairTurnRequest:
        return RepairTurnRequest(
            message_id="msg-123",
            conversation_id="conv-456",
            workspace_id="default",
            original_question="Show me total sales by region",
            original_answer="Here are the sales by region...",
            generated_sql="SELECT region, SUM(amount) FROM sales GROUP BY region",
            executed_sql="SELECT region, SUM(amount) FROM sales GROUP BY region",
            context_tables=["analytics.dm.sales", "analytics.dm.regions"],
            metadata_sources=["openmetadata"],
            evidence=[
                AnswerEvidence(
                    evidence_id="ev-1",
                    type=EvidenceType.EXECUTION_RESULT,
                    reference="sales table",
                )
            ],
            claims=[
                AnswerClaim(claim_id="cl-1", text="Total sales")
            ],
            claim_evidence_links=[
                ClaimEvidenceLink(claim_id="cl-1", evidence_ids=["ev-1"])
            ],
            tool_trace=[],
            feedback_reason=feedback_reason,
            feedback_comment=comment,
        )

    def test_build_repair_question_for_wrong_source(self) -> None:
        """Repair question should address wrong data source."""
        service = RepairTurnService()
        request = self._make_request(
            feedback_reason=FeedbackReason.WRONG_SOURCE,
            comment="Use the sales_v2 table instead",
        )
        question = service.build_repair_question(request)

        assert "wrong source" in question.lower()
        assert "sales_v2" in question
        assert "sales by region" in question

    def test_build_repair_question_for_wrong_metric(self) -> None:
        """Repair question should address wrong calculation."""
        service = RepairTurnService()
        request = self._make_request(
            feedback_reason=FeedbackReason.WRONG_METRIC,
            comment="Should be AVG not SUM",
        )
        question = service.build_repair_question(request)

        assert "incorrect calculations" in question.lower() or "metric" in question.lower()
        assert "AVG" in question

    def test_build_repair_question_for_incomplete(self) -> None:
        """Repair question should address incomplete answer."""
        service = RepairTurnService()
        request = self._make_request(feedback_reason=FeedbackReason.INCOMPLETE)
        question = service.build_repair_question(request)

        assert "incomplete" in question.lower()
        assert "sales by region" in question

    def test_build_repair_question_for_other(self) -> None:
        """Repair question should include custom comment."""
        service = RepairTurnService()
        request = self._make_request(
            feedback_reason=FeedbackReason.OTHER,
            comment="The answer is missing March data",
        )
        question = service.build_repair_question(request)

        assert "issues" in question.lower()
        assert "March data" in question

    def test_extract_repair_context(self) -> None:
        """Repair context should contain all relevant data."""
        service = RepairTurnService()
        request = self._make_request(
            feedback_reason=FeedbackReason.WRONG_SOURCE,
            comment="Test comment",
        )
        context = service.extract_repair_context(request)

        assert context["original_message_id"] == "msg-123"
        assert context["conversation_id"] == "conv-456"
        assert context["workspace_id"] == "default"
        assert context["original_question"] == "Show me total sales by region"
        assert context["feedback_reason"] == "wrong_source"
        assert context["feedback_comment"] == "Test comment"
        assert context["context_tables"] == ["analytics.dm.sales", "analytics.dm.regions"]
        assert context["has_generated_sql"] is True
        assert context["has_executed_sql"] is True
        assert context["evidence_count"] == 1
        assert context["claims_count"] == 1
