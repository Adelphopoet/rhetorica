"""Deterministic DAG interpreter. All external effects live in activities."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class ScenarioWorkflow:
    def __init__(self) -> None:
        self.decisions: dict[str, bool] = {}

    @workflow.signal
    def approve(self, decision: dict[str, Any]) -> None:
        self.decisions.setdefault(decision["node_id"], decision["approved"])

    @workflow.run
    async def run(self, run_id: str) -> None:
        tasks: dict[str, asyncio.Task] = {}
        try:
            graph = await workflow.execute_activity(
                "scenario_bootstrap",
                run_id,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            test_node = graph.get("test_node_id")
            nodes = graph["nodes"]
            parents = {
                node["id"]: [edge for edge in graph["edges"] if edge["target"] == node["id"]]
                for node in nodes
            }
            completed: dict[str, dict[str, Any]] = {}
            result_ref = None

            async def execute(node: dict[str, Any], skip: bool) -> dict[str, Any]:
                command = {"run_id": run_id, "node_id": node["id"], "skip": skip}
                outcome = await workflow.execute_activity(
                    "scenario_step",
                    command,
                    activity_id=f"step-{node['id']}",
                    start_to_close_timeout=timedelta(minutes=20),
                    heartbeat_timeout=timedelta(seconds=30),
                    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=2),
                        maximum_interval=timedelta(seconds=30),
                        maximum_attempts=3,
                    ),
                )
                if outcome["status"] == "waiting":
                    await workflow.wait_condition(lambda: node["id"] in self.decisions)
                    if not self.decisions[node["id"]]:
                        return {"status": "rejected"}
                    outcome = await workflow.execute_activity(
                        "scenario_step",
                        {**command, "approve": True},
                        activity_id=f"approve-{node['id']}",
                        start_to_close_timeout=timedelta(seconds=60),
                        heartbeat_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                return dict(outcome)

            while len(completed) < len(nodes):
                for node in nodes:
                    key = node["id"]
                    if key in completed or key in tasks:
                        continue
                    if test_node:
                        ready, active = True, key == test_node
                    else:
                        incoming = parents[key]
                        ready = all(edge["source"] in completed for edge in incoming)
                        active = ready and (
                            not incoming
                            or any(
                                completed[edge["source"]]["status"] == "succeeded"
                                and (
                                    edge["branch"] is None
                                    or edge["branch"]
                                    == (
                                        "true"
                                        if completed[edge["source"]].get("branch")
                                        else "false"
                                    )
                                )
                                for edge in incoming
                            )
                        )
                    if ready:
                        tasks[key] = asyncio.create_task(execute(node, not active))
                if not tasks:
                    raise RuntimeError("Workflow has no runnable nodes")  # noqa: TRY301 - the run must record failed status
                await workflow.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)
                for node in nodes:
                    key = node["id"]
                    if key not in tasks or not tasks[key].done():
                        continue
                    outcome = tasks.pop(key).result()
                    if outcome["status"] == "rejected":
                        for task in tasks.values():
                            task.cancel()
                        await asyncio.gather(*tasks.values(), return_exceptions=True)
                        await self._finish(run_id, "rejected")
                        return
                    completed[key] = outcome
                    if (node["type"] == "result" and not test_node) or key == test_node:
                        result_ref = outcome.get("output_ref")
            await self._finish(run_id, "succeeded", result_ref=result_ref)
        except BaseException as exc:
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            cancelled = isinstance(exc, asyncio.CancelledError)
            cause: Any = exc
            while getattr(cause, "cause", None) is not None:
                cause = cause.cause
            await asyncio.shield(
                self._finish(
                    run_id,
                    "cancelled" if cancelled else "failed",
                    error=None if cancelled else str(cause),
                )
            )
            raise

    async def _finish(self, run_id: str, status: str, **fields: Any) -> None:
        await workflow.execute_activity(
            "scenario_finish",
            {"run_id": run_id, "status": status, **fields},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_interval=timedelta(seconds=30)),
        )
