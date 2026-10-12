from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

from rhetorica_backend.contracts.admin_integrations import (
    RagConnectionConfig,
    RagConnectorItem,
    UpsertRagConnectorRequest,
)
from rhetorica_backend.contracts.workspaces import WorkspaceResourceKind
from rhetorica_backend.infra.postgres.models import RagConnectorConfigOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.workspace_resource_access import (
    WorkspaceResourceAccess,
    present_resource,
)
from rhetorica_backend.infra.secrets import SecretStore, SecretStoreError, get_secret_store
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


class RagConnectorRepository:
    """Persistence gateway for RAG (Qdrant) connector settings."""

    def __init__(self, db: DatabaseRuntime, secret_store: SecretStore | None = None):
        self._db = db
        self._secret_store = secret_store or get_secret_store()
        self._resources = WorkspaceResourceAccess(
            db,
            kind=WorkspaceResourceKind.RAG_CONNECTOR,
            model=RagConnectorConfigOrm,
        )

    def upsert(self, request: UpsertRagConnectorRequest) -> RagConnectorItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            stmt = select(RagConnectorConfigOrm).where(
                RagConnectorConfigOrm.workspace_id == request.workspace_id,
                RagConnectorConfigOrm.name == request.name,
            )
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                row = RagConnectorConfigOrm(
                    id=str(uuid4()),
                    workspace_id=request.workspace_id,
                    name=request.name,
                    is_active=request.is_active,
                    created_at=now,
                    updated_at=now,
                    provider=request.connection.provider,
                    base_url=request.connection.base_url,
                    api_key=request.connection.api_key,
                    collection_name=request.connection.collection_name,
                    vector_size=request.connection.vector_size,
                    distance=request.connection.distance,
                    prefer_grpc=request.connection.prefer_grpc,
                    embedding_model=request.connection.embedding_model,
                    confidence_threshold=request.connection.confidence_threshold,
                    extra_settings=request.connection.extra_settings,
                )
                session.add(row)
            else:
                row.updated_at = now
                row.is_active = request.is_active
                row.provider = request.connection.provider
                row.base_url = request.connection.base_url
                row.api_key = request.connection.api_key or row.api_key
                row.collection_name = request.connection.collection_name
                row.vector_size = request.connection.vector_size
                row.distance = request.connection.distance
                row.prefer_grpc = request.connection.prefer_grpc
                row.embedding_model = request.connection.embedding_model
                row.confidence_threshold = request.connection.confidence_threshold
                row.extra_settings = request.connection.extra_settings

            if request.connection.api_key is not None:
                secret_ref = row.api_key_secret_ref or self._api_key_secret_ref(
                    workspace_id=request.workspace_id,
                    connector_id=row.id,
                )
                self._secret_store.set_secret(
                    workspace_id=request.workspace_id,
                    secret_key=secret_ref,
                    value=request.connection.api_key,
                )
                row.api_key_secret_ref = secret_ref
                row.api_key = None

            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row)

    def update(
        self,
        connector_id: str,
        workspace_id: str,
        request: UpsertRagConnectorRequest,
    ) -> RagConnectorItem | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(RagConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return None

            row.updated_at = now
            row.name = request.name
            row.is_active = request.is_active
            row.provider = request.connection.provider
            row.base_url = request.connection.base_url
            row.api_key = request.connection.api_key or row.api_key
            row.collection_name = request.connection.collection_name
            row.vector_size = request.connection.vector_size
            row.distance = request.connection.distance
            row.prefer_grpc = request.connection.prefer_grpc
            row.embedding_model = request.connection.embedding_model
            row.confidence_threshold = request.connection.confidence_threshold
            row.extra_settings = request.connection.extra_settings

            if request.connection.api_key is not None:
                secret_ref = row.api_key_secret_ref or self._api_key_secret_ref(
                    workspace_id=workspace_id,
                    connector_id=row.id,
                )
                self._secret_store.set_secret(
                    workspace_id=workspace_id,
                    secret_key=secret_ref,
                    value=request.connection.api_key,
                )
                row.api_key_secret_ref = secret_ref
                row.api_key = None

            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row)

    def list(self, workspace_id: str, limit: int) -> list[RagConnectorItem]:
        return [
            present_resource(self._to_contract(resource.row), resource)
            for resource in self._resources.list(workspace_id=workspace_id, limit=limit)
        ]

    def get(self, workspace_id: str, name: str | None = None) -> RagConnectorItem | None:
        resource = next(
            (
                item
                for item in self._resources.list(workspace_id=workspace_id, limit=None)
                if name is None or item.alias == name
            ),
            None,
        )
        if resource is None:
            return None
        return present_resource(self._to_contract(resource.row), resource)

    def get_connection(
        self, workspace_id: str, name: str | None = None
    ) -> RagConnectionConfig | None:
        resource = self._resources.first_active(workspace_id=workspace_id, name=name)
        if resource is None:
            if self._resources.list(workspace_id=workspace_id, limit=1):
                return None
            return self._builtin_connection_from_env()
        row = resource.row
        return RagConnectionConfig(
            provider=row.provider,
            base_url=row.base_url,
            api_key=self._resolve_api_key(row),
            collection_name=row.collection_name,
            vector_size=row.vector_size,
            distance=row.distance,
            prefer_grpc=row.prefer_grpc,
            embedding_model=row.embedding_model,
            confidence_threshold=row.confidence_threshold,
            extra_settings=row.extra_settings,
        )

    def delete(self, connector_id: str, workspace_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(RagConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return False
            self._delete_secret(row.workspace_id, row.api_key_secret_ref)
            session.delete(row)
            session.commit()
            return True

    @staticmethod
    def _to_contract(row: RagConnectorConfigOrm) -> RagConnectorItem:
        connection = RagConnectionConfig(
            provider=row.provider,
            base_url=row.base_url,
            api_key=None,
            collection_name=row.collection_name,
            vector_size=row.vector_size,
            distance=row.distance,
            prefer_grpc=row.prefer_grpc,
            embedding_model=row.embedding_model,
            confidence_threshold=row.confidence_threshold,
            extra_settings=row.extra_settings,
        )
        return RagConnectorItem(
            id=row.id,
            name=row.name,
            workspace_id=row.workspace_id,
            is_active=row.is_active,
            connection=connection,
            has_api_key=bool(row.api_key_secret_ref or row.api_key),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _resolve_api_key(self, row: RagConnectorConfigOrm) -> str | None:
        if row.api_key_secret_ref:
            token = self._secret_store.get_secret(
                workspace_id=row.workspace_id,
                secret_key=row.api_key_secret_ref,
            )
            if token is not None:
                return token
        return row.api_key

    def _delete_secret(self, workspace_id: str, secret_ref: str | None) -> None:
        if not secret_ref:
            return
        try:
            self._secret_store.delete_secret(workspace_id=workspace_id, secret_key=secret_ref)
        except SecretStoreError:
            return

    @staticmethod
    def _api_key_secret_ref(workspace_id: str, connector_id: str) -> str:
        return f"rag_connector/{workspace_id}/{connector_id}/api_key"

    @staticmethod
    def _builtin_connection_from_env() -> RagConnectionConfig | None:
        base_url = os.getenv("RAG_QDRANT_URL") or os.getenv("QDRANT_URL") or "http://qdrant:6333"
        base_url = base_url.strip()
        if not base_url:
            return None

        vector_size = RagConnectorRepository._env_int("RAG_VECTOR_SIZE", 384)
        confidence_threshold = RagConnectorRepository._env_float("RAG_CONFIDENCE_THRESHOLD", 0.7)
        distance = (os.getenv("RAG_DISTANCE") or "cosine").strip().lower()
        prefer_grpc = RagConnectorRepository._env_bool("RAG_PREFER_GRPC", False)
        embedding_model = (os.getenv("RAG_EMBEDDING_MODEL") or "BAAI/bge-small-en-v1.5").strip()
        api_key = (os.getenv("RAG_QDRANT_API_KEY") or "").strip() or None

        extra_settings: dict[str, str | int | float | bool] = {}
        merge_threshold_raw = os.getenv("RAG_MERGE_SIMILARITY_THRESHOLD")
        if merge_threshold_raw:
            try:
                merge_threshold = float(merge_threshold_raw)
            except ValueError:
                merge_threshold = None
            if merge_threshold is not None and 0 <= merge_threshold <= 1:
                extra_settings["merge_similarity_threshold"] = merge_threshold

        return RagConnectionConfig(
            provider="qdrant",
            base_url=base_url,
            api_key=api_key,
            collection_name=(os.getenv("RAG_COLLECTION_NAME") or "rag_insights").strip(),
            vector_size=max(8, min(vector_size, 4096)),
            distance=distance if distance in {"cosine", "dot", "euclid"} else "cosine",
            prefer_grpc=prefer_grpc,
            embedding_model=embedding_model or "BAAI/bge-small-en-v1.5",
            confidence_threshold=min(max(confidence_threshold, 0.0), 1.0),
            extra_settings=extra_settings,
        )

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return int(value.strip())
        except ValueError:
            return default

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return float(value.strip())
        except ValueError:
            return default
