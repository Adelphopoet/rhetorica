from __future__ import annotations

from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)
from rhetorica_backend.contracts.admin_integrations import DocumentationConnectionConfig


class QdrantDocumentationStore:
    """Qdrant adapter for documentation chunk vectors only."""

    def __init__(self, connection: DocumentationConnectionConfig):
        self._connection = connection
        self._client = QdrantClient(
            url=connection.qdrant_base_url,
            api_key=connection.qdrant_api_key,
            prefer_grpc=connection.qdrant_prefer_grpc,
        )

    def upsert_chunks(self, chunks: list[dict[str, Any]]) -> None:
        if not chunks:
            return
        self._ensure_collection()
        self._client.upsert(
            collection_name=self._connection.qdrant_collection_name,
            wait=True,
            points=[
                PointStruct(
                    id=chunk["qdrant_point_id"],
                    vector=chunk["vector"],
                    payload={
                        "workspace_id": chunk["workspace_id"],
                        "connector_name": chunk["connector_name"],
                        "page_ref": chunk["page_ref"],
                        "chunk_ref": chunk["chunk_ref"],
                        "url": chunk["url"],
                        "title": chunk["title"],
                        "section": chunk.get("section") or "",
                    },
                )
                for chunk in chunks
            ],
        )

    def search(
        self,
        *,
        workspace_id: str,
        connector_name: str | None,
        vector: list[float],
        limit: int,
        score_threshold: float = 0.0,
    ) -> list[dict[str, Any]]:
        if not self._collection_exists():
            return []
        must = [FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id))]
        if connector_name:
            must.append(FieldCondition(key="connector_name", match=MatchValue(value=connector_name)))
        response = self._client.query_points(
            collection_name=self._connection.qdrant_collection_name,
            query=vector,
            query_filter=Filter(must=must),
            limit=limit,
            with_payload=True,
            with_vectors=False,
            score_threshold=score_threshold,
        )
        points = response.points if hasattr(response, "points") else []
        return [
            {
                "point_id": str(point.id),
                "semantic_score": float(point.score or 0.0),
                "payload": point.payload if isinstance(point.payload, dict) else {},
            }
            for point in points
        ]

    def delete_connector_points(self, *, workspace_id: str, connector_name: str) -> None:
        if not self._collection_exists():
            return
        self._client.delete(
            collection_name=self._connection.qdrant_collection_name,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id)),
                        FieldCondition(
                            key="connector_name",
                            match=MatchValue(value=connector_name),
                        ),
                    ]
                )
            ),
            wait=True,
        )

    def delete_page_points(
        self,
        *,
        workspace_id: str,
        connector_name: str,
        page_ref: str,
    ) -> None:
        if not self._collection_exists():
            return
        self._client.delete(
            collection_name=self._connection.qdrant_collection_name,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id)),
                        FieldCondition(key="connector_name", match=MatchValue(value=connector_name)),
                        FieldCondition(key="page_ref", match=MatchValue(value=page_ref)),
                    ]
                )
            ),
            wait=True,
        )

    def _ensure_collection(self) -> None:
        if self._collection_exists():
            return
        self._client.create_collection(
            collection_name=self._connection.qdrant_collection_name,
            vectors_config=VectorParams(size=self._connection.vector_size, distance=Distance.COSINE),
        )
        for field_name in ("workspace_id", "connector_name", "page_ref", "chunk_ref"):
            self._client.create_payload_index(
                collection_name=self._connection.qdrant_collection_name,
                field_name=field_name,
                field_schema=PayloadSchemaType.KEYWORD,
            )

    def _collection_exists(self) -> bool:
        return bool(self._client.collection_exists(self._connection.qdrant_collection_name))
