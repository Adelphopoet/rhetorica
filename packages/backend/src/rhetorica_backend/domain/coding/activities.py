"""Temporal activity boundary for durable coding lifecycle effects."""

from __future__ import annotations

import asyncio
from typing import Any

from rhetorica_backend.contracts.coding import CodingReasonCode
from rhetorica_backend.domain.coding.errors import (
    CodingBudgetExhausted,
    CodingEnvironmentPreparationFailed,
    CodingRunError,
)
from rhetorica_backend.domain.coding.lifecycle import CodingLifecycleService
from temporalio import activity


class CodingActivities:
    def __init__(self, lifecycle: CodingLifecycleService) -> None:
        self._lifecycle = lifecycle

    @activity.defn(name="coding_prepare")
    async def prepare(self, run_id: str) -> dict[str, Any]:
        run = await self._call(self._lifecycle.prepare(run_id=run_id))
        return {"phase": run.phase, "status": run.status, "terminal": self._terminal(run.status)}

    @activity.defn(name="coding_develop")
    async def develop(self, command: dict[str, Any]) -> dict[str, Any]:
        run_id, cycle = self._command(command)
        try:
            candidate = await self._call(self._lifecycle.develop(run_id=run_id, cycle=cycle))
        except CodingEnvironmentPreparationFailed:
            stopped = await self._call(
                self._lifecycle.fail_after_activity_error(
                    run_id=run_id, reason_code=CodingReasonCode.DEPENDENCY_INSTALL_FAILED
                )
            )
            return {"terminal": True, "status": stopped.status, "reason_code": stopped.reason_code}
        except CodingBudgetExhausted as error:
            stopped = await self._call(
                self._lifecycle.stop_after_budget_or_deadline(run_id=run_id, error=error)
            )
            return {"terminal": True, "status": stopped.status, "reason_code": stopped.reason_code}
        return {"terminal": False, "candidate_digest": candidate.digest}

    @activity.defn(name="coding_qa")
    async def qa(self, command: dict[str, Any]) -> dict[str, Any]:
        run_id, cycle = self._command(command)
        try:
            report = await self._call(self._lifecycle.qa(run_id=run_id, cycle=cycle))
        except CodingEnvironmentPreparationFailed:
            stopped = await self._call(
                self._lifecycle.fail_after_activity_error(
                    run_id=run_id, reason_code=CodingReasonCode.DEPENDENCY_INSTALL_FAILED
                )
            )
            return {"terminal": True, "status": stopped.status, "reason_code": stopped.reason_code}
        except CodingBudgetExhausted as error:
            stopped = await self._call(
                self._lifecycle.stop_after_budget_or_deadline(run_id=run_id, error=error)
            )
            return {"terminal": True, "status": stopped.status, "reason_code": stopped.reason_code}
        verdict = report.qa_verdict
        return {
            "terminal": False,
            "report_id": report.id,
            "candidate_digest": report.candidate_digest,
            # A blocked QA result cannot be fixed by another developer pass.
            "retry_developer": verdict is not None and verdict.status == "failed",
        }

    @activity.defn(name="coding_coordinate")
    async def coordinate(self, command: dict[str, Any]) -> dict[str, Any]:
        run_id, turn = self._command(command, key="turn")
        try:
            decision = await self._call(self._lifecycle.coordinate(run_id=run_id, turn=turn))
        except CodingEnvironmentPreparationFailed:
            stopped = await self._call(
                self._lifecycle.fail_after_activity_error(
                    run_id=run_id, reason_code=CodingReasonCode.DEPENDENCY_INSTALL_FAILED
                )
            )
            return {"terminal": True, "status": stopped.status, "reason_code": stopped.reason_code}
        except CodingBudgetExhausted as error:
            stopped = await self._call(
                self._lifecycle.stop_after_budget_or_deadline(run_id=run_id, error=error)
            )
            return {"terminal": True, "status": stopped.status, "reason_code": stopped.reason_code}
        except CodingRunError as error:
            if error.status_code != 403:
                raise
            stopped = await self._call(
                self._lifecycle.stop_after_policy_denial(run_id=run_id, error=error)
            )
            return {"terminal": True, "status": stopped.status, "reason_code": stopped.reason_code}
        run, publish = await self._call(
            self._lifecycle.finish_turn(run_id=run_id, decision=decision)
        )
        return {
            "terminal": not publish,
            "publish": publish,
            "decision": decision.decision,
            "status": run.status,
        }

    @activity.defn(name="coding_publish")
    async def publish(self, run_id: str) -> dict[str, Any]:
        run = await self._call(self._lifecycle.publish(run_id=run_id))
        return {"terminal": self._terminal(run.status), "status": run.status}

    @activity.defn(name="coding_cancel")
    async def cancel(self, run_id: str) -> dict[str, Any]:
        run = await self._call(self._lifecycle.cancel(run_id=run_id))
        return {"terminal": True, "status": run.status}

    @activity.defn(name="coding_fail")
    async def fail(self, run_id: str) -> dict[str, Any]:
        run = await self._call(self._lifecycle.fail_after_activity_error(run_id=run_id))
        return {"terminal": True, "status": run.status, "reason_code": run.reason_code}

    @staticmethod
    async def _call(awaitable: Any) -> Any:
        async def heartbeat() -> None:
            while True:
                activity.heartbeat()
                await asyncio.sleep(5)

        pulse = asyncio.create_task(heartbeat())
        try:
            return await awaitable
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)

    @staticmethod
    def _command(command: dict[str, Any], *, key: str = "cycle") -> tuple[str, int]:
        run_id = command.get("run_id")
        cycle = command.get(key)
        if not isinstance(run_id, str) or not run_id or not isinstance(cycle, int) or cycle < 0:
            raise ValueError("coding_activity_command_invalid")
        return run_id, cycle

    @staticmethod
    def _terminal(status: str) -> bool:
        return status in {"succeeded", "failed", "cancelled", "rejected"}
