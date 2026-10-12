"""Transactional and concurrency-safe outbox persistence for Langfuse feedback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from rhetorica_backend.contracts.chat import AssistantFeedbackValue, FeedbackReason
from rhetorica_backend.domain.chat.feedback.review_priority import ReviewPriorityPolicy
from rhetorica_backend.infra.postgres.chat_models import (
    ChatAnswerTraceOrm,
    ChatMessageOrm,
    LangfuseReviewOutboxOrm,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

_PENDING = "pending"
_PROCESSING = "processing"
_RETRYABLE_ERROR = "retryable_error"
_DELIVERED = "delivered"
_CLAIM_TTL = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class ClaimedLangfuseFeedback:
    """A detached snapshot that a worker exclusively owns for one short lease."""

    id: str
    revision: int
    claim_token: str
    payload: dict[str, object]


class LangfuseFeedbackOutboxRepository:
    """Keeps feedback delivery retryable without a local review queue.

    The outbox is deliberately a latest-state projection. A revision and a
    delivery lease make that safe when feedback changes while a worker is
    publishing an older payload.
    """

    def __init__(self, *, db: DatabaseRuntime, priority_policy: ReviewPriorityPolicy | None = None) -> None:
        self._db = db
        self._priority_policy = priority_policy or ReviewPriorityPolicy()

    def enqueue_feedback(
        self,
        *,
        assistant_message_id: str,
        conversation_id: str,
        reaction: AssistantFeedbackValue,
        reason: FeedbackReason | None,
        what_went_wrong: str | None,
        expected_answer: str | None,
    ) -> bool:
        """Standalone compatibility entrypoint; commits one complete projection."""
        with self._db.session() as session:
            queued = self.enqueue_feedback_in_session(
                session=session,
                assistant_message_id=assistant_message_id,
                conversation_id=conversation_id,
                reaction=reaction,
                reason=reason,
                what_went_wrong=what_went_wrong,
                expected_answer=expected_answer,
            )
            session.commit()
            return queued

    def enqueue_feedback_in_session(
        self,
        *,
        session: Session,
        assistant_message_id: str,
        conversation_id: str,
        reaction: AssistantFeedbackValue,
        reason: FeedbackReason | None,
        what_went_wrong: str | None,
        expected_answer: str | None,
    ) -> bool:
        """Write the projection in a caller-owned transaction.

        `ChatAnswerTraceOrm` contains immutable per-answer tool counters, so
        priority cannot be affected by another turn in the conversation.
        """
        now = datetime.now(tz=UTC)
        trace = session.get(ChatAnswerTraceOrm, assistant_message_id)
        if trace is None:
            return False
        message_count = session.scalar(
            select(func.count(ChatMessageOrm.id)).where(ChatMessageOrm.conversation_id == conversation_id)
        ) or 0
        priority, evidence = self._priority_policy.evaluate(
            conversation_message_count=int(message_count),
            tool_call_count=trace.tool_call_count,
            tool_error_count=trace.tool_error_count,
        )
        payload: dict[str, object] = {
            "trace_id": trace.trace_id,
            "conversation_id": conversation_id,
            "score_id": f"rhetorica-feedback-{assistant_message_id}",
            "reaction": reaction.value,
            "reason": reason.value if reason else None,
            "is_negative": reaction != AssistantFeedbackValue.USEFUL,
            "what_went_wrong": what_went_wrong,
            "expected_answer": expected_answer,
            "priority": priority.value,
            "priority_evidence": evidence.to_payload(),
        }
        row = session.execute(
            select(LangfuseReviewOutboxOrm)
            .where(
                LangfuseReviewOutboxOrm.assistant_message_id == assistant_message_id,
                LangfuseReviewOutboxOrm.event_type == "feedback",
            )
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            session.add(
                LangfuseReviewOutboxOrm(
                    assistant_message_id=assistant_message_id,
                    event_type="feedback",
                    payload=payload,
                    delivery_state=_PENDING,
                    created_at=now,
                )
            )
            return True

        # Invalidate an older worker claim before making the latest payload available.
        row.payload = payload
        row.revision += 1
        row.delivery_state = _PENDING
        row.claim_token = None
        row.claimed_at = None
        row.attempts = 0
        row.last_error = None
        row.delivered_at = None
        return True

    def claim_pending_delivery(self, *, limit: int = 100) -> list[ClaimedLangfuseFeedback]:
        """Claim pending work without two workers publishing the same revision."""
        now = datetime.now(tz=UTC)
        stale_before = now - _CLAIM_TTL
        with self._db.session() as session:
            rows = list(
                session.execute(
                    select(LangfuseReviewOutboxOrm)
                    .where(
                        or_(
                            LangfuseReviewOutboxOrm.delivery_state.in_([_PENDING, _RETRYABLE_ERROR]),
                            and_(
                                LangfuseReviewOutboxOrm.delivery_state == _PROCESSING,
                                LangfuseReviewOutboxOrm.claimed_at < stale_before,
                            ),
                        )
                    )
                    .order_by(LangfuseReviewOutboxOrm.created_at.asc())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                ).scalars()
            )
            claims: list[ClaimedLangfuseFeedback] = []
            for row in rows:
                claim_token = str(uuid4())
                row.delivery_state = _PROCESSING
                row.claim_token = claim_token
                row.claimed_at = now
                row.attempts += 1
                claims.append(
                    ClaimedLangfuseFeedback(
                        id=row.id,
                        revision=row.revision,
                        claim_token=claim_token,
                        payload=dict(row.payload or {}),
                    )
                )
            session.commit()
            return claims

    def mark_delivery(
        self,
        *,
        outbox_id: str,
        revision: int,
        claim_token: str,
        error: str | None,
    ) -> bool:
        """Finish only the exact revision and lease that was claimed.

        A false return is expected for stale work: the newer revision remains
        pending and is delivered by a later worker.
        """
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            result = session.execute(
                update(LangfuseReviewOutboxOrm)
                .where(
                    LangfuseReviewOutboxOrm.id == outbox_id,
                    LangfuseReviewOutboxOrm.revision == revision,
                    LangfuseReviewOutboxOrm.claim_token == claim_token,
                    LangfuseReviewOutboxOrm.delivery_state == _PROCESSING,
                )
                .values(
                    delivery_state=_DELIVERED if error is None else _RETRYABLE_ERROR,
                    last_error=error,
                    delivered_at=now if error is None else None,
                    claim_token=None,
                    claimed_at=None,
                )
            )
            session.commit()
            return result.rowcount == 1
