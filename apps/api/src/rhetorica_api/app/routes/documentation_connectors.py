from functools import lru_cache

from celery import Celery  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends, HTTPException, Query
from rhetorica_backend.contracts import (
    DeleteConnectorResponse,
    DocumentationConnectorItem,
    DocumentationSyncResponse,
    DocumentationSyncRunItem,
    UpsertDocumentationConnectorRequest,
)
from rhetorica_backend.infra.postgres.documentation_connector_repository import (
    DocumentationConnectorRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy.exc import IntegrityError

from rhetorica_api.app.auth import require_admin_access
from rhetorica_api.app.settings import settings

router = APIRouter(
    prefix="/v1/admin/documentation-connectors",
    tags=["documentation-connectors"],
    dependencies=[Depends(require_admin_access)],
)


@lru_cache(maxsize=1)
def _repository() -> DocumentationConnectorRepository:
    db_runtime = DatabaseRuntime(settings.database_url)
    db_runtime.create_schema()
    return DocumentationConnectorRepository(db=db_runtime)


@lru_cache(maxsize=1)
def _celery_app() -> Celery:
    return Celery(
        "rhetorica-api",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
    )


@router.get("", response_model=list[DocumentationConnectorItem])
def list_documentation_connectors(
    workspace_id: str = Query(default="default", min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[DocumentationConnectorItem]:
    return _repository().list(workspace_id=workspace_id, limit=limit)


@router.post("", response_model=DocumentationConnectorItem)
def upsert_documentation_connector(
    payload: UpsertDocumentationConnectorRequest,
) -> DocumentationConnectorItem:
    try:
        return _repository().upsert(payload)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="documentation_connector_name_conflict",
        ) from exc


@router.put("/{connector_id}", response_model=DocumentationConnectorItem)
def update_documentation_connector(
    connector_id: str,
    payload: UpsertDocumentationConnectorRequest,
    workspace_id: str = Query(default="default", min_length=1),
) -> DocumentationConnectorItem:
    update_payload = payload.model_copy(update={"workspace_id": workspace_id})
    try:
        updated = _repository().update(
            connector_id=connector_id,
            workspace_id=workspace_id,
            request=update_payload,
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="documentation_connector_name_conflict",
        ) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="documentation_connector_not_found")
    return updated


@router.delete("/{connector_id}", response_model=DeleteConnectorResponse)
def delete_documentation_connector(
    connector_id: str,
    workspace_id: str = Query(default="default", min_length=1),
) -> DeleteConnectorResponse:
    deleted = _repository().delete(connector_id=connector_id, workspace_id=workspace_id)
    return DeleteConnectorResponse(connector_id=connector_id, deleted=deleted)


@router.post("/{connector_id}/sync", response_model=DocumentationSyncResponse)
def run_documentation_sync(
    connector_id: str,
    workspace_id: str = Query(default="default", min_length=1),
) -> DocumentationSyncResponse:
    connector = _repository().get_by_id(connector_id=connector_id, workspace_id=workspace_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="documentation_connector_not_found")
    task = _celery_app().send_task(
        "documentation.sync_connector",
        args=[connector_id, workspace_id, "manual"],
    )
    return DocumentationSyncResponse(
        connector_id=connector_id,
        task_id=str(task.id),
        status="queued",
    )


@router.get("/{connector_id}/sync-runs", response_model=list[DocumentationSyncRunItem])
def list_documentation_sync_runs(
    connector_id: str,
    workspace_id: str = Query(default="default", min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[DocumentationSyncRunItem]:
    return _repository().list_sync_runs(
        connector_id=connector_id,
        workspace_id=workspace_id,
        limit=limit,
    )
