from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from rhetorica_backend.contracts import (
    DeleteConnectorResponse,
    RagConnectorItem,
    UpsertRagConnectorRequest,
)
from rhetorica_backend.infra.postgres.rag_connector_repository import RagConnectorRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy.exc import IntegrityError

from rhetorica_api.app.auth import require_workspace_owner_access
from rhetorica_api.app.settings import settings

router = APIRouter(
    prefix="/v1/admin/rag-connectors",
    tags=["rag-connectors"],
    dependencies=[Depends(require_workspace_owner_access)],
)


@lru_cache(maxsize=1)
def _repository() -> RagConnectorRepository:
    db_runtime = DatabaseRuntime(settings.database_url)
    db_runtime.create_schema()
    return RagConnectorRepository(db=db_runtime)


@router.get("", response_model=list[RagConnectorItem])
def list_rag_connectors(
    workspace_id: str = Query(default="default", min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[RagConnectorItem]:
    return _repository().list(workspace_id=workspace_id, limit=limit)


@router.post("", response_model=RagConnectorItem)
def upsert_rag_connector(payload: UpsertRagConnectorRequest) -> RagConnectorItem:
    try:
        return _repository().upsert(payload)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="rag_connector_name_conflict") from exc


@router.put("/{connector_id}", response_model=RagConnectorItem)
def update_rag_connector(
    connector_id: str,
    payload: UpsertRagConnectorRequest,
    workspace_id: str = Query(default="default", min_length=1),
) -> RagConnectorItem:
    update_payload = payload.model_copy(update={"workspace_id": workspace_id})
    try:
        updated = _repository().update(
            connector_id=connector_id,
            workspace_id=workspace_id,
            request=update_payload,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="rag_connector_name_conflict") from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="rag_connector_not_found")
    return updated


@router.delete("/{connector_id}", response_model=DeleteConnectorResponse)
def delete_rag_connector(
    connector_id: str,
    workspace_id: str = Query(default="default", min_length=1),
) -> DeleteConnectorResponse:
    deleted = _repository().delete(connector_id=connector_id, workspace_id=workspace_id)
    return DeleteConnectorResponse(connector_id=connector_id, deleted=deleted)
