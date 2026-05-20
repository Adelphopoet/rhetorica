from __future__ import annotations

from typing import Any, ClassVar

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)
from rhetorica_backend.contracts.admin_integrations import RagConnectionConfig


class QdrantInsightsStore:
    """Thin storage adapter for RAG insights in Qdrant."""

    _DISTANCE_MAP: ClassVar[dict[str, Distance]] = {
        "cosine": Distance.COSINE,
        "dot": Distance.DOT,
        "euclid": Distance.EUCLID,
    }

    def __init__(self, connection: RagConnectionConfig):
        self._connection = connection
        self._client = QdrantClient(
            url=connection.base_url,
            api_key=connection.api_key,
            prefer_grpc=connection.prefer_grpc,
        )

    def upsert(self, point_id: str, vector: list[float], payload: dict[str, object]) -> None:
        self._ensure_collection()
        self._client.upsert(
            collection_name=self._connection.collection_name,
            wait=True,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload,
                )
            ],
        )

    def search_similar(
        self,
        workspace_id: str,
        vector: list[float],
        limit: int,
        score_threshold: float,
    ) -> list[dict[str, Any]]:
        if not self._collection_exists():
            return []
        response = self._client.query_points(
            collection_name=self._connection.collection_name,
            query=vector,
            query_filter=Filter(
                must=[FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id))],
                must_not=[
                    FieldCondition(
                        key="verification_status",
                        match=MatchValue(value="rejected"),
                    )
                ],
            ),
            limit=limit,
            with_payload=True,
            with_vectors=False,
            score_threshold=score_threshold,
        )
        points = response.points if hasattr(response, "points") else []
        results: list[dict[str, Any]] = []
        for point in points:
            payload = point.payload if isinstance(point.payload, dict) else {}
            score = float(point.score) if point.score is not None else 0.0
            results.append(
                {
                    "id": str(point.id),
                    "score": score,
                    "payload": payload,
                }
            )
        return results

    def search_by_status(
        self,
        workspace_id: str,
        verification_status: str,
        vector: list[float],
        limit: int,
        score_threshold: float,
    ) -> list[dict[str, Any]]:
        if not self._collection_exists():
            return []

        response = self._client.query_points(
            collection_name=self._connection.collection_name,
            query=vector,
            query_filter=Filter(
                must=[
                    FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id)),
                    FieldCondition(
                        key="verification_status",
                        match=MatchValue(value=verification_status),
                    ),
                ]
            ),
            limit=limit,
            with_payload=True,
            with_vectors=False,
            score_threshold=score_threshold,
        )
        points = response.points if hasattr(response, "points") else []
        results: list[dict[str, Any]] = []
        for point in points:
            payload = point.payload if isinstance(point.payload, dict) else {}
            score = float(point.score) if point.score is not None else 0.0
            results.append(
                {
                    "id": str(point.id),
                    "score": score,
                    "payload": payload,
                }
            )
        return results

    def update_payload(self, point_id: str, payload: dict[str, object]) -> None:
        self._client.set_payload(
            collection_name=self._connection.collection_name,
            payload=payload,
            points=[point_id],
            wait=True,
        )

    def list_pending(self, workspace_id: str, limit: int) -> list[dict[str, Any]]:
        if not self._collection_exists():
            return []
        records, _ = self._client.scroll(
            collection_name=self._connection.collection_name,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id)),
                    FieldCondition(key="verification_status", match=MatchValue(value="pending")),
                ]
            ),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        result: list[dict[str, Any]] = []
        for record in records:
            payload = record.payload if isinstance(record.payload, dict) else {}
            result.append(
                {
                    "id": str(record.id),
                    "payload": payload,
                }
            )
        return result

    def get_record(self, point_id: str) -> dict[str, Any] | None:
        if not self._collection_exists():
            return None
        points = self._client.retrieve(
            collection_name=self._connection.collection_name,
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            return None
        payload = points[0].payload if isinstance(points[0].payload, dict) else {}
        return {"id": str(points[0].id), "payload": payload}

    def set_verification(
        self,
        point_id: str,
        status: str,
        verified_by: str,
        verified_at: str,
    ) -> None:
        self._client.set_payload(
            collection_name=self._connection.collection_name,
            payload={
                "verification_status": status,
                "human_verified": True,
                "verified_by": verified_by,
                "verified_at": verified_at,
                "updated_at": verified_at,
            },
            points=[point_id],
            wait=True,
        )

    def _ensure_collection(self) -> None:
        if self._collection_exists():
            return
        self._client.create_collection(
            collection_name=self._connection.collection_name,
            vectors_config=VectorParams(
                size=self._connection.vector_size,
                distance=self._DISTANCE_MAP[self._connection.distance],
            ),
        )

    def _collection_exists(self) -> bool:
        return bool(self._client.collection_exists(self._connection.collection_name))
