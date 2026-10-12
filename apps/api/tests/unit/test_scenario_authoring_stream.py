import asyncio
from types import SimpleNamespace

from rhetorica_api.app.routes.scenario_authoring_stream import proposal_events
from rhetorica_backend.contracts.scenario_workflows import (
    ScenarioAiProgress,
    ScenarioAiProposalRequest,
)
from rhetorica_backend.domain.scenarios.errors import WorkflowConflict


def test_stream_emits_progress_and_a_safe_error():
    async def generate(*args):
        await args[3](
            ScenarioAiProgress(phase="generating", message="Готовлю", batch=1, node_count=0)
        )
        raise WorkflowConflict()

    async def check():
        return [
            event
            async for event in proposal_events(
                SimpleNamespace(propose=generate),
                "s",
                SimpleNamespace(),
                ScenarioAiProposalRequest(agent_id="a", instruction="Edit", expected_revision=0),
            )
        ]

    events = asyncio.run(check())
    assert len(events) == 2
    assert "event: progress" in events[0]
    assert "event: error" in events[1]
    assert "scenario_revision_conflict" in events[1]


def test_disconnecting_stream_cancels_inflight_model_request():
    async def check():
        cancelled = asyncio.Event()

        async def generate(*args):
            await args[3](
                ScenarioAiProgress(phase="generating", message="Готовлю", batch=1, node_count=0)
            )
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        stream = proposal_events(
            SimpleNamespace(propose=generate),
            "s",
            SimpleNamespace(),
            ScenarioAiProposalRequest(agent_id="a", instruction="Edit", expected_revision=0),
        )
        assert "event: progress" in await anext(stream)
        await stream.aclose()
        await asyncio.wait_for(cancelled.wait(), 1)

    asyncio.run(check())
