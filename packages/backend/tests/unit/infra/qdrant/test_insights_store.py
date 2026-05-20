from __future__ import annotations

from rhetorica_backend.contracts.admin_integrations import RagConnectionConfig
from rhetorica_backend.infra.qdrant import insights_store as insights_store_module
from rhetorica_backend.infra.qdrant.insights_store import QdrantInsightsStore


class _FakeQdrantClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.exists = False
        self.created: dict[str, object] | None = None
        self.upserted: dict[str, object] | None = None

    def collection_exists(self, _collection_name: str) -> bool:
        return self.exists

    def create_collection(self, collection_name: str, vectors_config) -> None:
        self.exists = True
        self.created = {
            "collection_name": collection_name,
            "vector_size": vectors_config.size,
            "distance": str(vectors_config.distance).lower(),
        }

    def upsert(self, collection_name: str, wait: bool, points: list[object]) -> None:
        self.upserted = {
            "collection_name": collection_name,
            "wait": wait,
            "points": points,
        }

    def scroll(self, **kwargs):
        return ([], None)

    def retrieve(self, **kwargs):
        return []

    def set_payload(self, **kwargs):
        return None


def test_qdrant_upsert_creates_collection_and_writes_point(monkeypatch) -> None:
    fake_client = _FakeQdrantClient()
    monkeypatch.setattr(insights_store_module, "QdrantClient", lambda **kwargs: fake_client)

    store = QdrantInsightsStore(
        RagConnectionConfig(
            provider="qdrant",
            base_url="http://qdrant:6333",
            api_key="secret",
            collection_name="rag_insights",
            vector_size=8,
            distance="cosine",
            embedding_model="BAAI/bge-small-en-v1.5",
            confidence_threshold=0.7,
        )
    )
    store.upsert(
        point_id="point-1",
        vector=[0.1, 0.2, 0.3, 0.4],
        payload={"workspace_id": "default", "verification_status": "pending"},
    )

    assert fake_client.created is not None
    assert fake_client.created["collection_name"] == "rag_insights"
    assert fake_client.created["vector_size"] == 8
    assert fake_client.upserted is not None
    assert fake_client.upserted["collection_name"] == "rag_insights"
    assert fake_client.upserted["wait"] is True
