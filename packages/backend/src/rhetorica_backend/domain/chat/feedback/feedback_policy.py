"""Typed policy for routing feedback to appropriate handlers.

This module provides explicit, deterministic feedback routing without heuristics.
Each feedback type maps to specific handlers through typed contracts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import ClassVar, Protocol

from rhetorica_backend.contracts.chat import (
    AssistantFeedbackValue,
    FeedbackReason,
)


class FeedbackRoutingAction(str, Enum):
    """Explicit routing actions for feedback processing."""

    RECORD_TO_RAG = "record_to_rag"
    RECORD_NEGATIVE_METADATA_SIGNAL = "record_negative_metadata_signal"
    TRIGGER_REPAIR_TURN = "trigger_repair_turn"
    LOG_AND_IGNORE = "log_and_ignore"


@dataclass(frozen=True)
class FeedbackContext:
    """Structured context for feedback processing."""

    message_id: str
    conversation_id: str
    workspace_id: str
    feedback: AssistantFeedbackValue
    reason: FeedbackReason | None
    comment: str | None
    context_tables: list[str]
    selected_connector: str | None


@dataclass(frozen=True)
class FeedbackRoutingDecision:
    """Explicit routing decision with typed actions."""

    actions: frozenset[FeedbackRoutingAction]
    table_fqns_to_downrank: list[str]
    repair_prompt_context: dict | None
    reason: str


class FeedbackPolicy:
    """Typed policy for routing feedback to appropriate handlers.

    This policy provides explicit, deterministic routing without heuristics.
    Each feedback type maps to specific actions through typed contracts.
    """

    # Mapping of feedback type -> expected reason requirement
    _REASON_REQUIRED_FOR: ClassVar[frozenset[AssistantFeedbackValue]] = frozenset({
        AssistantFeedbackValue.WRONG,
        AssistantFeedbackValue.UNSAFE,
        AssistantFeedbackValue.INCOMPLETE,
    })

    # Mapping of feedback type -> routing actions
    _FEEDBACK_ACTIONS: ClassVar[dict[AssistantFeedbackValue, frozenset[FeedbackRoutingAction]]] = {
        AssistantFeedbackValue.USEFUL: frozenset({FeedbackRoutingAction.RECORD_TO_RAG}),
        AssistantFeedbackValue.WRONG: frozenset({
            FeedbackRoutingAction.RECORD_NEGATIVE_METADATA_SIGNAL,
            FeedbackRoutingAction.TRIGGER_REPAIR_TURN,
        }),
        AssistantFeedbackValue.UNSAFE: frozenset({FeedbackRoutingAction.LOG_AND_IGNORE}),
        AssistantFeedbackValue.INCOMPLETE: frozenset({
            FeedbackRoutingAction.TRIGGER_REPAIR_TURN,
        }),
    }

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(__name__)

    def validate_feedback_request(
        self,
        feedback: AssistantFeedbackValue,
        reason: FeedbackReason | None,
    ) -> tuple[bool, str]:
        """Validate that feedback request is well-formed.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if feedback in self._REASON_REQUIRED_FOR and reason is None:
            return (
                False,
                f"Reason is required for negative feedback type: {feedback.value}",
            )
        return True, ""

    def decide_routing(
        self,
        context: FeedbackContext,
    ) -> FeedbackRoutingDecision:
        """Make explicit routing decision based on typed policy.

        No heuristics are used - all decisions are deterministic based on
        the feedback type and reason.
        """
        self._logger.debug(
            "feedback.policy.routing_decision",
            extra={
                "message_id": context.message_id,
                "feedback": context.feedback.value,
                "reason": context.reason.value if context.reason else None,
                "context_tables": context.context_tables,
            },
        )

        # Determine base actions from feedback type
        actions = self._FEEDBACK_ACTIONS.get(
            context.feedback,
            frozenset({FeedbackRoutingAction.LOG_AND_IGNORE}),
        )

        # Determine tables to downrank based on feedback reason
        table_fqns_to_downrank = self._tables_for_negative_signal(
            feedback=context.feedback,
            reason=context.reason,
            context_tables=context.context_tables,
        )

        # Build repair prompt context if needed
        repair_prompt_context = (
            self._build_repair_context(context)
            if (FeedbackRoutingAction.TRIGGER_REPAIR_TURN in actions)
            else None
        )

        # Build reason string for logging
        reason = self._build_routing_reason(context, actions)

        return FeedbackRoutingDecision(
            actions=actions,
            table_fqns_to_downrank=table_fqns_to_downrank,
            repair_prompt_context=repair_prompt_context,
            reason=reason,
        )

    def _tables_for_negative_signal(
        self,
        feedback: AssistantFeedbackValue,
        reason: FeedbackReason | None,
        context_tables: list[str],
    ) -> list[str]:
        """Determine which tables should receive negative ranking signal.

        Only WRONG_SOURCE and WRONG_METRIC reasons trigger table downranking.
        """
        if feedback != AssistantFeedbackValue.WRONG:
            return []

        if reason not in (FeedbackReason.WRONG_SOURCE, FeedbackReason.WRONG_METRIC):
            return []

        return list(context_tables)

    def _build_repair_context(
        self,
        context: FeedbackContext,
    ) -> dict:
        """Build structured context for repair turn generation."""
        repair_context = {
            "original_message_id": context.message_id,
            "conversation_id": context.conversation_id,
            "workspace_id": context.workspace_id,
            "feedback_type": context.feedback.value,
            "feedback_reason": context.reason.value if context.reason else None,
            "feedback_comment": context.comment,
            "context_tables": context.context_tables,
            "selected_connector": context.selected_connector,
            "requested_at": datetime.now(tz=UTC).isoformat(),
        }

        # Add reason-specific repair hints
        if context.reason == FeedbackReason.WRONG_SOURCE:
            repair_context["repair_hint"] = (
                "The analyst indicated the wrong data source was used. "
                "Consider alternative tables or clarify which data source to use."
            )
        elif context.reason == FeedbackReason.WRONG_METRIC:
            repair_context["repair_hint"] = (
                "The analyst indicated the metric or calculation was wrong. "
                "Revisit the calculation logic and definitions."
            )
        elif context.reason == FeedbackReason.INCOMPLETE:
            repair_context["repair_hint"] = (
                "The answer was incomplete. Address all aspects of the question."
            )
        elif context.reason == FeedbackReason.OTHER:
            repair_context["repair_hint"] = (
                f"Analyst feedback: {context.comment or 'No additional details provided'}"
            )

        return repair_context

    def _build_routing_reason(
        self,
        context: FeedbackContext,
        actions: frozenset[FeedbackRoutingAction],
    ) -> str:
        """Build human-readable reason for routing decision."""
        action_names = ", ".join(sorted(a.value for a in actions))
        return (
            f"feedback_type={context.feedback.value}, "
            f"reason={context.reason.value if context.reason else 'none'}, "
            f"actions={action_names}"
        )


