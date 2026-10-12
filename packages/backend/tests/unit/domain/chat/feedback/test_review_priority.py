from rhetorica_backend.domain.chat.feedback.review_priority import (
    LangfuseReviewPriority,
    ReviewPriorityPolicy,
)


def test_priority_uses_only_persisted_conversation_and_tool_counts() -> None:
    policy = ReviewPriorityPolicy()

    low, low_evidence = policy.evaluate(
        conversation_message_count=1,
        tool_call_count=0,
        tool_error_count=0,
    )
    high, _ = policy.evaluate(
        conversation_message_count=0,
        tool_call_count=6,
        tool_error_count=0,
    )
    critical, _ = policy.evaluate(
        conversation_message_count=0,
        tool_call_count=0,
        tool_error_count=2,
    )

    assert low is LangfuseReviewPriority.LOW
    assert low_evidence.to_payload()["policy_version"] == "v1"
    assert high is LangfuseReviewPriority.HIGH
    assert critical is LangfuseReviewPriority.CRITICAL


def test_priority_clamps_invalid_counts_without_a_model_call() -> None:
    priority, evidence = ReviewPriorityPolicy().evaluate(
        conversation_message_count=-1,
        tool_call_count=-5,
        tool_error_count=-10,
    )

    assert priority is LangfuseReviewPriority.LOW
    assert evidence.conversation_message_count == 0
    assert evidence.tool_call_count == 0
    assert evidence.tool_error_count == 0
