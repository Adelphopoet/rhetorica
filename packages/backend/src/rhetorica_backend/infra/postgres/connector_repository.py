from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from rhetorica_backend.contracts.connectors import ConnectorItem, UpsertConnectorRequest
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.infra.postgres.models import ConnectorConfigOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.secrets import SecretStore, SecretStoreError, get_secret_store
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


class ConnectorRepository:
    """Persistence gateway for workspace connector settings."""

    def __init__(self, db: DatabaseRuntime, secret_store: SecretStore | None = None):
        self._db = db
        self._secret_store = secret_store or get_secret_store()

    def upsert(self, request: UpsertConnectorRequest) -> ConnectorItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            stmt = select(ConnectorConfigOrm).where(
                ConnectorConfigOrm.workspace_id == request.workspace_id,
                ConnectorConfigOrm.name == request.name,
            )
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                row = ConnectorConfigOrm(
                    id=str(uuid4()),
                    workspace_id=request.workspace_id,
                    name=request.name,
                    is_active=request.is_active,
                    created_at=now,
                    updated_at=now,
                    engine=request.connection.engine,
                    host=request.connection.host,
                    port=request.connection.port,
                    username=request.connection.username,
                    password=request.connection.password,
                    database=request.connection.database,
                    catalog=request.connection.catalog,
                    schema_name=request.connection.schema_name,
                    secure=request.connection.secure,
                    verify=request.connection.verify,
                    extra_settings=request.connection.extra_settings,
                )
                session.add(row)
            else:
                row.updated_at = now
                row.is_active = request.is_active
                row.engine = request.connection.engine
                row.host = request.connection.host
                row.port = request.connection.port
                row.username = request.connection.username
                row.password = request.connection.password or row.password
                row.database = request.connection.database
                row.catalog = request.connection.catalog
                row.schema_name = request.connection.schema_name
                row.secure = request.connection.secure
                row.verify = request.connection.verify
                row.extra_settings = request.connection.extra_settings

            if request.connection.password is not None:
                secret_ref = row.password_secret_ref or self._password_secret_ref(
                    workspace_id=request.workspace_id,
                    connector_id=row.id,
                )
                self._secret_store.set_secret(
                    workspace_id=request.workspace_id,
                    secret_key=secret_ref,
                    value=request.connection.password,
                )
                row.password_secret_ref = secret_ref
                row.password = None

            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row)

    def update(
        self, connector_id: str, workspace_id: str, request: UpsertConnectorRequest
    ) -> ConnectorItem | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(ConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return None

            row.updated_at = now
            row.name = request.name
            row.is_active = request.is_active
            row.engine = request.connection.engine
            row.host = request.connection.host
            row.port = request.connection.port
            row.username = request.connection.username
            row.password = request.connection.password or row.password
            row.database = request.connection.database
            row.catalog = request.connection.catalog
            row.schema_name = request.connection.schema_name
            row.secure = request.connection.secure
            row.verify = request.connection.verify
            row.extra_settings = request.connection.extra_settings
            if request.connection.password is not None:
                secret_ref = row.password_secret_ref or self._password_secret_ref(
                    workspace_id=workspace_id,
                    connector_id=row.id,
                )
                self._secret_store.set_secret(
                    workspace_id=workspace_id,
                    secret_key=secret_ref,
                    value=request.connection.password,
                )
                row.password_secret_ref = secret_ref
                row.password = None

            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row)

    def list(self, workspace_id: str, limit: int) -> list[ConnectorItem]:
        with self._db.session() as session:
            stmt = (
                select(ConnectorConfigOrm)
                .where(ConnectorConfigOrm.workspace_id == workspace_id)
                .order_by(ConnectorConfigOrm.updated_at.desc())
                .limit(limit)
            )
            rows = session.execute(stmt).scalars().all()

        return [self._to_contract(row) for row in rows]

    def get(self, workspace_id: str, name: str) -> ConnectorItem | None:
        with self._db.session() as session:
            stmt = select(ConnectorConfigOrm).where(
                ConnectorConfigOrm.workspace_id == workspace_id,
                ConnectorConfigOrm.name == name,
            )
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return self._to_contract(row)

    def get_connection(self, workspace_id: str, name: str) -> EngineConnectionConfig | None:
        return self.get_connection_for_execution(workspace_id=workspace_id, name=name)

    def get_connection_for_execution(
        self, workspace_id: str, name: str
    ) -> EngineConnectionConfig | None:
        with self._db.session() as session:
            stmt = select(ConnectorConfigOrm).where(
                ConnectorConfigOrm.workspace_id == workspace_id,
                ConnectorConfigOrm.name == name,
                ConnectorConfigOrm.is_active.is_(True),
            )
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return EngineConnectionConfig(
            engine=row.engine,
            host=row.host,
            port=row.port,
            username=row.username,
            password=self._resolve_password(row),
            database=row.database,
            catalog=row.catalog,
            schema=row.schema_name,
            secure=row.secure,
            verify=row.verify,
            extra_settings=row.extra_settings,
        )

    def delete(self, connector_id: str, workspace_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(ConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return False
            self._delete_secret(row.workspace_id, row.password_secret_ref)
            session.delete(row)
            session.commit()
            return True

    @staticmethod
    def _to_contract(row: ConnectorConfigOrm) -> ConnectorItem:
        connection = EngineConnectionConfig(
            engine=row.engine,
            host=row.host,
            port=row.port,
            username=row.username,
            password=None,
            database=row.database,
            catalog=row.catalog,
            schema=row.schema_name,
            secure=row.secure,
            verify=row.verify,
            extra_settings=row.extra_settings,
        )
        return ConnectorItem(
            id=row.id,
            name=row.name,
            workspace_id=row.workspace_id,
            is_active=row.is_active,
            connection=connection,
            has_password=bool(row.password_secret_ref or row.password),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _resolve_password(self, row: ConnectorConfigOrm) -> str | None:
        if row.password_secret_ref:
            password = self._secret_store.get_secret(
                workspace_id=row.workspace_id,
                secret_key=row.password_secret_ref,
            )
            if password is not None:
                return password
        return row.password

    def _delete_secret(self, workspace_id: str, secret_ref: str | None) -> None:
        if not secret_ref:
            return
        try:
            self._secret_store.delete_secret(workspace_id=workspace_id, secret_key=secret_ref)
        except SecretStoreError:
            return

    @staticmethod
    def _password_secret_ref(workspace_id: str, connector_id: str) -> str:
        return f"connector/{workspace_id}/{connector_id}/password"
