from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import confluence_connectors as confluence_connector_routes
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.routes import openmetadata_connectors as openmetadata_connector_routes
from rhetorica_api.app.routes import rag_connectors as rag_connector_routes
from rhetorica_api.app.routes import rag_insights_review as rag_insights_review_routes
from rhetorica_api.app.settings import settings
from rhetorica_backend.domain.rag import review_service as rag_review_service_module


class _FakeQdrantStore:
    _records: ClassVar[dict[str, dict[str, object]]] = {
        "point-1": {
            "workspace_id": "default",
            "conversation_id": "conv-1",
            "message_id": "msg-1",
            "execution_id": None,
            "summary": "Users react better to weekday morning pushes",
            "answer_excerpt": "Morning push in weekdays wins for retention.",
            "generated_sql": "select 1",
            "context_tables": ["dm.events"],
            "metadata_sources": ["openmetadata"],
            "keywords": ["push", "retention"],
            "llm_confidence": 0.83,
            "llm_reason": "stable recurring signal",
            "verification_status": "pending",
            "human_verified": False,
            "verified_by": None,
            "verified_at": None,
            "created_at": "2026-04-02T00:00:00+00:00",
            "updated_at": "2026-04-02T00:00:00+00:00",
        }
    }

    def __init__(self, _connection):
        pass

    def list_pending(self, workspace_id: str, limit: int) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for record_id, payload in self._records.items():
            if payload.get("workspace_id") != workspace_id:
                continue
            if payload.get("verification_status") != "pending":
                continue
            result.append({"id": record_id, "payload": payload})
            if len(result) >= limit:
                break
        return result

    def get_record(self, point_id: str) -> dict[str, object] | None:
        payload = self._records.get(point_id)
        if payload is None:
            return None
        return {"id": point_id, "payload": payload}

    def set_verification(
        self,
        point_id: str,
        status: str,
        verified_by: str,
        verified_at: str,
    ) -> None:
        payload = self._records[point_id]
        payload["verification_status"] = status
        payload["human_verified"] = True
        payload["verified_by"] = verified_by
        payload["verified_at"] = verified_at
        payload["updated_at"] = verified_at


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_rag_review.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    confluence_connector_routes._repository.cache_clear()
    openmetadata_connector_routes._repository.cache_clear()
    rag_connector_routes._repository.cache_clear()
    rag_insights_review_routes._service.cache_clear()
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    monkeypatch.setattr(rag_review_service_module, "QdrantInsightsStore", _FakeQdrantStore)
    return TestClient(create_app())


def test_rag_insights_review_list_approve_reject(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    pending_response = client.get(
        "/v1/admin/rag-insights/pending",
        params={"workspace_id": "default"},
    )
    assert pending_response.status_code == 200
    pending_rows = pending_response.json()
    assert len(pending_rows) == 1
    assert pending_rows[0]["verification_status"] == "pending"
    assert pending_rows[0]["likes_count"] == 0

    approve_response = client.post(
        "/v1/admin/rag-insights/point-1/approve",
        json={"workspace_id": "default", "verified_by": "qa-admin"},
    )
    assert approve_response.status_code == 200
    assert approve_response.json()["verification_status"] == "verified"

    # Reset to pending, then reject.
    _FakeQdrantStore._records["point-1"]["verification_status"] = "pending"
    _FakeQdrantStore._records["point-1"]["human_verified"] = False
    _FakeQdrantStore._records["point-1"]["verified_by"] = None
    _FakeQdrantStore._records["point-1"]["verified_at"] = None

    reject_response = client.post(
        "/v1/admin/rag-insights/point-1/reject",
        json={"workspace_id": "default", "verified_by": "qa-admin"},
    )
    assert reject_response.status_code == 200
    assert reject_response.json()["verification_status"] == "rejected"
