"""Thin authenticated web adapters for durable coding work."""

import asyncio
import json
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from rhetorica_backend.contracts.coding import (
    CreateCodingRunRequest,
    UpsertCodingAgentTeamRevision,
    UpsertWorkspaceCodingDefaults,
)
from rhetorica_backend.domain.coding.bootstrap import build_coding_service
from rhetorica_backend.domain.coding.errors import CodingRunError
from rhetorica_backend.domain.coding.settings import CodingSettings

from rhetorica_api.app.auth import get_current_auth_context

router = APIRouter(prefix="/v1", tags=["coding-runs"])


@lru_cache(maxsize=1)
def service():
    return build_coding_service(CodingSettings())


@lru_cache(maxsize=1)
def planning_service():
    from rhetorica_backend.domain.coding.bootstrap import build_coding_planning

    return build_coding_planning(CodingSettings())


@lru_cache(maxsize=1)
def defaults_service():
    from rhetorica_backend.domain.coding.bootstrap import build_coding_defaults

    return build_coding_defaults(CodingSettings())


@lru_cache(maxsize=1)
def teams_service():
    from rhetorica_backend.domain.coding.bootstrap import build_coding_teams

    return build_coding_teams(CodingSettings())


@router.get("/coding-agent-teams")
def coding_agent_teams():
    return invoke(teams_service().list, actor())


@router.post("/coding-agent-teams", status_code=201)
def create_coding_agent_team_revision(payload: UpsertCodingAgentTeamRevision):
    return invoke(teams_service().create_revision, actor(), payload)


@router.get("/coding-agent-teams/{team_id}")
def coding_agent_team(team_id: str):
    return invoke(teams_service().detail, actor(), team_id=team_id)


@router.post("/coding-agent-teams/{team_id}/revisions/{revision_id}/activate")
def activate_coding_agent_team(team_id: str, revision_id: str):
    return invoke(teams_service().activate, actor(), team_id=team_id, revision_id=revision_id)


@router.get("/coding-workspace-settings")
def workspace_settings():
    user = actor()
    return invoke(defaults_service().view, user)


@router.put("/coding-workspace-settings")
def replace_workspace_settings(payload: UpsertWorkspaceCodingDefaults):
    user = actor()
    return invoke(defaults_service().replace, user, payload)


@router.get("/coding-plans/{conversation_id}")
def coding_plan(conversation_id: str):
    user = actor()
    return invoke(planning_service().current, conversation_id, user)


@router.post("/coding-plans/{conversation_id}/{plan_id}/confirm", status_code=202)
def confirm_coding_plan(conversation_id: str, plan_id: str):
    user = actor()
    result = invoke(
        planning_service().confirm, conversation_id=conversation_id, plan_id=plan_id, user=user
    )
    return {"run_id": result.run.id, "run": result.run}


def actor():
    auth = get_current_auth_context()
    if auth.credential_type != "session":
        raise HTTPException(status_code=403, detail="channel_not_allowed")
    workspace = auth.active_workspace_id or auth.user.workspace_id
    return auth.user.for_workspace(workspace)


def invoke(method, *args, **kwargs):
    try:
        return method(*args, **kwargs)
    except CodingRunError as exc:
        detail = getattr(exc, "detail", None) or exc.code
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc


@router.get("/coding-capabilities")
def capabilities(root_agent_id: str | None = None, use_workspace_defaults: bool = True):
    user = actor()
    return invoke(
        service().capabilities,
        user,
        root_agent_id=root_agent_id,
        use_workspace_defaults=use_workspace_defaults,
    )


@router.get("/coding-team-options")
def coding_team_options():
    return invoke(service().team_options, actor())


@router.get("/coding-runs")
def list_runs(conversation_id: str | None = None):
    user = actor()
    invoke(service().require_actor, user, auth_kind="session")
    return service().runs.list_owned(
        workspace_id=user.workspace_id, owner_user_id=user.id, conversation_id=conversation_id
    )


@router.get("/coding-runs/{run_id}/diff")
def diff(run_id: str):
    user = actor()
    return invoke(service().diff, run_id, user)


@router.post("/coding-runs", status_code=202)
def create(payload: CreateCodingRunRequest, idempotency_key: Annotated[str, Header()]):
    user = actor()
    result = invoke(
        service().create, payload, user, auth_kind="session", idempotency_key=idempotency_key
    )
    return {"run_id": result.run.id, "run": result.run}


@router.get("/coding-runs/{run_id}")
def read(run_id: str, after: Annotated[int, Query(ge=0)] = 0):
    user = actor()
    return invoke(service().view, run_id, user, after=after)


@router.post("/coding-runs/{run_id}/cancel")
def cancel(run_id: str):
    user = actor()
    return invoke(service().cancel, run_id, user)


@router.get("/coding-runs/{run_id}/events")
async def events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: Annotated[str | None, Header()] = None,
):
    user = actor()
    await asyncio.to_thread(invoke, service().view, run_id, user)
    try:
        cursor = max(after, int(last_event_id or 0))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_event_cursor") from exc

    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            try:
                view = await asyncio.to_thread(service().view, run_id, user, after=cursor)
            except CodingRunError as exc:
                # Headers are already sent: report the failure in-band instead of dropping the stream.
                yield f"event: error\ndata: {json.dumps({'code': exc.code})}\n\n"
                return
            for event in view.progress.events:
                cursor = event.sequence
                yield f"id: {cursor}\nevent: coding\ndata: {event.model_dump_json()}\n\n"
            if len(view.progress.events) < 200:
                if view.run.status in {"succeeded", "failed", "cancelled", "rejected"}:
                    yield "event: done\ndata: {}\n\n"
                    return
                yield ": keep-alive\n\n"
                await asyncio.sleep(2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
