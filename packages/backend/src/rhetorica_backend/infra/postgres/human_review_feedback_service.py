"""One transactional use case for user feedback and its Langfuse projection."""

from __future__ import annotations

from dataclasses import dataclass

from rhetorica_backend.contracts.chat import AssistantFeedbackValue, FeedbackReason
from rhetorica_backend.infra.postgres.chat_repository import ChatMessageItem, ChatRepository
from rhetorica_backend.infra.postgres.langfuse_feedback_repository import (
    LangfuseFeedbackOutboxRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


@dataclass(frozen=True, slots=True)
class SavedHumanReviewFeedback:
    message: ChatMessageItem
    queued_for_langfuse: bool


class HumanReviewFeedbackService:
    """Commit local feedback and its retryable external projection together."""

    def __init__(self, *, db: DatabaseRuntime) -> None:
        self._db = db
        self._chat = ChatRepository(db=db)
        self._outbox = LangfuseFeedbackOutboxRepository(db=db)

    def save(
        self,
        *,
        assistant_message_id: str,
        conversation_id: str,
        owner_user_id: str,
        reaction: AssistantFeedbackValue,
        reason: FeedbackReason | None,
        comment: str | None,
        what_went_wrong: str | None,
        expected_answer: str | None,
    ) -> SavedHumanReviewFeedback | None:
        """Persist all local state atomically; caller may enqueue worker after return."""
        with self._db.session() as session:
            message = self._chat.update_message_feedback_in_session(
                session=session,
                conversation_id=conversation_id,
                message_id=assistant_message_id,
                feedback=reaction.value,
                owner_user_id=owner_user_id,
                feedback_reason=reason.value if reason else None,
                feedback_comment=comment,
                feedback_what_went_wrong=what_went_wrong,
                feedback_expected_answer=expected_answer,
            )
            if message is None:
                session.rollback()
                return None
            queued = self._outbox.enqueue_feedback_in_session(
                session=session,
                assistant_message_id=assistant_message_id,
                conversation_id=conversation_id,
                reaction=reaction,
                reason=reason,
                what_went_wrong=what_went_wrong,
                expected_answer=expected_answer,
            )
            session.commit()
            return SavedHumanReviewFeedback(message=message, queued_for_langfuse=queued)
