from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from rhetorica_backend.contracts import (
    RagInsightReviewActionRequest,
    RagInsightReviewActionResponse,
    RagInsightReviewItem,
)
from rhetorica_backend.domain.rag import RagInsightReviewService
from rhetorica_backend.infra.postgres.rag_connector_repository import RagConnectorRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime

from rhetorica_api.app.auth import require_admin_access
from rhetorica_api.app.settings import settings

router = APIRouter(
    prefix="/v1/admin/rag-insights",
    tags=["rag-insights-review"],
    dependencies=[Depends(require_admin_access)],
)


@lru_cache(maxsize=1)
def _service() -> RagInsightReviewService:
    db_runtime = DatabaseRuntime(settings.database_url)
    db_runtime.create_schema()
    repository = RagConnectorRepository(db=db_runtime)
    return RagInsightReviewService(rag_connector_repository=repository)


@router.get("/pending", response_model=list[RagInsightReviewItem])
def list_pending_rag_insights(
    workspace_id: str = Query(default="default", min_length=1),
    limit: int = Query(default=50, ge=1, le=500),
    rag_connector_name: str | None = Query(default=None),
) -> list[RagInsightReviewItem]:
    return _service().list_pending(
        workspace_id=workspace_id,
        limit=limit,
        rag_connector_name=rag_connector_name,
    )


@router.post("/{record_id}/approve", response_model=RagInsightReviewActionResponse)
def approve_rag_insight(
    record_id: str,
    payload: RagInsightReviewActionRequest,
    rag_connector_name: str | None = Query(default=None),
) -> RagInsightReviewActionResponse:
    updated = _service().approve(
        workspace_id=payload.workspace_id,
        record_id=record_id,
        verified_by=payload.verified_by,
        rag_connector_name=rag_connector_name,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="rag_insight_not_found")
    return updated


@router.post("/{record_id}/reject", response_model=RagInsightReviewActionResponse)
def reject_rag_insight(
    record_id: str,
    payload: RagInsightReviewActionRequest,
    rag_connector_name: str | None = Query(default=None),
) -> RagInsightReviewActionResponse:
    updated = _service().reject(
        workspace_id=payload.workspace_id,
        record_id=record_id,
        verified_by=payload.verified_by,
        rag_connector_name=rag_connector_name,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="rag_insight_not_found")
    return updated
