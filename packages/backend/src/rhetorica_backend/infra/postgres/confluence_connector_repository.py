from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from rhetorica_backend.contracts.admin_integrations import (
    ConfluenceAuthType,
    ConfluenceConnectionConfig,
    ConfluenceConnectorItem,
    UpsertConfluenceConnectorRequest,
)
from rhetorica_backend.infra.postgres.models import ConfluenceConnectorConfigOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.secrets import SecretStore, SecretStoreError, get_secret_store
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


class ConfluenceConnectorRepository:
    """Persistence gateway for Confluence connector settings."""

    def __init__(self, db: DatabaseRuntime, secret_store: SecretStore | None = None):
        self._db = db
        self._secret_store = secret_store or get_secret_store()

    def upsert(self, request: UpsertConfluenceConnectorRequest) -> ConfluenceConnectorItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            stmt = select(ConfluenceConnectorConfigOrm).where(
                ConfluenceConnectorConfigOrm.workspace_id == request.workspace_id,
                ConfluenceConnectorConfigOrm.name == request.name,
            )
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                row = ConfluenceConnectorConfigOrm(
                    id=str(uuid4()),
                    workspace_id=request.workspace_id,
                    name=request.name,
                    is_active=request.is_active,
                    created_at=now,
                    updated_at=now,
                    provider=request.connection.provider,
                    base_url=request.connection.base_url,
                    username=request.connection.username,
                    api_token=request.connection.api_token,
                    auth_type=request.connection.auth_type,
                    verify_tls=request.connection.verify_tls,
                    space_key=request.connection.space_key,
                    page_limit=request.connection.page_limit,
                    extra_settings=request.connection.extra_settings,
                )
                session.add(row)
            else:
                row.updated_at = now
                row.is_active = request.is_active
                row.provider = request.connection.provider
                row.base_url = request.connection.base_url
                row.username = request.connection.username
                row.api_token = request.connection.api_token or row.api_token
                row.auth_type = request.connection.auth_type
                row.verify_tls = request.connection.verify_tls
                row.space_key = request.connection.space_key
                row.page_limit = request.connection.page_limit
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
        request: UpsertConfluenceConnectorRequest,
    ) -> ConfluenceConnectorItem | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(ConfluenceConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return None

            row.updated_at = now
            row.name = request.name
            row.is_active = request.is_active
            row.provider = request.connection.provider
            row.base_url = request.connection.base_url
            row.username = request.connection.username
            row.api_token = request.connection.api_token or row.api_token
            row.auth_type = request.connection.auth_type
            row.verify_tls = request.connection.verify_tls
            row.space_key = request.connection.space_key
            row.page_limit = request.connection.page_limit
            row.extra_settings = request.connection.extra_settings

            if request.connection.api_token is not None:
                secret_ref = row.api_token_secret_ref or self._api_token_secret_ref(
                    workspace_id=workspace_id,
                    connector_id=row.id,
                )
                self._secret_store.set_secret(
                    workspace_id=workspace_id,
                    secret_key=secret_ref,
                    value=request.connection.api_token,
                )
                row.api_token_secret_ref = secret_ref
                row.api_token = None

            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row)

    def list(self, workspace_id: str, limit: int) -> list[ConfluenceConnectorItem]:
        with self._db.session() as session:
            stmt = (
                select(ConfluenceConnectorConfigOrm)
                .where(ConfluenceConnectorConfigOrm.workspace_id == workspace_id)
                .order_by(ConfluenceConnectorConfigOrm.updated_at.desc())
                .limit(limit)
            )
            rows = session.execute(stmt).scalars().all()
        return [self._to_contract(row) for row in rows]

    def get(self, workspace_id: str, name: str | None = None) -> ConfluenceConnectorItem | None:
        with self._db.session() as session:
            stmt = select(ConfluenceConnectorConfigOrm).where(
                ConfluenceConnectorConfigOrm.workspace_id == workspace_id,
                ConfluenceConnectorConfigOrm.is_active.is_(True),
            )
            if name:
                stmt = stmt.where(ConfluenceConnectorConfigOrm.name == name)
            stmt = stmt.order_by(ConfluenceConnectorConfigOrm.updated_at.desc()).limit(1)
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return self._to_contract(row)

    def get_connection(
        self,
        workspace_id: str,
        name: str | None = None,
    ) -> ConfluenceConnectionConfig | None:
        with self._db.session() as session:
            stmt = select(ConfluenceConnectorConfigOrm).where(
                ConfluenceConnectorConfigOrm.workspace_id == workspace_id,
                ConfluenceConnectorConfigOrm.is_active.is_(True),
            )
            if name:
                stmt = stmt.where(ConfluenceConnectorConfigOrm.name == name)
            stmt = stmt.order_by(ConfluenceConnectorConfigOrm.updated_at.desc()).limit(1)
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None

        return ConfluenceConnectionConfig(
            provider=row.provider,
            base_url=row.base_url,
            username=row.username,
            api_token=self._resolve_api_token(row),
            auth_type=self._normalize_auth_type(row.auth_type),
            verify_tls=row.verify_tls,
            space_key=row.space_key,
            page_limit=row.page_limit,
            extra_settings=row.extra_settings,
        )

    def delete(self, connector_id: str, workspace_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(ConfluenceConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return False
            self._delete_secret(row.workspace_id, row.api_token_secret_ref)
            session.delete(row)
            session.commit()
            return True

    def _to_contract(self, row: ConfluenceConnectorConfigOrm) -> ConfluenceConnectorItem:
        connection = ConfluenceConnectionConfig(
            provider=row.provider,
            base_url=row.base_url,
            username=row.username,
            api_token=None,
            auth_type=ConfluenceConnectorRepository._normalize_auth_type(row.auth_type),
            verify_tls=row.verify_tls,
            space_key=row.space_key,
            page_limit=row.page_limit,
            extra_settings=row.extra_settings,
        )
        return ConfluenceConnectorItem(
            id=row.id,
            name=row.name,
            workspace_id=row.workspace_id,
            is_active=row.is_active,
            connection=connection,
            has_api_token=bool(self._resolve_api_token(row)),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _resolve_api_token(self, row: ConfluenceConnectorConfigOrm) -> str | None:
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
        return f"confluence_connector/{workspace_id}/{connector_id}/api_token"

    @staticmethod
    def _normalize_auth_type(value: str | None) -> ConfluenceAuthType:
        return "basic" if value == "basic" else "bearer"
