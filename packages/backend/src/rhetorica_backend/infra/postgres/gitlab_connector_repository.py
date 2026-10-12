from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from rhetorica_backend.contracts.admin_integrations import (
    GitLabConnectionConfig,
    GitLabConnectorItem,
    GitLabProvider,
    UpsertGitLabConnectorRequest,
)
from rhetorica_backend.infra.postgres.models import GitLabConnectorConfigOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.secrets import SecretStore, SecretStoreError, get_secret_store
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


class GitLabConnectorRepository:
    """Persistence gateway for read-only GitLab connector settings."""

    def __init__(self, db: DatabaseRuntime, secret_store: SecretStore | None = None):
        self._db = db
        self._secret_store = secret_store or get_secret_store()

    def upsert(self, request: UpsertGitLabConnectorRequest) -> GitLabConnectorItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.execute(
                select(GitLabConnectorConfigOrm).where(
                    GitLabConnectorConfigOrm.workspace_id == request.workspace_id,
                    GitLabConnectorConfigOrm.name == request.name,
                )
            ).scalar_one_or_none()
            if row is None:
                row = GitLabConnectorConfigOrm(
                    id=str(uuid4()),
                    workspace_id=request.workspace_id,
                    name=request.name,
                    created_at=now,
                    updated_at=now,
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
        request: UpsertGitLabConnectorRequest,
    ) -> GitLabConnectorItem | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(GitLabConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return None
            row.name = request.name
            self._apply_connection(row=row, request=request, now=now)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row)

    def list(self, workspace_id: str, limit: int) -> list[GitLabConnectorItem]:
        with self._db.session() as session:
            rows = (
                session.execute(
                    select(GitLabConnectorConfigOrm)
                    .where(GitLabConnectorConfigOrm.workspace_id == workspace_id)
                    .order_by(GitLabConnectorConfigOrm.updated_at.desc())
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
    ) -> GitLabConnectorItem | None:
        with self._db.session() as session:
            stmt = select(GitLabConnectorConfigOrm).where(
                GitLabConnectorConfigOrm.workspace_id == workspace_id,
                GitLabConnectorConfigOrm.is_active.is_(True),
            )
            if name:
                stmt = stmt.where(GitLabConnectorConfigOrm.name == name)
            stmt = stmt.order_by(GitLabConnectorConfigOrm.updated_at.desc()).limit(1)
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return self._to_contract(row)

    def get_connection(
        self,
        workspace_id: str,
        name: str | None = None,
    ) -> GitLabConnectionConfig | None:
        with self._db.session() as session:
            stmt = select(GitLabConnectorConfigOrm).where(
                GitLabConnectorConfigOrm.workspace_id == workspace_id,
                GitLabConnectorConfigOrm.is_active.is_(True),
            )
            if name:
                stmt = stmt.where(GitLabConnectorConfigOrm.name == name)
            stmt = stmt.order_by(GitLabConnectorConfigOrm.updated_at.desc()).limit(1)
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return GitLabConnectionConfig(
            provider=cast(GitLabProvider, row.provider),
            base_url=row.base_url,
            api_token=self._resolve_api_token(row),
            use_when=row.use_when,
            verify_tls=row.verify_tls,
            default_project_paths=list(row.default_project_paths or []),
            default_group_paths=list(row.default_group_paths or []),
            search_limit=row.search_limit,
            read_max_tokens=row.read_max_tokens,
            extra_settings=row.extra_settings,
        )

    def delete(self, connector_id: str, workspace_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(GitLabConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return False
            self._delete_secret(row.workspace_id, row.api_token_secret_ref)
            session.delete(row)
            session.commit()
            return True

    def _apply_connection(
        self,
        *,
        row: GitLabConnectorConfigOrm,
        request: UpsertGitLabConnectorRequest,
        now: datetime,
    ) -> None:
        row.updated_at = now
        row.is_active = request.is_active
        row.provider = request.connection.provider
        row.base_url = request.connection.base_url
        row.use_when = request.connection.use_when
        row.verify_tls = request.connection.verify_tls
        row.default_project_paths = list(request.connection.default_project_paths)
        row.default_group_paths = list(request.connection.default_group_paths)
        row.search_limit = request.connection.search_limit
        row.read_max_tokens = request.connection.read_max_tokens
        row.extra_settings = request.connection.extra_settings

        if request.connection.api_token is not None:
            secret_ref = row.api_token_secret_ref or self._api_token_secret_ref(
                workspace_id=request.workspace_id,
                connector_id=row.id,
            )
            self._secret_store.set_secret(
                workspace_id=request.workspace_id,
                secret_key=secret_ref,
                value=request.connection.api_token,
            )
            row.api_token_secret_ref = secret_ref
            row.api_token = None

    @staticmethod
    def _to_contract(row: GitLabConnectorConfigOrm) -> GitLabConnectorItem:
        return GitLabConnectorItem(
            id=row.id,
            name=row.name,
            workspace_id=row.workspace_id,
            is_active=row.is_active,
            connection=GitLabConnectionConfig(
                provider=cast(GitLabProvider, row.provider),
                base_url=row.base_url,
                api_token=None,
                use_when=row.use_when,
                verify_tls=row.verify_tls,
                default_project_paths=list(row.default_project_paths or []),
                default_group_paths=list(row.default_group_paths or []),
                search_limit=row.search_limit,
                read_max_tokens=row.read_max_tokens,
                extra_settings=row.extra_settings,
            ),
            has_api_token=bool(row.api_token_secret_ref or row.api_token),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _resolve_api_token(self, row: GitLabConnectorConfigOrm) -> str | None:
        if row.api_token_secret_ref:
            token = self._secret_store.get_secret(
                workspace_id=row.workspace_id,
                secret_key=row.api_token_secret_ref,
            )
            if token is not None:
                return token
        return row.api_token

    def _delete_secret(self, workspace_id: str, secret_ref: str | None) -> None:
        if not secret_ref:
            return
        try:
            self._secret_store.delete_secret(workspace_id=workspace_id, secret_key=secret_ref)
        except SecretStoreError:
            return

    @staticmethod
    def _api_token_secret_ref(workspace_id: str, connector_id: str) -> str:
        return f"gitlab_connector/{workspace_id}/{connector_id}/api_token"
