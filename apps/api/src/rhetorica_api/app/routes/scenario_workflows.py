"""HTTP/SSE adapters for authoring and asynchronous scenario execution."""

import asyncio
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from rhetorica_backend.contracts.scenario_workflows import (
    TERMINAL_RUN_STATUSES,
    PublishScenarioRequest,
    SaveScenarioDraftRequest,
    ScenarioAiProposal,
    ScenarioAiProposalRequest,
    ScenarioApprovalRequest,
    ScenarioDraft,
    ScenarioRun,
    ScenarioVersion,
    StartScenarioRunRequest,
    WorkflowValidationResult,
)
from rhetorica_backend.domain.scenarios.bootstrap import build_scenario_runtime
from rhetorica_backend.domain.scenarios.settings import ScenarioSettings
from rhetorica_backend.domain.scenarios.workflow_service import ScenarioWorkflowService

from rhetorica_api.app.identity import resolve_current_user
from rhetorica_api.app.routes.scenario_authoring_stream import proposal_events

router = APIRouter(prefix="/v1", tags=["scenario-workflows"])


@lru_cache(maxsize=1)
def service() -> ScenarioWorkflowService:
    return build_scenario_runtime(ScenarioSettings()).service


@router.get("/scenario-capabilities")
def capabilities(workspace_id: str = "default") -> dict:
    user = resolve_current_user(workspace_id)
    return {"enabled": service().enabled, **service().resources.capabilities(user)}


@router.get("/scenarios/{scenario_id}/draft", response_model=ScenarioDraft)
def get_draft(scenario_id: str, workspace_id: str = "default") -> ScenarioDraft:
    return service().authoring.get_draft(scenario_id, resolve_current_user(workspace_id))


@router.put("/scenarios/{scenario_id}/draft", response_model=ScenarioDraft)
def save_draft(scenario_id: str, payload: SaveScenarioDraftRequest) -> ScenarioDraft:
    return service().save(scenario_id, resolve_current_user(payload.workspace_id), payload)


@router.post("/scenarios/{scenario_id}/validate", response_model=WorkflowValidationResult)
def validate(scenario_id: str, payload: SaveScenarioDraftRequest) -> WorkflowValidationResult:
    user = resolve_current_user(payload.workspace_id)
    service().authoring.get_draft(scenario_id, user)
    return service().validate(payload.workflow, user)


@router.post("/scenarios/{scenario_id}/publish", response_model=ScenarioVersion)
def publish(scenario_id: str, payload: PublishScenarioRequest) -> ScenarioVersion:
    return service().publish(
        scenario_id, resolve_current_user(payload.workspace_id), payload.expected_revision
    )


@router.get("/scenarios/{scenario_id}/versions", response_model=list[ScenarioVersion])
def versions(scenario_id: str, workspace_id: str = "default") -> list[ScenarioVersion]:
    return service().authoring.list_versions(scenario_id, resolve_current_user(workspace_id))


@router.post("/scenarios/{scenario_id}/ai-proposals", response_model=ScenarioAiProposal)
async def propose(
    scenario_id: str, payload: ScenarioAiProposalRequest, request: Request
) -> ScenarioAiProposal | StreamingResponse:
    user = resolve_current_user(payload.workspace_id)
    if "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(
            proposal_events(service(), scenario_id, user, payload),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return await service().propose(scenario_id, user, payload)


@router.post("/scenarios/{scenario_id}/runs", status_code=202)
def start_run(
    scenario_id: str,
    payload: StartScenarioRunRequest,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> dict:
    run = service().start(
        scenario_id, resolve_current_user(payload.workspace_id), payload, idempotency_key or ""
    )
    return {"run_id": run.id, "run": run.model_dump(mode="json")}


@router.get("/scenario-runs", response_model=list[ScenarioRun])
def list_runs(
    workspace_id: str = "default",
    scenario_id: str | None = None,
    conversation_id: str | None = None,
) -> list[ScenarioRun]:
    return service().runs.list_runs(
        resolve_current_user(workspace_id), scenario_id=scenario_id, conversation_id=conversation_id
    )


@router.get("/scenario-runs/{run_id}", response_model=ScenarioRun)
def get_run(run_id: str, workspace_id: str = "default") -> ScenarioRun:
    return service().get(run_id, resolve_current_user(workspace_id))


@router.post("/scenario-runs/{run_id}/cancel", response_model=ScenarioRun)
def cancel_run(run_id: str, workspace_id: str = "default") -> ScenarioRun:
    return service().runs.cancel(run_id, resolve_current_user(workspace_id))


@router.post("/scenario-runs/{run_id}/steps/{node_id}/approval", status_code=204)
def approve(run_id: str, node_id: str, payload: ScenarioApprovalRequest) -> None:
    service().runs.approve(
        run_id, node_id, resolve_current_user(payload.workspace_id), payload.approved
    )


@router.get("/scenario-runs/{run_id}/events")
async def events(
    run_id: str,
    request: Request,
    workspace_id: str = "default",
    after: int = 0,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    user = resolve_current_user(workspace_id)
    await asyncio.to_thread(service().runs.get, run_id, user)
    try:
        cursor = max(0, after, int(last_event_id or 0))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_event_cursor") from exc

    async def stream() -> AsyncIterator[str]:
        nonlocal cursor
        while not await request.is_disconnected():
            live_user = await asyncio.to_thread(service().resources.user, user.id, workspace_id)
            batch = await asyncio.to_thread(service().runs.events, run_id, live_user, cursor)
            for event in batch:
                cursor = event.sequence
                yield f"id: {cursor}\nevent: scenario\ndata: {event.model_dump_json()}\n\n"
            if len(batch) < 200:
                run, _ = await asyncio.to_thread(service().runs.get, run_id, live_user)
                if run.status in TERMINAL_RUN_STATUSES:
                    yield "event: done\ndata: {}\n\n"
                    return
                yield ": keep-alive\n\n"
                await asyncio.sleep(2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
