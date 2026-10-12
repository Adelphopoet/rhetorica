"""Deterministic model notices for approaching SDK turn limits."""

from __future__ import annotations

LOOP_LIMIT_NOTICE_THRESHOLD = 5
FORCED_FINALIZATION_TURNS = 2


def loop_limit_notice_content(*, iteration: int, max_iterations: int) -> str:
    """Tell the model its exact remaining budget and required finalization mode."""
    remaining_turns = max_iterations - iteration
    forced_finalization_turns = min(FORCED_FINALIZATION_TURNS, max_iterations)
    if remaining_turns <= forced_finalization_turns:
        attempt = forced_finalization_turns - remaining_turns + 1
        return (
            f"This is forced finalization attempt {attempt}/{forced_finalization_turns}. "
            "External tool use is finished. Return the final user-facing response now "
            "using the active output contract. If evidence is incomplete, return the "
            "best supported answer with explicit limitations or ask one concrete "
            "clarification question."
        )
    return (
        f"You have {remaining_turns} LLM turns left including this one before the "
        "agent run is stopped. Prioritize finishing, avoid exploratory or duplicate "
        "tool calls, and prepare a final response with clear limitations when evidence "
        "is incomplete."
    )


def should_force_finalization(*, completed_turns: int, max_iterations: int) -> bool:
    """Disable tools for the final two model invocations."""
    return max_iterations - completed_turns <= min(
        FORCED_FINALIZATION_TURNS,
        max_iterations,
    )
