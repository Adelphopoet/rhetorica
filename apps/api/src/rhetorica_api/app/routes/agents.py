from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from rhetorica_backend.contracts import (
    AgentProfileItem,
    AgentProfilePublicItem,
    AgentReactionResponse,
    DeleteConnectorResponse,
    LiteLlmAgentApiSpikeResult,
    UpsertAgentProfileRequest,
)
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy.exc import IntegrityError

from rhetorica_api.app.auth import AuthContext, require_admin_access
from rhetorica_api.app.identity import resolve_current_user
from rhetorica_api.app.settings import settings

router = APIRouter(prefix="/v1/agents", tags=["agents"])
admin_router = APIRouter(
    prefix="/v1/admin/agents",
    tags=["admin-agents"],
    dependencies=[Depends(require_admin_access)],
)


@lru_cache(maxsize=1)
def _repository() -> AgentProfileRepository:
    db_runtime = DatabaseRuntime(settings.database_url)
    db_runtime.create_schema()
    return AgentProfileRepository(db=db_runtime)


@router.get("", response_model=list[AgentProfilePublicItem])
def list_agents(
    workspace_id: str = Query(default="default", min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AgentProfileItem]:
    current_user = resolve_current_user(workspace_id)
    return _repository().list(
        workspace_id=workspace_id,
        current_user_id=current_user.id,
        include_inactive=False,
        limit=limit,
    )


@router.post("/{agent_id}/star", response_model=AgentReactionResponse)
def star_agent(
    agent_id: str,
    workspace_id: str = Query(default="default", min_length=1),
) -> AgentReactionResponse:
    current_user = resolve_current_user(workspace_id)
    result = _repository().set_star(
        agent_id=agent_id,
        workspace_id=workspace_id,
        user_id=current_user.id,
        enabled=True,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="agent_not_found")
    return result


@router.delete("/{agent_id}/star", response_model=AgentReactionResponse)
def unstar_agent(
    agent_id: str,
    workspace_id: str = Query(default="default", min_length=1),
) -> AgentReactionResponse:
    current_user = resolve_current_user(workspace_id)
    result = _repository().set_star(
        agent_id=agent_id,
        workspace_id=workspace_id,
        user_id=current_user.id,
        enabled=False,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="agent_not_found")
    return result


@router.post("/{agent_id}/favorite", response_model=AgentReactionResponse)
def favorite_agent(
    agent_id: str,
    workspace_id: str = Query(default="default", min_length=1),
) -> AgentReactionResponse:
    current_user = resolve_current_user(workspace_id)
    result = _repository().set_favorite(
        agent_id=agent_id,
        workspace_id=workspace_id,
        user_id=current_user.id,
        enabled=True,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="agent_not_found")
    return result


@router.delete("/{agent_id}/favorite", response_model=AgentReactionResponse)
def unfavorite_agent(
    agent_id: str,
    workspace_id: str = Query(default="default", min_length=1),
) -> AgentReactionResponse:
    current_user = resolve_current_user(workspace_id)
    result = _repository().set_favorite(
        agent_id=agent_id,
        workspace_id=workspace_id,
        user_id=current_user.id,
        enabled=False,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="agent_not_found")
    return result


@admin_router.get("", response_model=list[AgentProfileItem])
def list_admin_agents(
    workspace_id: str = Query(default="default", min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AgentProfileItem]:
    current_user = resolve_current_user(workspace_id)
    return _repository().list(
        workspace_id=workspace_id,
        current_user_id=current_user.id,
        include_inactive=True,
        limit=limit,
    )


@admin_router.post("", response_model=AgentProfileItem)
def create_agent(
    payload: UpsertAgentProfileRequest,
    auth_context: Annotated[AuthContext, Depends(require_admin_access)],
) -> AgentProfileItem:
    try:
        return _repository().create(payload, actor_user_id=auth_context.user_id)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="agent_slug_conflict") from exc


@admin_router.put("/{agent_id}", response_model=AgentProfileItem)
def update_agent(
    agent_id: str,
    payload: UpsertAgentProfileRequest,
    auth_context: Annotated[AuthContext, Depends(require_admin_access)],
    workspace_id: str = Query(default="default", min_length=1),
) -> AgentProfileItem:
    try:
        updated = _repository().update(
            agent_id=agent_id,
            workspace_id=workspace_id,
            request=payload.model_copy(update={"workspace_id": workspace_id}),
            actor_user_id=auth_context.user_id,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="agent_slug_conflict") from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="agent_not_found")
    return updated


@admin_router.delete("/{agent_id}", response_model=DeleteConnectorResponse)
def delete_agent(
    agent_id: str,
    workspace_id: str = Query(default="default", min_length=1),
) -> DeleteConnectorResponse:
    deleted = _repository().delete(agent_id=agent_id, workspace_id=workspace_id)
    return DeleteConnectorResponse(connector_id=agent_id, deleted=deleted)


@admin_router.get("/litellm-spike", response_model=LiteLlmAgentApiSpikeResult)
def litellm_agent_api_spike() -> LiteLlmAgentApiSpikeResult:
    return AgentProfileRepository.litellm_agent_api_spike()
