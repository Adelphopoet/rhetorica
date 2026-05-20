from __future__ import annotations

from pathlib import Path

from rhetorica_backend.contracts.admin_integrations import (
    RagConnectionConfig,
    UpsertRagConnectorRequest,
)
from rhetorica_backend.infra.postgres.rag_connector_repository import RagConnectorRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


def _runtime(tmp_path: Path, db_name: str) -> DatabaseRuntime:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/{db_name}")
    runtime.create_schema()
    return runtime


def test_get_connection_returns_builtin_env_config_when_db_empty(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, "rag_builtin_env.db")
    repository = RagConnectorRepository(db=runtime)

    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("RAG_COLLECTION_NAME", "rag_insights")
    monkeypatch.setenv("RAG_VECTOR_SIZE", "512")
    monkeypatch.setenv("RAG_DISTANCE", "dot")
    monkeypatch.setenv("RAG_CONFIDENCE_THRESHOLD", "0.85")
    monkeypatch.setenv("RAG_MERGE_SIMILARITY_THRESHOLD", "0.93")

    connection = repository.get_connection(workspace_id="ws-1")

    assert connection is not None
    assert connection.base_url == "http://qdrant:6333"
    assert connection.collection_name == "rag_insights"
    assert connection.vector_size == 512
    assert connection.distance == "dot"
    assert connection.confidence_threshold == 0.85
    assert connection.extra_settings["merge_similarity_threshold"] == 0.93


def test_get_connection_prefers_workspace_connector_over_builtin_env(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, "rag_connector_priority.db")
    repository = RagConnectorRepository(db=runtime)

    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("RAG_COLLECTION_NAME", "rag_from_env")

    repository.upsert(
        UpsertRagConnectorRequest(
            name="rag-main",
            workspace_id="ws-1",
            connection=RagConnectionConfig(
                provider="qdrant",
                base_url="http://custom-qdrant:6333",
                api_key=None,
                collection_name="rag_from_db",
                vector_size=384,
                distance="cosine",
                prefer_grpc=False,
                embedding_model="BAAI/bge-small-en-v1.5",
                confidence_threshold=0.7,
            ),
        )
    )

    connection = repository.get_connection(workspace_id="ws-1")

    assert connection is not None
    assert connection.base_url == "http://custom-qdrant:6333"
    assert connection.collection_name == "rag_from_db"
