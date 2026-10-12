from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from rhetorica_backend.contracts.admin_integrations import (
    KubernetesAuthType,
    KubernetesConnectionConfig,
    KubernetesConnectorItem,
    KubernetesProvider,
    UpsertKubernetesConnectorRequest,
)
from rhetorica_backend.infra.postgres.models import KubernetesConnectorConfigOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.secrets import SecretStore, SecretStoreError, get_secret_store
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


class KubernetesConnectorRepository:
    """Persistence gateway for read-only Kubernetes connector settings."""

    def __init__(self, db: DatabaseRuntime, secret_store: SecretStore | None = None):
        self._db = db
        self._secret_store = secret_store or get_secret_store()

    def upsert(self, request: UpsertKubernetesConnectorRequest) -> KubernetesConnectorItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.execute(
                select(KubernetesConnectorConfigOrm).where(
                    KubernetesConnectorConfigOrm.workspace_id == request.workspace_id,
                    KubernetesConnectorConfigOrm.name == request.name,
                )
            ).scalar_one_or_none()
            if row is None:
                row = KubernetesConnectorConfigOrm(
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
        request: UpsertKubernetesConnectorRequest,
    ) -> KubernetesConnectorItem | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(KubernetesConnectorConfigOrm, connector_id)
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

    def list(self, workspace_id: str, limit: int) -> list[KubernetesConnectorItem]:
        with self._db.session() as session:
            rows = (
                session.execute(
                    select(KubernetesConnectorConfigOrm)
                    .where(KubernetesConnectorConfigOrm.workspace_id == workspace_id)
                    .order_by(KubernetesConnectorConfigOrm.updated_at.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        return [self._to_contract(row) for row in rows]

    def get_connection(
        self,
        workspace_id: str,
        name: str | None = None,
        *,
        visible_to_default_assistant: bool | None = None,
    ) -> KubernetesConnectionConfig | None:
        with self._db.session() as session:
            stmt = select(KubernetesConnectorConfigOrm).where(
                KubernetesConnectorConfigOrm.workspace_id == workspace_id,
                KubernetesConnectorConfigOrm.is_active.is_(True),
            )
            if name:
                stmt = stmt.where(KubernetesConnectorConfigOrm.name == name)
            if visible_to_default_assistant is not None:
                stmt = stmt.where(
                    KubernetesConnectorConfigOrm.visible_to_default_assistant.is_(
                        visible_to_default_assistant
                    )
                )
            stmt = stmt.order_by(KubernetesConnectorConfigOrm.updated_at.desc()).limit(1)
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return self._connection_from_row(row, include_secret=True)

    def delete(self, connector_id: str, workspace_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(KubernetesConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return False
            self._delete_secret(row.workspace_id, row.bearer_token_secret_ref)
            session.delete(row)
            session.commit()
            return True

    def _apply_connection(
        self,
        *,
        row: KubernetesConnectorConfigOrm,
        request: UpsertKubernetesConnectorRequest,
        now: datetime,
    ) -> None:
        row.updated_at = now
        row.is_active = request.is_active
        row.visible_to_default_assistant = request.visible_to_default_assistant
        row.provider = request.connection.provider
        row.base_url = request.connection.base_url
        row.use_when = request.connection.use_when
        row.auth_type = request.connection.auth_type
        row.verify_tls = request.connection.verify_tls
        row.namespace_allowlist = request.connection.namespace_allowlist
        row.default_limit = request.connection.default_limit
        row.max_limit = request.connection.max_limit
        row.log_max_chars = request.connection.log_max_chars
        row.timeout_seconds = request.connection.timeout_seconds
        row.extra_settings = request.connection.extra_settings

        if request.connection.bearer_token is not None:
            secret_ref = row.bearer_token_secret_ref or self._token_secret_ref(
                workspace_id=request.workspace_id,
                connector_id=row.id,
            )
            self._secret_store.set_secret(
                workspace_id=request.workspace_id,
                secret_key=secret_ref,
                value=request.connection.bearer_token,
            )
            row.bearer_token_secret_ref = secret_ref
            row.bearer_token = None

    def _to_contract(self, row: KubernetesConnectorConfigOrm) -> KubernetesConnectorItem:
        return KubernetesConnectorItem(
            id=row.id,
            name=row.name,
            workspace_id=row.workspace_id,
            is_active=row.is_active,
            visible_to_default_assistant=row.visible_to_default_assistant,
            connection=self._connection_from_row(row, include_secret=False),
            has_bearer_token=bool(row.bearer_token_secret_ref or row.bearer_token),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _connection_from_row(
        self,
        row: KubernetesConnectorConfigOrm,
        *,
        include_secret: bool,
    ) -> KubernetesConnectionConfig:
        return KubernetesConnectionConfig(
            provider=cast(KubernetesProvider, row.provider),
            base_url=row.base_url,
            bearer_token=self._resolve_token(row) if include_secret else None,
            use_when=row.use_when,
            auth_type=cast(KubernetesAuthType, row.auth_type),
            verify_tls=row.verify_tls,
            namespace_allowlist=row.namespace_allowlist,
            default_limit=row.default_limit,
            max_limit=row.max_limit,
            log_max_chars=row.log_max_chars,
            timeout_seconds=row.timeout_seconds,
            extra_settings=row.extra_settings,
        )

    def _resolve_token(self, row: KubernetesConnectorConfigOrm) -> str | None:
        if row.bearer_token_secret_ref:
            token = self._secret_store.get_secret(
                workspace_id=row.workspace_id,
                secret_key=row.bearer_token_secret_ref,
            )
            if token is not None:
                return token
        return row.bearer_token

    def _delete_secret(self, workspace_id: str, secret_ref: str | None) -> None:
        if not secret_ref:
            return
        try:
            self._secret_store.delete_secret(workspace_id=workspace_id, secret_key=secret_ref)
        except SecretStoreError:
            return

    @staticmethod
    def _token_secret_ref(workspace_id: str, connector_id: str) -> str:
        return f"kubernetes_connector/{workspace_id}/{connector_id}/bearer_token"
