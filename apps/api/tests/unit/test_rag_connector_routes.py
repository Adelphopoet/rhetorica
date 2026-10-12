from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.routes import rag_connectors as rag_connector_routes
from rhetorica_api.app.routes import rag_insights_review as rag_insights_review_routes
from rhetorica_api.app.settings import settings


def _client(tmp_path: Path) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_rag_connectors.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    rag_connector_routes._repository.cache_clear()
    rag_insights_review_routes._service.cache_clear()
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    return TestClient(create_app())


def test_rag_connector_upsert_list_delete(tmp_path: Path) -> None:
    client = _client(tmp_path)
    create_response = client.post(
        "/v1/admin/rag-connectors",
        json={
            "name": "rag-main",
            "workspace_id": "default",
            "connection": {
                "provider": "qdrant",
                "base_url": "http://qdrant:6333",
                "api_key": "token-1",
                "collection_name": "rag_insights",
                "vector_size": 384,
                "distance": "cosine",
                "embedding_model": "BAAI/bge-small-en-v1.5",
                "confidence_threshold": 0.72,
            },
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["name"] == "rag-main"
    assert payload["has_api_key"] is True
    assert payload["connection"]["api_key"] is None
    connector_id = payload["id"]

    list_response = client.get("/v1/admin/rag-connectors", params={"workspace_id": "default"})
    assert list_response.status_code == 200
    rows = list_response.json()
    assert len(rows) == 1
    assert rows[0]["id"] == connector_id

    update_response = client.put(
        f"/v1/admin/rag-connectors/{connector_id}",
        params={"workspace_id": "default"},
        json={
            "name": "rag-main-updated",
            "workspace_id": "default",
            "connection": {
                "provider": "qdrant",
                "base_url": "http://qdrant:6333",
                "api_key": None,
                "collection_name": "rag_insights_v2",
                "vector_size": 768,
                "distance": "dot",
                "prefer_grpc": True,
                "embedding_model": "BAAI/bge-base-en-v1.5",
                "confidence_threshold": 0.8,
            },
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "rag-main-updated"
    assert updated["has_api_key"] is True
    assert updated["connection"]["collection_name"] == "rag_insights_v2"
    assert updated["connection"]["distance"] == "dot"

    delete_response = client.delete(
        f"/v1/admin/rag-connectors/{connector_id}",
        params={"workspace_id": "default"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True
