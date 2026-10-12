"""Deterministic coordinator for durable governed coding work."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, cast

from temporalio import workflow
from temporalio.common import RetryPolicy

TEAM_WORKFLOW_PATCH = "coding-agent-teams-v1"


@workflow.defn
class CodingWorkflow:
    """Coordinates compact IDs only; all I/O and state live in activities.

    Temporal owns durability, not routing: the SDK coordinator chooses which
    team member to call inside one durable turn. Runs started before coding
    teams replay the fixed developer/QA history through the unpatched branch.
    """

    @workflow.run
    async def run(self, run_id: str) -> None:
        try:
            prepared = await self._activity("coding_prepare", run_id, activity_id="prepare")
            if prepared.get("terminal"):
                return
            if self._teams_enabled():
                await self._coordinated(run_id)
            else:
                await self._legacy(run_id)
        except asyncio.CancelledError:
            await asyncio.shield(
                self._activity("coding_cancel", run_id, activity_id="cancel", retry=False)
            )
            raise
        except Exception:
            await self._activity("coding_fail", run_id, activity_id="fail", retry=False)
            raise

    async def _coordinated(self, run_id: str) -> None:
        turn = await self._activity(
            "coding_coordinate",
            {"run_id": run_id, "turn": 0},
            activity_id="coordinator-0",
            long_running=True,
            timeout_minutes=24 * 60,
        )
        if turn.get("terminal") or not turn.get("publish"):
            return
        await self._publish(run_id)

    async def _legacy(self, run_id: str) -> None:
        cycle = 0
        while True:
            developer = await self._activity(
                "coding_develop",
                {"run_id": run_id, "cycle": cycle},
                activity_id=f"developer-{cycle}",
                long_running=True,
            )
            if developer.get("terminal"):
                return
            qa = await self._activity(
                "coding_qa",
                {"run_id": run_id, "cycle": cycle},
                activity_id=f"qa-{cycle}",
                long_running=True,
            )
            if qa.get("terminal"):
                return
            if qa.get("retry_developer"):
                cycle += 1
                continue
            await self._publish(run_id)
            return

    async def _publish(self, run_id: str) -> None:
        publication = await self._activity(
            "coding_publish",
            run_id,
            activity_id="publish",
            long_running=True,
        )
        if publication.get("status") == "cancel_requested":
            await self._activity(
                "coding_cancel",
                run_id,
                activity_id="cancel-after-publication",
                retry=False,
            )

    @staticmethod
    def _teams_enabled() -> bool:
        return workflow.patched(TEAM_WORKFLOW_PATCH)

    @staticmethod
    async def _activity(
        name: str,
        argument: str | dict[str, Any],
        *,
        activity_id: str,
        long_running: bool = False,
        retry: bool = True,
        timeout_minutes: int | None = None,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await workflow.execute_activity(
                name,
                argument,
                activity_id=activity_id,
                start_to_close_timeout=timedelta(
                    minutes=timeout_minutes or (20 if long_running else 1)
                ),
                heartbeat_timeout=timedelta(seconds=30) if long_running else None,
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                retry_policy=(
                    RetryPolicy(
                        initial_interval=timedelta(seconds=2),
                        maximum_interval=timedelta(seconds=30),
                        maximum_attempts=3,
                        non_retryable_error_types=["ModelBehaviorError", "UserError"],
                    )
                    if retry
                    else RetryPolicy(maximum_attempts=1)
                ),
            ),
        )
