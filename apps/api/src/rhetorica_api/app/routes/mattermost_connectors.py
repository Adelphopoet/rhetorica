from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from rhetorica_backend.contracts import (
    DeleteConnectorResponse,
    MattermostConnectorItem,
    UpsertMattermostConnectorRequest,
)
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.mattermost_connector_repository import (
    MattermostConnectorRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy.exc import IntegrityError

from rhetorica_api.app.auth import require_workspace_owner_access
from rhetorica_api.app.settings import settings

router = APIRouter(
    prefix="/v1/admin/mattermost-connectors",
    tags=["mattermost-connectors"],
    dependencies=[Depends(require_workspace_owner_access)],
)


@lru_cache(maxsize=1)
def _repository() -> MattermostConnectorRepository:
    db_runtime = DatabaseRuntime(settings.database_url)
    db_runtime.create_schema()
    return MattermostConnectorRepository(db=db_runtime)


@lru_cache(maxsize=1)
def _agent_repository() -> AgentProfileRepository:
    db_runtime = DatabaseRuntime(settings.database_url)
    db_runtime.create_schema()
    return AgentProfileRepository(db=db_runtime)


@router.get("", response_model=list[MattermostConnectorItem])
def list_mattermost_connectors(
    workspace_id: str = Query(default="default", min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[MattermostConnectorItem]:
    return _repository().list(workspace_id=workspace_id, limit=limit)


@router.post("", response_model=MattermostConnectorItem)
def upsert_mattermost_connector(
    payload: UpsertMattermostConnectorRequest,
) -> MattermostConnectorItem:
    _validate_agent(payload)
    try:
        return _repository().upsert(payload)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="mattermost_connector_name_conflict") from exc


@router.put("/{connector_id}", response_model=MattermostConnectorItem)
def update_mattermost_connector(
    connector_id: str,
    payload: UpsertMattermostConnectorRequest,
    workspace_id: str = Query(default="default", min_length=1),
) -> MattermostConnectorItem:
    update_payload = payload.model_copy(update={"workspace_id": workspace_id})
    _validate_agent(update_payload)
    try:
        updated = _repository().update(
            connector_id=connector_id,
            workspace_id=workspace_id,
            request=update_payload,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="mattermost_connector_name_conflict") from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="mattermost_connector_not_found")
    return updated


@router.delete("/{connector_id}", response_model=DeleteConnectorResponse)
def delete_mattermost_connector(
    connector_id: str,
    workspace_id: str = Query(default="default", min_length=1),
) -> DeleteConnectorResponse:
    deleted = _repository().delete(connector_id=connector_id, workspace_id=workspace_id)
    return DeleteConnectorResponse(connector_id=connector_id, deleted=deleted)


def _validate_agent(payload: UpsertMattermostConnectorRequest) -> None:
    if payload.agent_id is None:
        return
    agent = _agent_repository().get(
        agent_id=payload.agent_id,
        workspace_id=payload.workspace_id,
        active_only=True,
    )
    if agent is None:
        raise HTTPException(status_code=404, detail="agent_not_found")
