"""Repair turn service for generating corrected answers based on negative feedback."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, ClassVar

from rhetorica_backend.contracts.chat import (
    FeedbackReason,
    RepairTurnRequest,
)


@dataclass(frozen=True)
class RepairPromptComponents:
    """Structured components for building repair prompt."""

    original_question: str
    original_answer: str
    feedback_reason: FeedbackReason
    feedback_comment: str | None
    context_tables: list[str]
    generated_sql: str | None
    executed_sql: str | None


class RepairTurnService:
    """Service for generating repair questions from negative feedback.

    This service transforms negative feedback into actionable repair prompts
    that can be sent to the chat orchestrator.
    """

    # Templates for different feedback reasons
    _REPAIR_PROMPT_TEMPLATES: ClassVar[dict[FeedbackReason, str]] = {
        FeedbackReason.WRONG_SOURCE: (
            "The previous answer used incorrect data sources. "
            "Context: original question about '{question}' answered with tables {tables}. "
            "The answer was marked wrong because wrong source was used. "
            "Original answer: {answer}. "
            "Please regenerate the answer using the correct data sources. "
            "SQL that was used: {sql}. "
            "Alternative approach: Consider whether the tables accurately represent the requested data. "
            "{comment}"
        ),
        FeedbackReason.WRONG_METRIC: (
            "The previous answer had incorrect calculations or metrics. "
            "Context: original question about '{question}' answered with tables {tables}. "
            "The answer was marked wrong because the metric or calculation was incorrect. "
            "Original answer: {answer}. "
            "SQL used: {sql}. "
            "Please regenerate the answer with correct calculations. "
            "Executed SQL: {executed_sql}. "
            "{comment}"
        ),
        FeedbackReason.INCOMPLETE: (
            "The previous answer was incomplete. "
            "Context: original question about '{question}'. "
            "Original answer: {answer}. "
            "Tables used: {tables}. "
            "Please provide a more complete answer that addresses all aspects of the question. "
            "{comment}"
        ),
        FeedbackReason.OTHER: (
            "The previous answer had issues that need correction. "
            "Context: original question about '{question}'. "
            "Original answer: {answer}. "
            "Tables used: {tables}. "
            "SQL: {sql}. "
            "Please correct the answer based on the following feedback: {comment}"
        ),
    }

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(__name__)

    def build_repair_question(self, request: RepairTurnRequest) -> str:
        """Build a repair question from the repair turn request.

        The repair question incorporates:
        - Original question
        - Original answer
        - Feedback reason and comment
        - Context tables
        - SQL that was used
        """
        components = RepairPromptComponents(
            original_question=request.original_question,
            original_answer=request.original_answer,
            feedback_reason=request.feedback_reason,
            feedback_comment=request.feedback_comment,
            context_tables=request.context_tables,
            generated_sql=request.generated_sql,
            executed_sql=request.executed_sql,
        )

        template = self._REPAIR_PROMPT_TEMPLATES.get(
            request.feedback_reason,
            self._REPAIR_PROMPT_TEMPLATES[FeedbackReason.OTHER],
        )

        repair_question = template.format(
            question=components.original_question,
            answer=components.original_answer,
            tables=", ".join(components.context_tables) if components.context_tables else "no tables",
            sql=components.generated_sql or components.executed_sql or "no SQL",
            executed_sql=components.executed_sql or "N/A",
            comment=f"Additional feedback: {components.feedback_comment}"
            if components.feedback_comment
            else "",
        )

        self._logger.debug(
            "repair_turn.question_built",
            extra={
                "original_question": components.original_question[:100],
                "feedback_reason": components.feedback_reason.value,
                "repair_question_preview": repair_question[:200],
            },
        )

        return repair_question

    def extract_repair_context(self, request: RepairTurnRequest) -> dict[str, Any]:
        """Extract structured repair context for logging/debugging."""
        return {
            "original_message_id": request.message_id,
            "conversation_id": request.conversation_id,
            "workspace_id": request.workspace_id,
            "original_question": request.original_question,
            "feedback_reason": request.feedback_reason.value,
            "feedback_comment": request.feedback_comment,
            "context_tables": request.context_tables,
            "has_generated_sql": request.generated_sql is not None,
            "has_executed_sql": request.executed_sql is not None,
            "evidence_count": len(request.evidence),
            "claims_count": len(request.claims),
        }
