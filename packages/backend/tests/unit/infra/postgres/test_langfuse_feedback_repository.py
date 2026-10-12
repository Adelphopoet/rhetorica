from __future__ import annotations

from rhetorica_backend.contracts.chat import AssistantFeedbackValue
from rhetorica_backend.infra.postgres.chat_models import LangfuseReviewOutboxOrm
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.human_review_feedback_service import (
    HumanReviewFeedbackService,
)
from rhetorica_backend.infra.postgres.langfuse_feedback_repository import (
    LangfuseFeedbackOutboxRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


def _prepared_repository(tmp_path):
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path / 'review.db'}")
    runtime.create_schema()
    chat = ChatRepository(db=runtime)
    conversation = chat.create_conversation(workspace_id="default", title="Review")
    assistant = chat.save_message(
        conversation_id=conversation.id,
        role="assistant",
        content="Answer",
    )
    chat.save_answer_trace(
        assistant_message_id=assistant.id,
        conversation_id=conversation.id,
        trace_id="a" * 32,
        tool_call_count=6,
        tool_error_count=1,
    )
    return conversation, assistant, LangfuseFeedbackOutboxRepository(db=runtime)


def test_claim_cas_leaves_newer_feedback_pending(tmp_path) -> None:
    conversation, assistant, repository = _prepared_repository(tmp_path)
    assert repository.enqueue_feedback(
        assistant_message_id=assistant.id,
        conversation_id=conversation.id,
        reaction=AssistantFeedbackValue.WRONG,
        reason=None,
        what_went_wrong="Wrong period",
        expected_answer="Correct period",
    )
    old_claim = repository.claim_pending_delivery()[0]

    assert repository.enqueue_feedback(
        assistant_message_id=assistant.id,
        conversation_id=conversation.id,
        reaction=AssistantFeedbackValue.USEFUL,
        reason=None,
        what_went_wrong=None,
        expected_answer=None,
    )

    assert repository.mark_delivery(
        outbox_id=old_claim.id,
        revision=old_claim.revision,
        claim_token=old_claim.claim_token,
        error=None,
    ) is False
    fresh_claim = repository.claim_pending_delivery()[0]
    assert fresh_claim.revision == old_claim.revision + 1
    assert fresh_claim.payload["reaction"] == "useful"


def test_priority_uses_answer_snapshot_not_conversation_evidence(tmp_path) -> None:
    conversation, assistant, repository = _prepared_repository(tmp_path)
    repository.enqueue_feedback(
        assistant_message_id=assistant.id,
        conversation_id=conversation.id,
        reaction=AssistantFeedbackValue.WRONG,
        reason=None,
        what_went_wrong="Wrong period",
        expected_answer="Correct period",
    )

    claim = repository.claim_pending_delivery()[0]
    evidence = claim.payload["priority_evidence"]
    assert evidence == {
        "conversation_message_count": 1,
        "tool_call_count": 6,
        "tool_error_count": 1,
        "policy_version": "v1",
    }


def test_feedback_and_outbox_projection_commit_together(tmp_path) -> None:
    conversation, assistant, repository = _prepared_repository(tmp_path)
    runtime = repository._db
    saved = HumanReviewFeedbackService(db=runtime).save(
        assistant_message_id=assistant.id,
        conversation_id=conversation.id,
        owner_user_id=conversation.owner_user_id,
        reaction=AssistantFeedbackValue.WRONG,
        reason=None,
        comment=None,
        what_went_wrong="Wrong period",
        expected_answer="Correct period",
    )

    assert saved is not None
    assert saved.message.feedback == "wrong"
    assert saved.queued_for_langfuse is True
    with runtime.session() as session:
        row = session.query(LangfuseReviewOutboxOrm).one()
        assert row.payload["what_went_wrong"] == "Wrong period"
