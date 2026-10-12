from __future__ import annotations

from typing import Protocol

import httpx
from rhetorica_backend.contracts.admin_integrations import DocumentationConnectionConfig


class DocumentationEmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]: ...


class OpenAiCompatibleEmbeddingProvider:
    """OpenAI-compatible embedding client used for documentation chunks."""

    def __init__(self, connection: DocumentationConnectionConfig):
        self._connection = connection

    def embed(self, text: str) -> list[float]:
        base_url = (self._connection.embedding_base_url or "").rstrip("/")
        if not base_url:
            raise RuntimeError("documentation_embedding_base_url_missing")
        endpoint_path = self._connection.embedding_endpoint_path
        url = self._embedding_url(base_url=base_url, endpoint_path=endpoint_path)
        headers: dict[str, str] = {}
        if self._connection.embedding_api_key:
            headers["Authorization"] = f"Bearer {self._connection.embedding_api_key}"
        response = httpx.post(
            url,
            json={
                "model": self._connection.embedding_model,
                "input": text,
                "encoding_format": "float",
            },
            headers=headers,
            timeout=self._connection.embedding_timeout_seconds,
            verify=self._connection.verify_tls,
        )
        response.raise_for_status()
        data = response.json()
        vector = self._extract_vector(data)
        self._validate_vector_size(vector)
        return vector

    @staticmethod
    def _embedding_url(*, base_url: str, endpoint_path: str) -> str:
        normalized_path = "/" + endpoint_path.lstrip("/")
        if base_url.endswith(normalized_path.rstrip("/")):
            return base_url
        if normalized_path.startswith("/v1/") and base_url.endswith("/v1"):
            return f"{base_url}/{normalized_path.removeprefix('/v1/')}"
        return f"{base_url}/{normalized_path.lstrip('/')}"

    @staticmethod
    def _extract_vector(data: object) -> list[float]:
        if isinstance(data, dict):
            records = data.get("data")
            if isinstance(records, list) and records:
                first = records[0]
                if isinstance(first, dict) and isinstance(first.get("embedding"), list):
                    return [float(value) for value in first["embedding"]]
            embedding = data.get("embedding")
            if isinstance(embedding, list):
                return [float(value) for value in embedding]
        raise RuntimeError("documentation_embedding_response_invalid")

    def _validate_vector_size(self, vector: list[float]) -> None:
        if len(vector) != self._connection.vector_size:
            raise RuntimeError(
                f"documentation_embedding_vector_size_mismatch:"
                f"expected={self._connection.vector_size}:actual={len(vector)}"
            )
