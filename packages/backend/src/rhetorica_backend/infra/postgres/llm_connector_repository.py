from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from rhetorica_backend.contracts.admin_integrations import (
    LlmConnectionConfig,
    LlmConnectorItem,
    UpsertLlmConnectorRequest,
)
from rhetorica_backend.infra.postgres.models import LlmConnectorConfigOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.secrets import SecretStore, SecretStoreError, get_secret_store
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


class LlmConnectorRepository:
    """Persistence gateway for LLM connector settings."""

    def __init__(self, db: DatabaseRuntime, secret_store: SecretStore | None = None):
        self._db = db
        self._secret_store = secret_store or get_secret_store()

    def upsert(self, request: UpsertLlmConnectorRequest) -> LlmConnectorItem:
        now = datetime.now(tz=UTC)
        extra_settings = self._build_extra_settings(
            base=request.connection.extra_settings,
            verify_tls=request.connection.verify_tls,
        )
        with self._db.session() as session:
            stmt = select(LlmConnectorConfigOrm).where(
                LlmConnectorConfigOrm.workspace_id == request.workspace_id,
                LlmConnectorConfigOrm.name == request.name,
            )
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                row = LlmConnectorConfigOrm(
                    id=str(uuid4()),
                    workspace_id=request.workspace_id,
                    name=request.name,
                    is_active=request.is_active,
                    created_at=now,
                    updated_at=now,
                    base_url=request.connection.base_url,
                    model=request.connection.model,
                    api_key=request.connection.api_key,
                    endpoint_path=request.connection.endpoint_path,
                    temperature=request.connection.temperature,
                    max_tokens=request.connection.max_tokens,
                    headers=request.connection.headers,
                    extra_settings=extra_settings,
                )
                session.add(row)
            else:
                row.updated_at = now
                row.is_active = request.is_active
                row.base_url = request.connection.base_url
                row.model = request.connection.model
                row.api_key = request.connection.api_key or row.api_key
                row.endpoint_path = request.connection.endpoint_path
                row.temperature = request.connection.temperature
                row.max_tokens = request.connection.max_tokens
                row.headers = request.connection.headers
                row.extra_settings = extra_settings

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
        request: UpsertLlmConnectorRequest,
    ) -> LlmConnectorItem | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(LlmConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return None
            row.updated_at = now
            row.name = request.name
            row.is_active = request.is_active
            row.base_url = request.connection.base_url
            row.model = request.connection.model
            row.api_key = request.connection.api_key or row.api_key
            row.endpoint_path = request.connection.endpoint_path
            row.temperature = request.connection.temperature
            row.max_tokens = request.connection.max_tokens
            row.headers = request.connection.headers
            row.extra_settings = self._build_extra_settings(
                base=request.connection.extra_settings,
                verify_tls=request.connection.verify_tls,
            )
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

    def list(self, workspace_id: str, limit: int) -> list[LlmConnectorItem]:
        with self._db.session() as session:
            stmt = (
                select(LlmConnectorConfigOrm)
                .where(LlmConnectorConfigOrm.workspace_id == workspace_id)
                .order_by(LlmConnectorConfigOrm.updated_at.desc())
                .limit(limit)
            )
            rows = session.execute(stmt).scalars().all()

        return [self._to_contract(row) for row in rows]

    def get(self, workspace_id: str, name: str | None = None) -> LlmConnectorItem | None:
        with self._db.session() as session:
            stmt = select(LlmConnectorConfigOrm).where(
                LlmConnectorConfigOrm.workspace_id == workspace_id,
                LlmConnectorConfigOrm.is_active.is_(True),
            )
            if name:
                stmt = stmt.where(LlmConnectorConfigOrm.name == name)
            stmt = stmt.order_by(LlmConnectorConfigOrm.updated_at.desc()).limit(1)
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return self._to_contract(row)

    def delete(self, connector_id: str, workspace_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(LlmConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return False
            self._delete_secret(row.workspace_id, row.api_key_secret_ref)
            session.delete(row)
            session.commit()
            return True

    def _to_contract(self, row: LlmConnectorConfigOrm) -> LlmConnectorItem:
        connection = LlmConnectionConfig(
            base_url=row.base_url,
            model=row.model,
            api_key=None,
            endpoint_path=row.endpoint_path,
            verify_tls=LlmConnectorRepository._resolve_verify_tls(row.extra_settings),
            temperature=row.temperature,
            max_tokens=row.max_tokens,
            headers=row.headers,
            extra_settings=row.extra_settings,
        )
        return LlmConnectorItem(
            id=row.id,
            name=row.name,
            workspace_id=row.workspace_id,
            is_active=row.is_active,
            connection=connection,
            has_api_key=bool(self._resolve_api_key(row)),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def get_connection(
        self, workspace_id: str, name: str | None = None
    ) -> LlmConnectionConfig | None:
        with self._db.session() as session:
            stmt = select(LlmConnectorConfigOrm).where(
                LlmConnectorConfigOrm.workspace_id == workspace_id,
                LlmConnectorConfigOrm.is_active.is_(True),
            )
            if name:
                stmt = stmt.where(LlmConnectorConfigOrm.name == name)
            stmt = stmt.order_by(LlmConnectorConfigOrm.updated_at.desc()).limit(1)
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return LlmConnectionConfig(
            base_url=row.base_url,
            model=row.model,
            api_key=self._resolve_api_key(row),
            endpoint_path=row.endpoint_path,
            verify_tls=self._resolve_verify_tls(row.extra_settings),
            temperature=row.temperature,
            max_tokens=row.max_tokens,
            headers=row.headers,
            extra_settings=row.extra_settings,
        )

    def _resolve_api_key(self, row: LlmConnectorConfigOrm) -> str | None:
        if row.api_key_secret_ref:
            api_key = self._secret_store.get_secret(
                workspace_id=row.workspace_id,
                secret_key=row.api_key_secret_ref,
            )
            if api_key is not None:
                return api_key
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
        return f"llm_connector/{workspace_id}/{connector_id}/api_key"

    @staticmethod
    def _resolve_verify_tls(extra_settings: dict[str, object] | None) -> bool:
        if not isinstance(extra_settings, dict):
            return True
        value = extra_settings.get("verify_tls")
        return bool(value) if isinstance(value, bool) else True

    @staticmethod
    def _build_extra_settings(base: dict[str, object], verify_tls: bool) -> dict[str, object]:
        return {**base, "verify_tls": verify_tls}
