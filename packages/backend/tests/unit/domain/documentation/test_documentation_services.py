from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rhetorica_backend.contracts.admin_integrations import (
    DocumentationConnectionConfig,
    UpsertDocumentationConnectorRequest,
)
from rhetorica_backend.domain.documentation import embedding as embedding_module
from rhetorica_backend.domain.documentation import retrieval_service as retrieval_module
from rhetorica_backend.domain.documentation.embedding import OpenAiCompatibleEmbeddingProvider
from rhetorica_backend.domain.documentation.models import DocumentationPageDocument
from rhetorica_backend.domain.documentation.retrieval_service import DocumentationRetrievalService
from rhetorica_backend.domain.documentation.sync_service import DocumentationSyncService
from rhetorica_backend.infra.postgres.documentation_connector_repository import (
    DocumentationConnectorRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.secrets import InMemorySecretStore


def _runtime(tmp_path: Path, db_name: str) -> DatabaseRuntime:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/{db_name}")
    runtime.create_schema()
    return runtime


def _repository(tmp_path: Path) -> DocumentationConnectorRepository:
    return DocumentationConnectorRepository(
        db=_runtime(tmp_path, "documentation.db"),
        secret_store=InMemorySecretStore(),
    )


def _connector(
    repository: DocumentationConnectorRepository,
    *,
    name: str = "platform_docs",
    visible_to_default_assistant: bool = True,
    embedding_base_url: str | None = None,
):
    return repository.upsert(
        UpsertDocumentationConnectorRequest(
            name=name,
            workspace_id="ws-1",
            is_active=True,
            visible_to_default_assistant=visible_to_default_assistant,
            connection=DocumentationConnectionConfig(
                base_url="https://docs.example.com",
                index_url="https://docs.example.com/llm-docs-index.json",
                usage_description="Use for platform docs.",
                include_paths=["/docs/"],
                embedding_base_url=embedding_base_url,
            ),
        )
    )


class OfflineDocumentationSyncService(DocumentationSyncService):
    def _load_documents(self, connector):  # type: ignore[override]
        body = "# ADR\n\n" + " ".join(["analytics-contract"] * 260)
        return [
            DocumentationPageDocument(
                page_ref="/docs/adr/analytics-contract/",
                title="Analytics Contract ADR",
                url="https://docs.example.com/docs/adr/analytics-contract/",
                source_path="docs/adr/analytics-contract.md",
                markdown_url=None,
                summary="ADR for analytics contract",
                body_text=body,
                metadata={},
            )
        ], []


class CountingEmbedder:
    calls = 0

    def __init__(self, connection):
        self._connection = connection

    def embed(self, text: str) -> list[float]:
        CountingEmbedder.calls += 1
        return [0.0] * self._connection.vector_size


class CountingStore:
    delete_calls = 0
    upsert_calls = 0

    def __init__(self, connection):
        self._connection = connection

    def delete_page_points(self, **_kwargs) -> None:
        CountingStore.delete_calls += 1

    def upsert_chunks(self, chunks) -> None:
        CountingStore.upsert_calls += 1


class StaticSearchEmbedder:
    def __init__(self, connection):
        self._connection = connection

    def embed(self, _text: str) -> list[float]:
        return [0.0] * self._connection.vector_size


class StaticSemanticStore:
    hits: ClassVar[list[dict[str, object]]] = []

    def __init__(self, _connection):
        pass

    def search(self, **_kwargs) -> list[dict[str, object]]:
        return self.hits


def _upsert_documentation_page(
    repository: DocumentationConnectorRepository,
    *,
    page_ref: str,
    title: str,
    body_text: str,
    point_id: str,
) -> None:
    repository.upsert_page_with_chunks(
        workspace_id="ws-1",
        connector_name="platform_docs",
        page_ref=page_ref,
        url=f"https://docs.example.com{page_ref}",
        title=title,
        source_path=None,
        markdown_url=None,
        summary=None,
        body_text=body_text,
        body_hash=f"hash-{point_id}",
        metadata_json={},
        chunks=[
            {
                "chunk_ref": f"{page_ref}#0",
                "chunk_index": 0,
                "section": None,
                "content": body_text,
                "content_hash": f"chunk-hash-{point_id}",
                "qdrant_point_id": point_id,
            }
        ],
    )


def test_sync_stores_pages_and_chunks_without_semantic_index(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    connector = _connector(repository)
    service = OfflineDocumentationSyncService(repository=repository)

    result = service.sync_connector(connector=connector, trigger="manual")

    assert result.status == "succeeded"
    assert result.pages_seen == 1
    assert result.pages_updated == 1
    assert result.chunks_indexed == 0
    assert "documentation_embedding_base_url_missing" in result.warnings

    search_results = repository.search_chunks(
        workspace_id="ws-1",
        query="analytics-contract",
        connector_name="platform_docs",
        limit=5,
    )
    assert search_results[0]["page_ref"] == "/docs/adr/analytics-contract/"


def test_sync_does_not_delete_semantic_points_for_unchanged_page(tmp_path: Path) -> None:
    CountingEmbedder.calls = 0
    CountingStore.delete_calls = 0
    CountingStore.upsert_calls = 0
    repository = _repository(tmp_path)
    connector = _connector(repository, embedding_base_url="https://embed.example.com")
    service = OfflineDocumentationSyncService(
        repository=repository,
        embedding_provider_factory=CountingEmbedder,
        qdrant_store_factory=CountingStore,
    )

    first = service.sync_connector(connector=connector, trigger="manual")
    second = service.sync_connector(connector=connector, trigger="manual")

    assert first.chunks_indexed > 0
    assert second.pages_updated == 0
    assert second.chunks_indexed == 0
    assert CountingStore.delete_calls == 1
    assert CountingStore.upsert_calls == 1


def test_renaming_connector_drops_old_indexed_rows(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    connector = _connector(repository)
    OfflineDocumentationSyncService(repository=repository).sync_connector(
        connector=connector,
        trigger="manual",
    )

    updated = repository.update(
        connector_id=connector.id,
        workspace_id="ws-1",
        request=UpsertDocumentationConnectorRequest(
            name="platform_docs_v2",
            workspace_id="ws-1",
            is_active=True,
            connection=connector.connection,
        ),
    )

    assert updated is not None
    assert repository.search_chunks(
        workspace_id="ws-1",
        query="analytics-contract",
        connector_name="platform_docs",
        limit=5,
    ) == []


def test_default_connector_names_respect_visibility_and_use_when(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    visible = _connector(repository, name="visible_docs", visible_to_default_assistant=True)
    _connector(repository, name="hidden_docs", visible_to_default_assistant=False)

    assert visible.connection.usage_description == "Use for platform docs."
    assert DocumentationRetrievalService(repository).default_connector_names("ws-1") == [
        "visible_docs"
    ]


def test_read_page_respects_token_budget(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    connector = _connector(repository)
    OfflineDocumentationSyncService(repository=repository).sync_connector(
        connector=connector,
        trigger="manual",
    )

    page, warnings = DocumentationRetrievalService(repository).read_page(
        workspace_id="ws-1",
        connector_name="platform_docs",
        page_ref="/docs/adr/analytics-contract/",
        max_tokens=200,
    )

    assert page is not None
    assert len(page["content"].split()) <= 150
    assert warnings == ["documentation_read_budget_reached"]


def test_merge_results_promotes_semantic_hits_before_lexical_only_hits() -> None:
    merged = DocumentationRetrievalService._merge_results(
        lexical=[
            {
                "chunk_ref": "exact",
                "page_ref": "/docs/api/exact/",
            }
        ],
        semantic=[
            {
                "chunk_ref": "semantic-only",
                "page_ref": "/docs/api/nearby/",
                "semantic_score": 0.99,
            }
        ],
    )

    assert merged[0]["chunk_ref"] == "semantic-only"
    assert merged[1]["chunk_ref"] == "exact"
    assert all("_rank_order" not in item for item in merged)


def test_merge_results_keeps_one_chunk_per_page() -> None:
    merged = DocumentationRetrievalService._merge_results(
        lexical=[
            {
                "chunk_ref": "page-a#0",
                "page_ref": "/docs/page-a/",
            },
            {
                "chunk_ref": "page-a#1",
                "page_ref": "/docs/page-a/",
            },
            {
                "chunk_ref": "page-b#0",
                "page_ref": "/docs/page-b/",
            },
        ],
        semantic=[],
    )

    assert [item["chunk_ref"] for item in merged] == ["page-a#0", "page-b#0"]


def test_search_chunks_filters_stopwords_but_keeps_short_domain_terms() -> None:
    terms = DocumentationConnectorRepository._lexical_terms(
        ["как", "и", "создать", "dq", "sql", "в"]
    )

    assert terms == ["создать", "dq", "sql"]


def test_search_chunks_ranks_lexical_matches_before_fresher_broad_matches(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _connector(repository)
    _upsert_documentation_page(
        repository,
        page_ref="/platform_docs/Vera/DQ/create-quality-check/",
        title="Vera DQ: как создать проверку качества данных",
        body_text="Инструкция Vera DQ описывает как создать проверку качества данных.",
        point_id="vera-dq-create",
    )
    _upsert_documentation_page(
        repository,
        page_ref="/platform_docs/data-catalog/deploy/",
        title="Data Catalog deploy",
        body_text="Как создать deploy data catalog для сервиса данных.",
        point_id="data-catalog-deploy",
    )

    results = repository.search_chunks(
        workspace_id="ws-1",
        query="как создать проверку качества данных?",
        connector_name="platform_docs",
        limit=3,
    )

    assert results[0]["page_ref"] == "/platform_docs/Vera/DQ/create-quality-check/"


def test_search_chunks_boosts_exact_phrase_over_shuffled_term_matches(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _connector(repository)
    _upsert_documentation_page(
        repository,
        page_ref="/platform_docs/broad/shuffled/",
        title="создать данные проверку качества",
        body_text="Общий раздел про создание и данные.",
        point_id="broad-shuffled",
    )
    _upsert_documentation_page(
        repository,
        page_ref="/platform_docs/Vera/DQ/create-quality-check/",
        title="Vera DQ",
        body_text="создать проверку качества данных в Vera.",
        point_id="vera-exact-phrase",
    )

    results = repository.search_chunks(
        workspace_id="ws-1",
        query="как создать проверку качества данных?",
        connector_name="platform_docs",
        limit=2,
    )

    assert results[0]["page_ref"] == "/platform_docs/Vera/DQ/create-quality-check/"


def test_search_documentation_returns_vera_dq_semantic_hits_in_top_three(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = _repository(tmp_path)
    _connector(repository, embedding_base_url="https://embed.example.com")
    _upsert_documentation_page(
        repository,
        page_ref="/platform_docs/data-catalog/deploy/",
        title="Data Catalog deploy",
        body_text="Как создать deploy data catalog для сервиса данных.",
        point_id="data-catalog-deploy",
    )
    relevant_pages = [
        (
            "/platform_docs/Vera/DQ/create-quality-check/",
            "Vera DQ: как создать проверку качества данных",
            "Инструкция Vera DQ описывает как создать проверку качества данных.",
            "vera-dq-create",
        ),
        (
            "/platform_docs/Vera/DQ/checks/",
            "Vera DQ проверки качества данных",
            "Раздел объясняет создание и настройку проверки качества данных в Vera.",
            "vera-dq-checks",
        ),
        (
            "/platform_docs/Vera/DQ/rules/",
            "Vera DQ правила проверок",
            "Правила Vera для проверок качества данных и запуска контроля.",
            "vera-dq-rules",
        ),
    ]
    for page_ref, title, body_text, point_id in relevant_pages:
        _upsert_documentation_page(
            repository,
            page_ref=page_ref,
            title=title,
            body_text=body_text,
            point_id=point_id,
        )
    StaticSemanticStore.hits = [
        {"point_id": "vera-dq-create", "semantic_score": 0.91},
        {"point_id": "vera-dq-checks", "semantic_score": 0.9},
        {"point_id": "vera-dq-rules", "semantic_score": 0.89},
    ]
    monkeypatch.setattr(retrieval_module, "OpenAiCompatibleEmbeddingProvider", StaticSearchEmbedder)
    monkeypatch.setattr(retrieval_module, "QdrantDocumentationStore", StaticSemanticStore)

    results, warnings = DocumentationRetrievalService(repository).search(
        workspace_id="ws-1",
        query="как создать проверку качества данных?",
        connector_name="platform_docs",
        limit=3,
    )

    assert warnings == []
    top_refs = [item["page_ref"] for item in results]
    assert all("/platform_docs/Vera/DQ/" in page_ref for page_ref in top_refs)
    assert "/platform_docs/data-catalog/deploy/" not in top_refs


def test_embedding_url_accepts_versioned_openai_base_url() -> None:
    assert (
        OpenAiCompatibleEmbeddingProvider._embedding_url(
            base_url="https://llmlite.example.com/v1",
            endpoint_path="/v1/embeddings",
        )
        == "https://llmlite.example.com/v1/embeddings"
    )


def test_embedding_request_uses_float_encoding(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [{"embedding": [0.1] * 8}]}

    def post(url: str, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(embedding_module.httpx, "post", post)
    connection = DocumentationConnectionConfig(
        base_url="https://docs.example.com",
        embedding_base_url="https://llmlite.example.com/v1",
        embedding_api_key="secret",
        embedding_model="BAAI/bge-small-en-v1.5",
        vector_size=8,
    )

    vector = OpenAiCompatibleEmbeddingProvider(connection).embed("document text")

    assert vector == [0.1] * 8
    assert captured["url"] == "https://llmlite.example.com/v1/embeddings"
    assert captured["json"] == {
        "model": "BAAI/bge-small-en-v1.5",
        "input": "document text",
        "encoding_format": "float",
    }


def test_chunk_splitter_caps_oversized_dense_text() -> None:
    text = " ".join(["https://example.com/very/long/path/with/query?value=1234567890"] * 80)

    parts = DocumentationSyncService._split_oversized_chunk(text)

    assert len(parts) > 1
    assert max(len(part) for part in parts) <= DocumentationSyncService._MAX_CHUNK_CHARS


def test_builtin_documentation_connector_reads_explicit_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DOCUMENTATION_BASE_URL", "https://docs.example.com/platform_docs")
    monkeypatch.setenv("DOCUMENTATION_INCLUDE_PATHS", "/docs/,/platform_docs/docs/")
    monkeypatch.setenv("DOCUMENTATION_EMBEDDING_BASE_URL", "https://llmlite.example.com/v1")
    monkeypatch.setenv("DOCUMENTATION_EMBEDDING_API_KEY", "secret")
    monkeypatch.setenv("DOCUMENTATION_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    monkeypatch.setenv("DOCUMENTATION_VECTOR_SIZE", "1024")
    repository = _repository(tmp_path)

    connection = repository.get_connection(workspace_id="default", name="platform_docs")

    assert connection is not None
    assert connection.include_paths == ["/docs/", "/platform_docs/docs/"]
    assert connection.embedding_base_url == "https://llmlite.example.com/v1"
    assert connection.embedding_api_key == "secret"
    assert connection.embedding_model == "BAAI/bge-small-en-v1.5"
    assert connection.vector_size == 1024
