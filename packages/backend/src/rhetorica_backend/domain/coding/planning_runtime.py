"""Native SDK streaming for an ordinary pre-confirmation conversation."""

from agents import Runner
from agents.stream_events import RawResponsesStreamEvent


async def run_planning_agent(agent, messages, *, max_turns, run_config, hooks):
    result = Runner.run_streamed(
        agent, messages, max_turns=max_turns, run_config=run_config, hooks=hooks
    )
    try:
        async for event in result.stream_events():
            if hooks is not None and isinstance(event, RawResponsesStreamEvent):
                hooks.response_event(event.data)
        return result
    finally:
        if not result.is_complete:
            result.cancel()
