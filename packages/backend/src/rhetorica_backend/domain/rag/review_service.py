from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError
from rhetorica_backend.contracts.admin_integrations import (
    RagInsightReviewActionResponse,
    RagInsightReviewItem,
)
from rhetorica_backend.infra.postgres.rag_connector_repository import RagConnectorRepository
from rhetorica_backend.infra.qdrant.insights_store import QdrantInsightsStore


class RagInsightReviewService:
    """Review workflow for pending RAG insights."""

    def __init__(self, rag_connector_repository: RagConnectorRepository):
        self._rag_connector_repository = rag_connector_repository

    def list_pending(
        self,
        workspace_id: str,
        limit: int,
        rag_connector_name: str | None = None,
    ) -> list[RagInsightReviewItem]:
        connection = self._rag_connector_repository.get_connection(
            workspace_id=workspace_id,
            name=rag_connector_name,
        )
        if connection is None:
            return []
        records = QdrantInsightsStore(connection).list_pending(workspace_id=workspace_id, limit=limit)
        items: list[RagInsightReviewItem] = []
        for record in records:
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            item_payload = {"id": record["id"], **payload}
            try:
                items.append(RagInsightReviewItem.model_validate(item_payload))
            except ValidationError:
                continue
        return items

    def approve(
        self,
        workspace_id: str,
        record_id: str,
        verified_by: str,
        rag_connector_name: str | None = None,
    ) -> RagInsightReviewActionResponse | None:
        return self._set_status(
            workspace_id=workspace_id,
            record_id=record_id,
            verified_by=verified_by,
            status="verified",
            rag_connector_name=rag_connector_name,
        )

    def reject(
        self,
        workspace_id: str,
        record_id: str,
        verified_by: str,
        rag_connector_name: str | None = None,
    ) -> RagInsightReviewActionResponse | None:
        return self._set_status(
            workspace_id=workspace_id,
            record_id=record_id,
            verified_by=verified_by,
            status="rejected",
            rag_connector_name=rag_connector_name,
        )

    def _set_status(
        self,
        workspace_id: str,
        record_id: str,
        verified_by: str,
        status: str,
        rag_connector_name: str | None = None,
    ) -> RagInsightReviewActionResponse | None:
        connection = self._rag_connector_repository.get_connection(
            workspace_id=workspace_id,
            name=rag_connector_name,
        )
        if connection is None:
            return None
        store = QdrantInsightsStore(connection)
        record = store.get_record(record_id)
        if record is None:
            return None
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return None
        if payload.get("workspace_id") != workspace_id:
            return None
        verified_at = datetime.now(tz=UTC).isoformat()
        store.set_verification(
            point_id=record_id,
            status=status,
            verified_by=verified_by,
            verified_at=verified_at,
        )
        return RagInsightReviewActionResponse(
            record_id=record_id,
            verification_status=status,
            verified_by=verified_by,
            verified_at=verified_at,
        )
