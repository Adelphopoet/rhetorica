from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import uuid4

from rhetorica_backend.contracts.admin_integrations import (
    DocumentationConnectionConfig,
    DocumentationConnectorItem,
    DocumentationSyncRunItem,
    UpsertDocumentationConnectorRequest,
)
from rhetorica_backend.infra.postgres.models import (
    DocumentationChunkOrm,
    DocumentationConnectorConfigOrm,
    DocumentationPageOrm,
    DocumentationSyncRunOrm,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.secrets import SecretStore, get_secret_store
from sqlalchemy import case, delete, literal, or_, select
from sqlalchemy.exc import IntegrityError


class DocumentationConnectorRepository:
    """Persistence gateway for documentation connectors, pages, chunks, and sync runs."""

    _LEXICAL_KEEP_SHORT_TERMS: ClassVar[frozenset[str]] = frozenset(
        {"ai", "bi", "db", "dq", "id", "llm", "sql", "yc"}
    )
    _LEXICAL_STOPWORDS: ClassVar[frozenset[str]] = frozenset(
        {
            "a",
            "an",
            "and",
            "are",
            "as",
            "at",
            "be",
            "by",
            "for",
            "from",
            "how",
            "in",
            "is",
            "of",
            "on",
            "or",
            "the",
            "to",
            "what",
            "when",
            "where",
            "who",
            "why",
            "а",
            "бы",
            "в",
            "во",
            "где",
            "для",
            "до",
            "его",
            "ее",
            "если",
            "же",
            "за",
            "зачем",
            "и",
            "из",
            "или",
            "как",
            "к",
            "когда",
            "ко",
            "кто",
            "куда",
            "ли",
            "на",
            "надо",
            "но",
            "нужно",
            "о",
            "об",
            "от",
            "по",
            "почему",
            "с",
            "со",
            "то",
            "того",
            "что",
            "чтобы",
            "это",
            "эта",
            "эти",
            "этот",
        }
    )

    def __init__(self, db: DatabaseRuntime, secret_store: SecretStore | None = None):
        self._db = db
        self._secret_store = secret_store or get_secret_store()

    def upsert(self, request: UpsertDocumentationConnectorRequest) -> DocumentationConnectorItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.execute(
                select(DocumentationConnectorConfigOrm).where(
                    DocumentationConnectorConfigOrm.workspace_id == request.workspace_id,
                    DocumentationConnectorConfigOrm.name == request.name,
                )
            ).scalar_one_or_none()
            if row is None:
                row = DocumentationConnectorConfigOrm(
                    id=str(uuid4()),
                    workspace_id=request.workspace_id,
                    name=request.name,
                    created_at=now,
                    updated_at=now,
                    next_sync_at=now if request.connection.sync_enabled else None,
                )
                session.add(row)
            self._apply_connection(row=row, request=request, now=now)
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
        request: UpsertDocumentationConnectorRequest,
    ) -> DocumentationConnectorItem | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(DocumentationConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return None
            old_name = row.name
            row.name = request.name
            self._apply_connection(row=row, request=request, now=now)
            if old_name != row.name:
                self._delete_indexed_content_for_connector_name(
                    session=session,
                    workspace_id=workspace_id,
                    connector_name=old_name,
                )
                row.next_sync_at = now if row.sync_enabled else None
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row)

    def list(self, workspace_id: str, limit: int) -> list[DocumentationConnectorItem]:
        with self._db.session() as session:
            rows = (
                session.execute(
                    select(DocumentationConnectorConfigOrm)
                    .where(DocumentationConnectorConfigOrm.workspace_id == workspace_id)
                    .order_by(DocumentationConnectorConfigOrm.updated_at.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        return [self._to_contract(row) for row in rows]

    def list_due(self, *, now: datetime, limit: int) -> list[DocumentationConnectorItem]:
        with self._db.session() as session:
            rows = (
                session.execute(
                    select(DocumentationConnectorConfigOrm)
                    .where(
                        DocumentationConnectorConfigOrm.is_active.is_(True),
                        DocumentationConnectorConfigOrm.sync_enabled.is_(True),
                        DocumentationConnectorConfigOrm.next_sync_at.is_not(None),
                        DocumentationConnectorConfigOrm.next_sync_at <= now,
                    )
                    .order_by(DocumentationConnectorConfigOrm.next_sync_at.asc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        return [self._to_contract(row) for row in rows]

    def get(
        self,
        workspace_id: str,
        name: str | None = None,
    ) -> DocumentationConnectorItem | None:
        with self._db.session() as session:
            stmt = select(DocumentationConnectorConfigOrm).where(
                DocumentationConnectorConfigOrm.workspace_id == workspace_id,
                DocumentationConnectorConfigOrm.is_active.is_(True),
            )
            if name:
                stmt = stmt.where(DocumentationConnectorConfigOrm.name == name)
            stmt = stmt.order_by(DocumentationConnectorConfigOrm.updated_at.desc()).limit(1)
            row = session.execute(stmt).scalar_one_or_none()
        if row is not None:
            return self._to_contract(row)
        builtin = self._builtin_connection_from_env()
        if builtin is None:
            return None
        if builtin.workspace_id != workspace_id:
            return None
        if name and builtin.name != name:
            return None
        return builtin

    def get_by_id(self, connector_id: str, workspace_id: str) -> DocumentationConnectorItem | None:
        with self._db.session() as session:
            row = session.get(DocumentationConnectorConfigOrm, connector_id)
        if row is None or row.workspace_id != workspace_id:
            return None
        return self._to_contract(row)

    def get_connection(
        self,
        workspace_id: str,
        name: str | None = None,
    ) -> DocumentationConnectionConfig | None:
        item = self.get(workspace_id=workspace_id, name=name)
        return item.connection if item is not None else None

    def delete(self, connector_id: str, workspace_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(DocumentationConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return False
            self._delete_secret(row.workspace_id, row.api_token_secret_ref)
            self._delete_secret(row.workspace_id, row.embedding_api_key_secret_ref)
            self._delete_secret(row.workspace_id, row.qdrant_api_key_secret_ref)
            session.execute(
                delete(DocumentationChunkOrm).where(
                    DocumentationChunkOrm.workspace_id == row.workspace_id,
                    DocumentationChunkOrm.connector_name == row.name,
                )
            )
            session.execute(
                delete(DocumentationPageOrm).where(
                    DocumentationPageOrm.workspace_id == row.workspace_id,
                    DocumentationPageOrm.connector_name == row.name,
                )
            )
            session.delete(row)
            session.commit()
            return True

    def create_sync_run(
        self,
        *,
        connector: DocumentationConnectorItem,
        trigger: str,
    ) -> DocumentationSyncRunItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = DocumentationSyncRunOrm(
                id=str(uuid4()),
                connector_id=connector.id,
                workspace_id=connector.workspace_id,
                connector_name=connector.name,
                status="running",
                trigger=trigger,
                started_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._sync_run_to_contract(row)

    def finish_sync_run(
        self,
        *,
        run_id: str,
        connector: DocumentationConnectorItem,
        status: str,
        pages_seen: int,
        pages_updated: int,
        chunks_indexed: int,
        warnings: list[str],
        error_message: str | None = None,
    ) -> DocumentationSyncRunItem:
        now = datetime.now(tz=UTC)
        next_sync_at = (
            now + timedelta(minutes=connector.connection.sync_interval_minutes)
            if connector.connection.sync_enabled
            else None
        )
        with self._db.session() as session:
            run = session.get(DocumentationSyncRunOrm, run_id)
            if run is None:
                raise ValueError("documentation_sync_run_missing")
            run.status = status
            run.pages_seen = pages_seen
            run.pages_updated = pages_updated
            run.chunks_indexed = chunks_indexed
            run.warnings = warnings
            run.error_message = error_message
            run.finished_at = now
            connector_row = session.get(DocumentationConnectorConfigOrm, connector.id)
            if connector_row is not None:
                connector_row.last_sync_status = status
                connector_row.last_sync_at = now
                connector_row.next_sync_at = next_sync_at
                connector_row.updated_at = now
            session.commit()
            session.refresh(run)
            return self._sync_run_to_contract(run)

    def list_sync_runs(
        self,
        *,
        connector_id: str,
        workspace_id: str,
        limit: int,
    ) -> list[DocumentationSyncRunItem]:
        with self._db.session() as session:
            rows = (
                session.execute(
                    select(DocumentationSyncRunOrm)
                    .where(
                        DocumentationSyncRunOrm.connector_id == connector_id,
                        DocumentationSyncRunOrm.workspace_id == workspace_id,
                    )
                    .order_by(DocumentationSyncRunOrm.started_at.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        return [self._sync_run_to_contract(row) for row in rows]

    def upsert_page_with_chunks(
        self,
        *,
        workspace_id: str,
        connector_name: str,
        page_ref: str,
        url: str,
        title: str,
        source_path: str | None,
        markdown_url: str | None,
        summary: str | None,
        body_text: str,
        body_hash: str,
        metadata_json: dict[str, Any],
        chunks: list[dict[str, Any]],
    ) -> tuple[bool, list[dict[str, Any]]]:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            page = session.execute(
                select(DocumentationPageOrm).where(
                    DocumentationPageOrm.workspace_id == workspace_id,
                    DocumentationPageOrm.connector_name == connector_name,
                    DocumentationPageOrm.page_ref == page_ref,
                )
            ).scalar_one_or_none()
            changed = page is None or page.body_hash != body_hash or page.deleted_at is not None
            if page is None:
                page = DocumentationPageOrm(
                    id=str(uuid4()),
                    workspace_id=workspace_id,
                    connector_name=connector_name,
                    page_ref=page_ref,
                    created_at=now,
                )
                session.add(page)
            page.url = url
            page.title = title
            page.source_path = source_path
            page.markdown_url = markdown_url
            page.summary = summary
            page.body_text = body_text
            page.body_hash = body_hash
            page.metadata_json = metadata_json
            page.deleted_at = None
            page.last_seen_at = now
            page.updated_at = now

            if changed:
                session.flush()
                session.execute(
                    delete(DocumentationChunkOrm).where(
                        DocumentationChunkOrm.workspace_id == workspace_id,
                        DocumentationChunkOrm.connector_name == connector_name,
                        DocumentationChunkOrm.page_ref == page_ref,
                    )
                )
                for chunk in chunks:
                    session.add(
                        DocumentationChunkOrm(
                            id=str(uuid4()),
                            workspace_id=workspace_id,
                            connector_name=connector_name,
                            page_id=page.id,
                            page_ref=page_ref,
                            chunk_ref=chunk["chunk_ref"],
                            chunk_index=int(chunk["chunk_index"]),
                            section=chunk.get("section"),
                            content=chunk["content"],
                            content_hash=chunk["content_hash"],
                            qdrant_point_id=chunk["qdrant_point_id"],
                            created_at=now,
                            updated_at=now,
                        )
                    )
            session.commit()
        return changed, chunks if changed else []

    def mark_missing_pages_deleted(
        self,
        *,
        workspace_id: str,
        connector_name: str,
        seen_refs: set[str],
    ) -> None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            pages = (
                session.execute(
                    select(DocumentationPageOrm).where(
                        DocumentationPageOrm.workspace_id == workspace_id,
                        DocumentationPageOrm.connector_name == connector_name,
                        DocumentationPageOrm.deleted_at.is_(None),
                    )
                )
                .scalars()
                .all()
            )
            for page in pages:
                if page.page_ref not in seen_refs:
                    page.deleted_at = now
                    page.updated_at = now
            session.commit()

    def search_chunks(
        self,
        *,
        workspace_id: str,
        query: str,
        connector_name: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        raw_terms = [term for term in re.split(r"\W+", query.casefold()) if term]
        terms = self._lexical_terms(raw_terms)
        phrases = self._lexical_phrases(query=query, terms=terms)
        with self._db.session() as session:
            stmt = (
                select(DocumentationChunkOrm, DocumentationPageOrm)
                .join(DocumentationPageOrm, DocumentationChunkOrm.page_id == DocumentationPageOrm.id)
                .where(
                    DocumentationChunkOrm.workspace_id == workspace_id,
                    DocumentationPageOrm.deleted_at.is_(None),
                )
            )
            if connector_name:
                stmt = stmt.where(DocumentationChunkOrm.connector_name == connector_name)
            if terms or phrases:
                clauses = []
                rank_score = literal(0)
                for phrase in phrases:
                    pattern = f"%{phrase}%"
                    title_match = DocumentationPageOrm.title.ilike(pattern)
                    page_ref_match = DocumentationPageOrm.page_ref.ilike(pattern)
                    content_match = DocumentationChunkOrm.content.ilike(pattern)
                    clauses.extend([content_match, title_match, page_ref_match])
                    rank_score += (
                        case((title_match, 40), else_=0)
                        + case((page_ref_match, 30), else_=0)
                        + case((content_match, 20), else_=0)
                    )
                for term in terms[:8]:
                    pattern = f"%{term}%"
                    title_match = DocumentationPageOrm.title.ilike(pattern)
                    page_ref_match = DocumentationPageOrm.page_ref.ilike(pattern)
                    content_match = DocumentationChunkOrm.content.ilike(pattern)
                    clauses.extend([content_match, title_match, page_ref_match])
                    rank_score += (
                        case((title_match, 4), else_=0)
                        + case((page_ref_match, 3), else_=0)
                        + case((content_match, 1), else_=0)
                    )
                stmt = stmt.where(or_(*clauses))
            else:
                rank_score = literal(0)
            rows = session.execute(
                stmt.order_by(
                    rank_score.desc(),
                    DocumentationPageOrm.page_ref.asc(),
                    DocumentationChunkOrm.chunk_index.asc(),
                ).limit(limit)
            ).all()

        return [self._chunk_result(chunk=chunk, page=page) for chunk, page in rows]

    @classmethod
    def _lexical_terms(cls, raw_terms: list[str]) -> list[str]:
        seen: set[str] = set()
        terms: list[str] = []
        for term in raw_terms:
            if term in cls._LEXICAL_STOPWORDS:
                continue
            if len(term) < 3 and term not in cls._LEXICAL_KEEP_SHORT_TERMS:
                continue
            if term in seen:
                continue
            seen.add(term)
            terms.append(term)
        return terms

    @classmethod
    def _lexical_phrases(cls, *, query: str, terms: list[str]) -> list[str]:
        normalized = " ".join(term for term in re.split(r"\W+", query.casefold()) if term)
        candidates = [normalized, " ".join(terms)]
        phrases: list[str] = []
        seen: set[str] = set()
        for phrase in candidates:
            if len(phrase) < 8 or phrase in seen:
                continue
            seen.add(phrase)
            phrases.append(phrase)
        return phrases

    def get_page(
        self,
        *,
        workspace_id: str,
        page_ref: str,
        connector_name: str | None,
    ) -> dict[str, Any] | None:
        with self._db.session() as session:
            stmt = select(DocumentationPageOrm).where(
                DocumentationPageOrm.workspace_id == workspace_id,
                DocumentationPageOrm.page_ref == page_ref,
                DocumentationPageOrm.deleted_at.is_(None),
            )
            if connector_name:
                stmt = stmt.where(DocumentationPageOrm.connector_name == connector_name)
            page = session.execute(stmt.order_by(DocumentationPageOrm.updated_at.desc())).scalar_one_or_none()
        if page is None:
            return None
        return {
            "connector_name": page.connector_name,
            "page_ref": page.page_ref,
            "url": page.url,
            "title": page.title,
            "source_path": page.source_path,
            "markdown_url": page.markdown_url,
            "summary": page.summary,
            "body_text": page.body_text,
            "body_hash": page.body_hash,
            "metadata": page.metadata_json,
            "updated_at": page.updated_at.isoformat(),
        }

    def get_chunks_by_point_ids(
        self,
        *,
        workspace_id: str,
        point_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not point_ids:
            return {}
        with self._db.session() as session:
            rows = session.execute(
                select(DocumentationChunkOrm, DocumentationPageOrm)
                .join(DocumentationPageOrm, DocumentationChunkOrm.page_id == DocumentationPageOrm.id)
                .where(
                    DocumentationChunkOrm.workspace_id == workspace_id,
                    DocumentationChunkOrm.qdrant_point_id.in_(point_ids),
                    DocumentationPageOrm.deleted_at.is_(None),
                )
            ).all()
        return {
            chunk.qdrant_point_id: self._chunk_result(chunk=chunk, page=page)
            for chunk, page in rows
        }

    @staticmethod
    def _chunk_result(
        *,
        chunk: DocumentationChunkOrm,
        page: DocumentationPageOrm,
    ) -> dict[str, Any]:
        return {
            "chunk_id": chunk.id,
            "chunk_ref": chunk.chunk_ref,
            "point_id": chunk.qdrant_point_id,
            "connector_name": chunk.connector_name,
            "page_ref": chunk.page_ref,
            "url": page.url,
            "title": page.title,
            "section": chunk.section,
            "snippet": chunk.content[:900],
        }

    def _apply_connection(
        self,
        *,
        row: DocumentationConnectorConfigOrm,
        request: UpsertDocumentationConnectorRequest,
        now: datetime,
    ) -> None:
        connection = request.connection
        row.is_active = request.is_active
        row.visible_to_default_assistant = request.visible_to_default_assistant
        row.provider = connection.provider
        row.base_url = connection.base_url
        row.index_url = connection.index_url
        row.content_format = connection.content_format
        row.username = connection.username
        row.usage_description = connection.usage_description
        row.auth_type = connection.auth_type
        row.verify_tls = connection.verify_tls
        row.include_paths = connection.include_paths
        row.exclude_paths = connection.exclude_paths
        row.sync_enabled = connection.sync_enabled
        row.sync_interval_minutes = connection.sync_interval_minutes
        row.qdrant_base_url = connection.qdrant_base_url
        row.qdrant_collection_name = connection.qdrant_collection_name
        row.qdrant_prefer_grpc = connection.qdrant_prefer_grpc
        row.vector_size = connection.vector_size
        row.embedding_base_url = connection.embedding_base_url
        row.embedding_model = connection.embedding_model
        row.embedding_endpoint_path = connection.embedding_endpoint_path
        row.embedding_timeout_seconds = connection.embedding_timeout_seconds
        row.extra_settings = connection.extra_settings
        row.updated_at = now
        if row.next_sync_at is None and connection.sync_enabled:
            row.next_sync_at = now
        if not connection.sync_enabled:
            row.next_sync_at = None

        if connection.api_token is not None:
            row.api_token_secret_ref = row.api_token_secret_ref or self._secret_ref(
                "api_token", request.workspace_id, row.id
            )
            self._secret_store.set_secret(
                workspace_id=request.workspace_id,
                secret_key=row.api_token_secret_ref,
                value=connection.api_token,
            )
            row.api_token = None
        if connection.embedding_api_key is not None:
            row.embedding_api_key_secret_ref = row.embedding_api_key_secret_ref or self._secret_ref(
                "embedding_api_key", request.workspace_id, row.id
            )
            self._secret_store.set_secret(
                workspace_id=request.workspace_id,
                secret_key=row.embedding_api_key_secret_ref,
                value=connection.embedding_api_key,
            )
            row.embedding_api_key = None
        if connection.qdrant_api_key is not None:
            row.qdrant_api_key_secret_ref = row.qdrant_api_key_secret_ref or self._secret_ref(
                "qdrant_api_key", request.workspace_id, row.id
            )
            self._secret_store.set_secret(
                workspace_id=request.workspace_id,
                secret_key=row.qdrant_api_key_secret_ref,
                value=connection.qdrant_api_key,
            )
            row.qdrant_api_key = None

    def _to_contract(self, row: DocumentationConnectorConfigOrm) -> DocumentationConnectorItem:
        return DocumentationConnectorItem(
            id=row.id,
            name=row.name,
            workspace_id=row.workspace_id,
            is_active=row.is_active,
            visible_to_default_assistant=bool(
                getattr(row, "visible_to_default_assistant", True)
            ),
            connection=DocumentationConnectionConfig(
                provider=row.provider,
                base_url=row.base_url,
                index_url=row.index_url,
                content_format=row.content_format,
                username=row.username,
                api_token=None,
                usage_description=getattr(row, "usage_description", None),
                auth_type=row.auth_type,
                verify_tls=row.verify_tls,
                include_paths=row.include_paths or [],
                exclude_paths=row.exclude_paths or [],
                sync_enabled=row.sync_enabled,
                sync_interval_minutes=row.sync_interval_minutes,
                qdrant_base_url=row.qdrant_base_url,
                qdrant_api_key=None,
                qdrant_collection_name=row.qdrant_collection_name,
                qdrant_prefer_grpc=row.qdrant_prefer_grpc,
                vector_size=row.vector_size,
                embedding_base_url=row.embedding_base_url,
                embedding_api_key=None,
                embedding_model=row.embedding_model,
                embedding_endpoint_path=row.embedding_endpoint_path,
                embedding_timeout_seconds=row.embedding_timeout_seconds,
                extra_settings=row.extra_settings or {},
            ),
            has_api_token=bool(row.api_token or row.api_token_secret_ref),
            has_embedding_api_key=bool(row.embedding_api_key or row.embedding_api_key_secret_ref),
            has_qdrant_api_key=bool(row.qdrant_api_key or row.qdrant_api_key_secret_ref),
            last_sync_status=row.last_sync_status,
            last_sync_at=row.last_sync_at,
            next_sync_at=row.next_sync_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def resolve_secrets(
        self,
        connection: DocumentationConnectionConfig,
        workspace_id: str,
        connector_id: str,
    ) -> DocumentationConnectionConfig:
        with self._db.session() as session:
            row = session.get(DocumentationConnectorConfigOrm, connector_id)
        if row is None or row.workspace_id != workspace_id:
            return connection
        return connection.model_copy(
            update={
                "api_token": self._resolve_secret(
                    row.workspace_id,
                    row.api_token_secret_ref,
                    row.api_token,
                    env_fallback_names=("DOCUMENTATION_API_TOKEN",),
                ),
                "embedding_api_key": self._resolve_secret(
                    row.workspace_id,
                    row.embedding_api_key_secret_ref,
                    row.embedding_api_key,
                    env_fallback_names=("DOCUMENTATION_EMBEDDING_API_KEY", "EXAMPLE_LLM_KEY"),
                ),
                "qdrant_api_key": self._resolve_secret(
                    row.workspace_id,
                    row.qdrant_api_key_secret_ref,
                    row.qdrant_api_key,
                    env_fallback_names=("DOCUMENTATION_QDRANT_API_KEY",),
                ),
            }
        )

    def _resolve_secret(
        self,
        workspace_id: str,
        secret_ref: str | None,
        fallback: str | None,
        *,
        env_fallback_names: tuple[str, ...] = (),
    ) -> str | None:
        if not secret_ref:
            return fallback or self._first_env(*env_fallback_names)
        return (
            self._secret_store.get_secret(workspace_id=workspace_id, secret_key=secret_ref)
            or fallback
            or self._first_env(*env_fallback_names)
        )

    def _delete_secret(self, workspace_id: str, secret_ref: str | None) -> None:
        if secret_ref:
            self._secret_store.delete_secret(workspace_id=workspace_id, secret_key=secret_ref)

    @staticmethod
    def _secret_ref(kind: str, workspace_id: str, connector_id: str) -> str:
        return f"documentation_connector/{workspace_id}/{connector_id}/{kind}"

    @staticmethod
    def _first_env(*names: str) -> str | None:
        for name in names:
            value = (os.getenv(name) or "").strip()
            if value:
                return value
        return None

    @staticmethod
    def _sync_run_to_contract(row: DocumentationSyncRunOrm) -> DocumentationSyncRunItem:
        return DocumentationSyncRunItem(
            id=row.id,
            connector_id=row.connector_id,
            workspace_id=row.workspace_id,
            connector_name=row.connector_name,
            status=row.status,
            trigger=row.trigger,
            pages_seen=row.pages_seen,
            pages_updated=row.pages_updated,
            chunks_indexed=row.chunks_indexed,
            warnings=row.warnings or [],
            error_message=row.error_message,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    @staticmethod
    def _builtin_connection_from_env() -> DocumentationConnectorItem | None:
        base_url = (os.getenv("DOCUMENTATION_BASE_URL") or "").strip()
        if not base_url:
            return None
        now = datetime.now(tz=UTC)
        embedding_api_key = DocumentationConnectorRepository._first_env(
            "DOCUMENTATION_EMBEDDING_API_KEY",
            "EXAMPLE_LLM_KEY",
        )
        qdrant_api_key = DocumentationConnectorRepository._first_env("DOCUMENTATION_QDRANT_API_KEY")
        api_token = DocumentationConnectorRepository._first_env("DOCUMENTATION_API_TOKEN")
        return DocumentationConnectorItem(
            id="env-documentation",
            name=(os.getenv("DOCUMENTATION_CONNECTOR_NAME") or "platform_docs").strip(),
            workspace_id=(os.getenv("DOCUMENTATION_WORKSPACE_ID") or "default").strip(),
            is_active=True,
            connection=DocumentationConnectionConfig(
                base_url=base_url,
                index_url=(os.getenv("DOCUMENTATION_INDEX_URL") or "").strip() or None,
                content_format=(os.getenv("DOCUMENTATION_CONTENT_FORMAT") or "markdown").strip(),
                username=(os.getenv("DOCUMENTATION_USERNAME") or "").strip() or None,
                api_token=api_token,
                usage_description=(os.getenv("DOCUMENTATION_USE_WHEN") or "").strip() or None,
                auth_type=(os.getenv("DOCUMENTATION_AUTH_TYPE") or "none").strip(),
                verify_tls=DocumentationConnectorRepository._env_bool("DOCUMENTATION_VERIFY_TLS", True),
                include_paths=DocumentationConnectorRepository._env_list("DOCUMENTATION_INCLUDE_PATHS"),
                exclude_paths=DocumentationConnectorRepository._env_list("DOCUMENTATION_EXCLUDE_PATHS"),
                sync_enabled=DocumentationConnectorRepository._env_bool("DOCUMENTATION_SYNC_ENABLED", True),
                sync_interval_minutes=DocumentationConnectorRepository._env_int(
                    "DOCUMENTATION_SYNC_INTERVAL_MINUTES",
                    1440,
                ),
                qdrant_base_url=(os.getenv("DOCUMENTATION_QDRANT_URL") or "http://qdrant:6333").strip(),
                qdrant_api_key=qdrant_api_key,
                qdrant_collection_name=(
                    os.getenv("DOCUMENTATION_QDRANT_COLLECTION") or "documentation_chunks"
                ).strip(),
                qdrant_prefer_grpc=DocumentationConnectorRepository._env_bool(
                    "DOCUMENTATION_QDRANT_PREFER_GRPC",
                    False,
                ),
                vector_size=DocumentationConnectorRepository._env_int("DOCUMENTATION_VECTOR_SIZE", 1024),
                embedding_base_url=(
                    os.getenv("DOCUMENTATION_EMBEDDING_BASE_URL")
                    or os.getenv("EXAMPLE_LLM_URL")
                    or ""
                ).strip()
                or None,
                embedding_api_key=embedding_api_key,
                embedding_model=(
                    os.getenv("DOCUMENTATION_EMBEDDING_MODEL")
                    or os.getenv("EMB_MODEL")
                    or "BAAI/bge-small-en-v1.5"
                ).strip(),
                embedding_endpoint_path=(
                    os.getenv("DOCUMENTATION_EMBEDDING_ENDPOINT_PATH") or "/v1/embeddings"
                ).strip(),
                embedding_timeout_seconds=DocumentationConnectorRepository._env_int(
                    "DOCUMENTATION_EMBEDDING_TIMEOUT_SECONDS",
                    120,
                ),
            ),
            has_api_token=bool(api_token),
            has_embedding_api_key=bool(embedding_api_key),
            has_qdrant_api_key=bool(qdrant_api_key),
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _delete_indexed_content_for_connector_name(
        *,
        session,
        workspace_id: str,
        connector_name: str,
    ) -> None:
        session.execute(
            delete(DocumentationChunkOrm).where(
                DocumentationChunkOrm.workspace_id == workspace_id,
                DocumentationChunkOrm.connector_name == connector_name,
            )
        )
        session.execute(
            delete(DocumentationPageOrm).where(
                DocumentationPageOrm.workspace_id == workspace_id,
                DocumentationPageOrm.connector_name == connector_name,
            )
        )

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = (os.getenv(name) or "").strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    @staticmethod
    def _env_list(name: str) -> list[str]:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]
