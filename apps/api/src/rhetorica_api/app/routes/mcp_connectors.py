from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from rhetorica_backend.contracts import DeleteConnectorResponse
from rhetorica_backend.contracts.admin_integrations import (
    McpConnectorItem,
    McpToolCatalogItem,
    McpToolSyncResponse,
    UpsertMcpConnectorRequest,
)
from rhetorica_backend.domain.mcp_connectors import McpToolCatalogSyncService
from rhetorica_backend.infra.postgres.mcp_connector_repository import McpConnectorRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy.exc import IntegrityError

from rhetorica_api.app.auth import require_admin_access
from rhetorica_api.app.settings import settings

router = APIRouter(
    prefix="/v1/admin/mcp-connectors",
    tags=["mcp-connectors"],
    dependencies=[Depends(require_admin_access)],
)


@lru_cache(maxsize=1)
def _repository() -> McpConnectorRepository:
    db_runtime = DatabaseRuntime(settings.database_url)
    db_runtime.create_schema()
    return McpConnectorRepository(db=db_runtime)


def _sync_service() -> McpToolCatalogSyncService:
    return McpToolCatalogSyncService(repository=_repository())


@router.get("", response_model=list[McpConnectorItem])
def list_mcp_connectors(
    workspace_id: str = Query(default="default", min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[McpConnectorItem]:
    return _repository().list(workspace_id=workspace_id, limit=limit)


@router.post("", response_model=McpConnectorItem)
def upsert_mcp_connector(payload: UpsertMcpConnectorRequest) -> McpConnectorItem:
    try:
        return _repository().upsert(payload)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="mcp_connector_name_conflict") from exc


@router.put("/{connector_id}", response_model=McpConnectorItem)
def update_mcp_connector(
    connector_id: str,
    payload: UpsertMcpConnectorRequest,
    workspace_id: str = Query(default="default", min_length=1),
) -> McpConnectorItem:
    update_payload = payload.model_copy(update={"workspace_id": workspace_id})
    try:
        updated = _repository().update(
            connector_id=connector_id,
            workspace_id=workspace_id,
            request=update_payload,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="mcp_connector_name_conflict") from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="mcp_connector_not_found")
    return updated


@router.delete("/{connector_id}", response_model=DeleteConnectorResponse)
def delete_mcp_connector(
    connector_id: str,
    workspace_id: str = Query(default="default", min_length=1),
) -> DeleteConnectorResponse:
    deleted = _repository().delete(connector_id=connector_id, workspace_id=workspace_id)
    return DeleteConnectorResponse(connector_id=connector_id, deleted=deleted)


@router.post("/{connector_id}/sync", response_model=McpToolSyncResponse)
def sync_mcp_connector(
    connector_id: str,
    workspace_id: str = Query(default="default", min_length=1),
) -> McpToolSyncResponse:
    connector = _repository().get_by_id(connector_id=connector_id, workspace_id=workspace_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="mcp_connector_not_found")
    count, error = _sync_service().sync_connector(
        connector_id=connector_id,
        workspace_id=workspace_id,
    )
    return McpToolSyncResponse(
        connector_id=connector_id,
        synced=error is None,
        tool_count=count,
        status="success" if error is None else "failed",
        error_message=error,
    )


@router.get("/{connector_id}/tools", response_model=list[McpToolCatalogItem])
def list_mcp_connector_tools(
    connector_id: str,
    workspace_id: str = Query(default="default", min_length=1),
    limit: int = Query(default=500, ge=1, le=1000),
) -> list[McpToolCatalogItem]:
    connector = _repository().get_by_id(connector_id=connector_id, workspace_id=workspace_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="mcp_connector_not_found")
    return _repository().list_connector_tools(
        connector_id=connector_id,
        workspace_id=workspace_id,
        limit=limit,
    )
