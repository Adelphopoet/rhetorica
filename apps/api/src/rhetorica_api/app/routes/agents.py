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
from rhetorica_backend.contracts.coding import (
    AgentGovernedPolicy,
    AgentPolicyConflict,
    DefaultAssistantPolicy,
    UpdateAgentGovernedPolicyRequest,
)
from rhetorica_backend.domain.coding.policies import AgentPolicyValidationError
from rhetorica_backend.infra.postgres.agent_policy_repository import (
    AgentPolicyConflictError,
    AgentPolicyRepository,
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
policy_router = APIRouter(
    prefix="/v1/admin",
    tags=["admin-agent-policies"],
    dependencies=[Depends(require_admin_access)],
)


@lru_cache(maxsize=1)
def _repository() -> AgentProfileRepository:
    db_runtime = DatabaseRuntime(settings.database_url)
    db_runtime.create_schema()
    return AgentProfileRepository(db=db_runtime)


@lru_cache(maxsize=1)
def _policy_repository() -> AgentPolicyRepository:
    db_runtime = DatabaseRuntime(settings.database_url)
    db_runtime.create_schema()
    return AgentPolicyRepository(db=db_runtime)


def _policy_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AgentPolicyConflictError):
        detail = AgentPolicyConflict(current_revision=exc.current_revision).model_dump()
        return HTTPException(status_code=409, detail=detail)
    if isinstance(exc, AgentPolicyValidationError):
        if exc.reason_code == "agent_policy_target_not_found":
            return HTTPException(status_code=404, detail="agent_policy_target_not_found")
        return HTTPException(status_code=422, detail=exc.reason_code)
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail="agent_policy_target_not_found")
    raise exc


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


@admin_router.get("/{agent_id}/governed-policy", response_model=AgentGovernedPolicy)
def get_agent_governed_policy(
    agent_id: str,
    workspace_id: str = Query(default="default", min_length=1),
) -> AgentGovernedPolicy:
    policy = _policy_repository().get_agent_policy(workspace_id=workspace_id, agent_id=agent_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="agent_policy_target_not_found")
    return policy


@admin_router.put("/{agent_id}/governed-policy", response_model=AgentGovernedPolicy)
def update_agent_governed_policy(
    agent_id: str,
    payload: UpdateAgentGovernedPolicyRequest,
    auth_context: Annotated[AuthContext, Depends(require_admin_access)],
    workspace_id: str = Query(default="default", min_length=1),
) -> AgentGovernedPolicy:
    try:
        return _policy_repository().replace_agent_policy(
            workspace_id=workspace_id,
            agent_id=agent_id,
            actor_user_id=auth_context.user_id,
            settings=payload,
        )
    except (AgentPolicyConflictError, AgentPolicyValidationError, LookupError) as exc:
        raise _policy_error(exc) from exc


@policy_router.get("/default-assistant-policy", response_model=DefaultAssistantPolicy)
def get_default_assistant_policy(
    workspace_id: str = Query(default="default", min_length=1),
) -> DefaultAssistantPolicy:
    try:
        return _policy_repository().get_default_assistant_policy(workspace_id=workspace_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="workspace_not_found") from exc


@policy_router.put("/default-assistant-policy", response_model=DefaultAssistantPolicy)
def update_default_assistant_policy(
    payload: UpdateAgentGovernedPolicyRequest,
    auth_context: Annotated[AuthContext, Depends(require_admin_access)],
    workspace_id: str = Query(default="default", min_length=1),
) -> DefaultAssistantPolicy:
    try:
        return _policy_repository().replace_default_assistant_policy(
            workspace_id=workspace_id,
            actor_user_id=auth_context.user_id,
            settings=payload,
        )
    except (AgentPolicyConflictError, AgentPolicyValidationError, LookupError) as exc:
        error = _policy_error(exc)
        if isinstance(exc, LookupError):
            error.detail = "workspace_not_found"
        raise error from exc


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
