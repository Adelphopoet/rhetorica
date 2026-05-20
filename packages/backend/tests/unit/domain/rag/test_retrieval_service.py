from __future__ import annotations

from rhetorica_backend.contracts.admin_integrations import RagConnectionConfig
from rhetorica_backend.domain.rag import retrieval_service as retrieval_module
from rhetorica_backend.domain.rag.retrieval_service import RagInsightRetriever


class _RepoStub:
    def get_connection(self, workspace_id: str, name: str | None = None) -> RagConnectionConfig | None:
        _ = workspace_id, name
        return RagConnectionConfig(
            provider="qdrant",
            base_url="http://qdrant:6333",
            api_key=None,
            collection_name="rag_insights",
            vector_size=8,
            distance="cosine",
            prefer_grpc=False,
            embedding_model="BAAI/bge-small-en-v1.5",
            confidence_threshold=0.7,
            extra_settings={"retrieval_similarity_threshold": 0.5},
        )


def test_retrieval_search_returns_ranked_snippets(monkeypatch) -> None:
    class _FakeStore:
        def __init__(self, _connection):
            pass

        def search_by_status(
            self,
            workspace_id: str,
            verification_status: str,
            vector: list[float],
            limit: int,
            score_threshold: float,
        ) -> list[dict]:
            assert workspace_id == "default"
            assert vector
            assert limit == 5
            assert score_threshold == 0.45
            if verification_status == "verified":
                return [
                    {
                        "id": "v-1",
                        "score": 0.91,
                        "payload": {
                            "verification_status": "verified",
                            "summary": "Verified insight",
                            "article_markdown": "# Insight\n\nVerified",
                            "keywords": ["verified"],
                            "context_tables": ["dm.verified"],
                        },
                    }
                ]
            return [
                {
                    "id": "p-1",
                    "score": 0.84,
                    "payload": {
                        "verification_status": "pending",
                        "summary": "Pending insight",
                        "article_markdown": "# Insight\n\nPending",
                        "keywords": ["pending"],
                        "context_tables": ["dm.pending"],
                    },
                }
            ]

    class _FakeEmbedder:
        def __init__(self, _model_name: str):
            pass

        def embed(self, _text: str) -> list[float]:
            return [0.1] * 8

    monkeypatch.setattr(retrieval_module, "QdrantInsightsStore", _FakeStore)
    monkeypatch.setattr(retrieval_module, "FastEmbedProvider", _FakeEmbedder)

    retriever = RagInsightRetriever(_RepoStub())
    results, warnings = retriever.search(
        workspace_id="default",
        query="retention",
        limit=5,
    )

    assert warnings == []
    assert [row["id"] for row in results] == ["v-1", "p-1"]
    assert results[0]["verification_status"] == "verified"
    assert results[1]["verification_status"] == "pending"
    assert "key_facts" in results[0]
    assert "citation" in results[0]
