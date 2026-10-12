"""Deterministic priority exported with feedback to Langfuse."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class LangfuseReviewPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class ReviewPriorityEvidence:
    conversation_message_count: int
    tool_call_count: int
    tool_error_count: int
    policy_version: str = "v1"

    def to_payload(self) -> dict[str, int | str]:
        return asdict(self)


class ReviewPriorityPolicy:
    """Classify a trace without an LLM from persisted execution evidence."""

    def evaluate(
        self,
        *,
        conversation_message_count: int,
        tool_call_count: int,
        tool_error_count: int,
    ) -> tuple[LangfuseReviewPriority, ReviewPriorityEvidence]:
        evidence = ReviewPriorityEvidence(
            conversation_message_count=max(0, conversation_message_count),
            tool_call_count=max(0, tool_call_count),
            tool_error_count=max(0, tool_error_count),
        )
        if evidence.tool_error_count >= 2:
            return LangfuseReviewPriority.CRITICAL, evidence
        if (
            evidence.tool_error_count >= 1
            or evidence.tool_call_count >= 6
            or evidence.conversation_message_count >= 16
        ):
            return LangfuseReviewPriority.HIGH, evidence
        if evidence.tool_call_count >= 3 or evidence.conversation_message_count >= 8:
            return LangfuseReviewPriority.NORMAL, evidence
        return LangfuseReviewPriority.LOW, evidence
