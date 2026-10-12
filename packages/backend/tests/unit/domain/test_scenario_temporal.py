from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from rhetorica_backend.domain.scenarios.temporal_workflow import ScenarioWorkflow
from temporalio import activity
from temporalio.client import Client, WorkflowHistory
from temporalio.worker import Replayer, Worker

FIXTURE = Path(__file__).parent / "fixtures" / "scenario-history.json"


def test_temporal_history_replays_without_io() -> None:
    async def replay() -> None:
        await Replayer(workflows=[ScenarioWorkflow]).replay_workflow(
            WorkflowHistory.from_json("scenario/replay", FIXTURE.read_text())
        )

    asyncio.run(replay())


@pytest.mark.skipif(
    not os.environ.get("TEMPORAL_TEST_ADDRESS"), reason="Requires self-hosted Temporal"
)
@pytest.mark.parametrize("branch", [True, False])
def test_temporal_branches_parallel_join_approval_and_replay(branch: bool) -> None:
    async def check() -> None:
        calls: list[str] = []
        finished: list[dict] = []
        waiting = asyncio.Event()

        @activity.defn(name="scenario_bootstrap")
        async def bootstrap(_run_id: str) -> dict:
            return {
                "nodes": [
                    {"id": key, "type": kind}
                    for key, kind in [
                        ("start", "input"),
                        ("condition", "condition"),
                        ("yes", "python"),
                        ("no", "sql"),
                        ("parallel", "python"),
                        ("join", "join"),
                        ("approval", "approval"),
                        ("end", "result"),
                    ]
                ],
                "edges": [
                    {"source": source, "target": target, "branch": choice}
                    for source, target, choice in [
                        ("start", "condition", None),
                        ("condition", "yes", "true"),
                        ("condition", "no", "false"),
                        ("start", "parallel", None),
                        ("yes", "join", None),
                        ("no", "join", None),
                        ("parallel", "join", None),
                        ("join", "approval", None),
                        ("approval", "end", None),
                    ]
                ],
                "test_node_id": None,
            }

        @activity.defn(name="scenario_step")
        async def step(command: dict) -> dict:
            activity.heartbeat()
            key = command["node_id"]
            if command.get("skip"):
                calls.append(f"skip:{key}")
                return {"status": "skipped"}
            if key == "approval" and not command.get("approve"):
                calls.append(key)
                waiting.set()
                return {"status": "waiting"}
            if key == "join":
                assert "parallel" in calls
                assert ("yes" if branch else "no") in calls
                assert ("skip:no" if branch else "skip:yes") in calls
            if key == "parallel":
                await asyncio.sleep(0.1)
            calls.append(f"approve:{key}" if command.get("approve") else key)
            return {
                "status": "succeeded",
                "output_ref": f"saved/{key}",
                "branch": branch if key == "condition" else None,
            }

        @activity.defn(name="scenario_finish")
        async def finish(command: dict) -> None:
            finished.append(command)

        client = await Client.connect(os.environ["TEMPORAL_TEST_ADDRESS"])
        queue = f"scenario-test-{uuid4()}"
        worker_args = {
            "task_queue": queue,
            "workflows": [ScenarioWorkflow],
            "activities": [bootstrap, step, finish],
        }
        async with Worker(client, **worker_args):
            handle = await client.start_workflow(
                ScenarioWorkflow.run, "owned-run", id=f"scenario-test-{uuid4()}", task_queue=queue
            )
            await asyncio.wait_for(waiting.wait(), 15)
        # A replacement worker resumes an approval from history; previous steps stay completed.
        async with Worker(client, **worker_args):
            await handle.signal(ScenarioWorkflow.approve, {"node_id": "approval", "approved": True})
            await asyncio.wait_for(handle.result(), 15)
        assert calls.count("parallel") == 1
        assert finished == [
            {"run_id": "owned-run", "status": "succeeded", "result_ref": "saved/end"}
        ]
        history = await handle.fetch_history()
        await Replayer(workflows=[ScenarioWorkflow]).replay_workflow(history)
        if branch and os.environ.get("UPDATE_SCENARIO_REPLAY") == "1":
            FIXTURE.parent.mkdir(exist_ok=True)
            FIXTURE.write_text(history.to_json())

    asyncio.run(check())


@pytest.mark.skipif(
    not os.environ.get("TEMPORAL_TEST_ADDRESS"), reason="Requires self-hosted Temporal"
)
@pytest.mark.parametrize("action", ["reject", "cancel"])
def test_temporal_rejection_and_cancellation_stop_dependants(action: str) -> None:
    async def check() -> None:
        from temporalio.client import WorkflowFailureError

        waiting = asyncio.Event()
        finished: list[dict] = []
        calls: list[str] = []

        @activity.defn(name="scenario_bootstrap")
        async def bootstrap(_run: str) -> dict:
            return {
                "nodes": [{"id": "approval", "type": "approval"}, {"id": "end", "type": "result"}],
                "edges": [{"source": "approval", "target": "end", "branch": None}],
            }

        @activity.defn(name="scenario_step")
        async def step(command: dict) -> dict:
            calls.append(command["node_id"])
            activity.heartbeat()
            waiting.set()
            return {"status": "waiting"}

        @activity.defn(name="scenario_finish")
        async def finish(command: dict) -> None:
            finished.append(command)

        client = await Client.connect(os.environ["TEMPORAL_TEST_ADDRESS"])
        queue = f"scenario-stop-{uuid4()}"
        async with Worker(
            client,
            task_queue=queue,
            workflows=[ScenarioWorkflow],
            activities=[bootstrap, step, finish],
        ):
            handle = await client.start_workflow(
                ScenarioWorkflow.run, "owned-run", id=queue, task_queue=queue
            )
            await asyncio.wait_for(waiting.wait(), 15)
            if action == "reject":
                await handle.signal(
                    ScenarioWorkflow.approve, {"node_id": "approval", "approved": False}
                )
                await asyncio.wait_for(handle.result(), 15)
            else:
                await handle.cancel()
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(handle.result(), 15)
        assert calls == ["approval"]
        assert finished[0]["status"] == ("rejected" if action == "reject" else "cancelled")
        await Replayer(workflows=[ScenarioWorkflow]).replay_workflow(await handle.fetch_history())

    asyncio.run(check())
