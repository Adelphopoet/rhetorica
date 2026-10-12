from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from rhetorica_backend.contracts import (
    DeleteConnectorResponse,
    KubernetesConnectorItem,
    UpsertKubernetesConnectorRequest,
)
from rhetorica_backend.infra.postgres.kubernetes_connector_repository import (
    KubernetesConnectorRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy.exc import IntegrityError

from rhetorica_api.app.auth import require_workspace_owner_access
from rhetorica_api.app.settings import settings

router = APIRouter(
    prefix="/v1/admin/kubernetes-connectors",
    tags=["kubernetes-connectors"],
    dependencies=[Depends(require_workspace_owner_access)],
)


@lru_cache(maxsize=1)
def _repository() -> KubernetesConnectorRepository:
    db_runtime = DatabaseRuntime(settings.database_url)
    db_runtime.create_schema()
    return KubernetesConnectorRepository(db=db_runtime)


@router.get("", response_model=list[KubernetesConnectorItem])
def list_kubernetes_connectors(
    workspace_id: str = Query(default="default", min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[KubernetesConnectorItem]:
    return _repository().list(workspace_id=workspace_id, limit=limit)


@router.post("", response_model=KubernetesConnectorItem)
def upsert_kubernetes_connector(
    payload: UpsertKubernetesConnectorRequest,
) -> KubernetesConnectorItem:
    try:
        return _repository().upsert(payload)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="kubernetes_connector_name_conflict") from exc


@router.put("/{connector_id}", response_model=KubernetesConnectorItem)
def update_kubernetes_connector(
    connector_id: str,
    payload: UpsertKubernetesConnectorRequest,
    workspace_id: str = Query(default="default", min_length=1),
) -> KubernetesConnectorItem:
    update_payload = payload.model_copy(update={"workspace_id": workspace_id})
    try:
        updated = _repository().update(
            connector_id=connector_id,
            workspace_id=workspace_id,
            request=update_payload,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="kubernetes_connector_name_conflict") from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="kubernetes_connector_not_found")
    return updated


@router.delete("/{connector_id}", response_model=DeleteConnectorResponse)
def delete_kubernetes_connector(
    connector_id: str,
    workspace_id: str = Query(default="default", min_length=1),
) -> DeleteConnectorResponse:
    deleted = _repository().delete(connector_id=connector_id, workspace_id=workspace_id)
    return DeleteConnectorResponse(connector_id=connector_id, deleted=deleted)