class NegativeFeedbackSignalSink(Protocol):
    """Protocol for sinks that receive negative feedback signals."""

    def record_negative_feedback(
        self,
        *,
        workspace_id: str | None,
        connector_name: str | None,
        table_fqns: list[str],
        occurred_at: datetime | None = None,
    ) -> None: ...


class FeedbackPipelineService:
    """Orchestrates feedback processing through typed policies.

    This service coordinates the feedback flow without containing business logic.
    It delegates to appropriate handlers based on FeedbackPolicy decisions.
    """

    def __init__(
        self,
        policy: FeedbackPolicy | None = None,
        metadata_ranking_service: NegativeFeedbackSignalSink | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._policy = policy or FeedbackPolicy()
        self._metadata_ranking = metadata_ranking_service
        self._logger = logger or logging.getLogger(__name__)

    def process_feedback(
        self,
        context: FeedbackContext,
    ) -> FeedbackRoutingDecision:
        """Process feedback through the typed policy pipeline."""
        # Validate request
        is_valid, error_msg = self._policy.validate_feedback_request(
            feedback=context.feedback,
            reason=context.reason,
        )
        if not is_valid:
            self._logger.warning(
                "feedback.policy.validation_failed",
                extra={"error_detail": error_msg, "context": context},
            )
            return FeedbackRoutingDecision(
                actions=frozenset({FeedbackRoutingAction.LOG_AND_IGNORE}),
                table_fqns_to_downrank=[],
                repair_prompt_context=None,
                reason=f"validation_failed: {error_msg}",
            )

        # Get routing decision
        decision = self._policy.decide_routing(context)

        self._logger.info(
            "feedback.policy.decision_made",
            extra={
                "message_id": context.message_id,
                "decision": decision.reason,
                "tables_to_downrank": decision.table_fqns_to_downrank,
                "has_repair_context": decision.repair_prompt_context is not None,
            },
        )

        return decision

    def apply_metadata_signal(
        self,
        decision: FeedbackRoutingDecision,
        workspace_id: str | None,
        connector_name: str | None,
    ) -> None:
        """Apply metadata ranking signal if needed."""
        if (
            FeedbackRoutingAction.RECORD_NEGATIVE_METADATA_SIGNAL not in decision.actions
            or not decision.table_fqns_to_downrank
        ):
            return

        if self._metadata_ranking is None:
            self._logger.warning(
                "feedback.metadata_signal.skipped_no_service",
                extra={"tables": decision.table_fqns_to_downrank},
            )
            return

        self._metadata_ranking.record_negative_feedback(
            workspace_id=workspace_id,
            connector_name=connector_name,
            table_fqns=decision.table_fqns_to_downrank,
        )
        self._logger.info(
            "feedback.metadata_signal.recorded",
            extra={
                "tables": decision.table_fqns_to_downrank,
                "connector": connector_name,
            },
        )
