"""Progress transport; disconnecting the stream cancels generation."""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

from rhetorica_backend.contracts.scenario_workflows import (
    ScenarioAiProgress,
    ScenarioAiProposalRequest,
)
from rhetorica_backend.domain.scenarios.errors import WorkflowError
from rhetorica_backend.domain.scenarios.workflow_service import ScenarioWorkflowService
from rhetorica_backend.infra.logging import get_logger
from rhetorica_backend.infra.postgres.user_repository import UserAccountItem


async def proposal_events(
    service: ScenarioWorkflowService,
    scenario_id: str,
    user: UserAccountItem,
    payload: ScenarioAiProposalRequest,
) -> AsyncIterator[str]:
    queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()

    async def progress(event: ScenarioAiProgress) -> None:
        await queue.put(("progress", event.model_dump(mode="json")))

    async def produce() -> None:
        try:
            proposal = await service.propose(scenario_id, user, payload, progress)
            await queue.put(("proposal", proposal.model_dump(mode="json")))
        except WorkflowError as exc:
            await queue.put((
                "error",
                {"code": exc.code, "detail": str(exc), "status": exc.status_code},
            ))
        except Exception:
            get_logger(__name__).exception("scenario.ai.stream_failed", scenario_id=scenario_id)
            await queue.put((
                "error",
                {
                    "code": "scenario_ai_failed",
                    "detail": "Не удалось подготовить предложение. Повторите запрос.",
                    "status": 500,
                },
            ))

    task = asyncio.create_task(produce())
    try:
        while True:
            try:
                event, value = await asyncio.wait_for(queue.get(), timeout=10)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield f"event: {event}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"
            if event in {"proposal", "error"}:
                return
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
