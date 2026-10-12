from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import uuid4

from rhetorica_backend.contracts.connectors import ConnectorItem, UpsertConnectorRequest
from rhetorica_backend.contracts.execution import EngineConnectionConfig, EngineType
from rhetorica_backend.contracts.workspaces import WorkspaceResourceKind
from rhetorica_backend.infra.postgres.models import ConnectorConfigOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository
from rhetorica_backend.infra.secrets import SecretStore, SecretStoreError, get_secret_store
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


class ConnectorRepository:
    """Persistence gateway for workspace connector settings."""

    def __init__(self, db: DatabaseRuntime, secret_store: SecretStore | None = None):
        self._db = db
        self._secret_store = secret_store or get_secret_store()
        self._workspace_resources = WorkspaceRepository(db=db)

    def upsert(self, request: UpsertConnectorRequest) -> ConnectorItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            stmt = select(ConnectorConfigOrm).where(
                ConnectorConfigOrm.workspace_id == request.workspace_id,
                ConnectorConfigOrm.name == request.name,
            )
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                is_new = True
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
                    usage_description=request.connection.usage_description,
                    database=request.connection.database,
                    catalog=request.connection.catalog,
                    schema_name=request.connection.schema_name,
                    secure=request.connection.secure,
                    verify=request.connection.verify,
                    extra_settings=request.connection.extra_settings,
                    extra_credential_secret_refs={},
                )
                session.add(row)
            else:
                is_new = False
                row.updated_at = now
                row.is_active = request.is_active
                row.engine = request.connection.engine
                row.host = request.connection.host
                row.port = request.connection.port
                row.username = request.connection.username
                row.password = request.connection.password or row.password
                row.usage_description = request.connection.usage_description
                row.database = request.connection.database
                row.catalog = request.connection.catalog
                row.schema_name = request.connection.schema_name
                row.secure = request.connection.secure
                row.verify = request.connection.verify
                row.extra_settings = request.connection.extra_settings

            requested_extra_credentials = request.connection.extra_credentials
            if request.connection.engine != "trino":
                requested_extra_credentials = {}
            self._sync_extra_credentials(
                row,
                requested=requested_extra_credentials,
                is_new=is_new,
            )

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
            row.usage_description = request.connection.usage_description
            row.database = request.connection.database
            row.catalog = request.connection.catalog
            row.schema_name = request.connection.schema_name
            row.secure = request.connection.secure
            row.verify = request.connection.verify
            row.extra_settings = request.connection.extra_settings
            requested_extra_credentials = request.connection.extra_credentials
            if request.connection.engine != "trino":
                requested_extra_credentials = {}
            self._sync_extra_credentials(
                row,
                requested=requested_extra_credentials,
                is_new=False,
            )
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

    def list(self, workspace_id: str, limit: int | None) -> list[ConnectorItem]:
        with self._db.session() as session:
            stmt = (
                select(ConnectorConfigOrm)
                .where(ConnectorConfigOrm.workspace_id == workspace_id)
                .order_by(ConnectorConfigOrm.updated_at.desc())
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            rows = session.execute(stmt).scalars().all()

        result = [self._to_contract(row) for row in rows]
        local_names = {item.name for item in result}
        for grant in self._workspace_resources.list_resource_grants(workspace_id=workspace_id):
            if (
                not grant.is_active
                or grant.resource_kind != WorkspaceResourceKind.DATABASE_CONNECTOR
                or grant.alias in local_names
            ):
                continue
            with self._db.session() as session:
                row = session.get(ConnectorConfigOrm, grant.resource_id)
            if row is None or row.workspace_id != grant.owner_workspace_id or not row.is_active:
                continue
            result.append(self._to_contract(row, workspace_id=workspace_id, name=grant.alias))
            if limit is not None and len(result) >= limit:
                break
        return result[:limit] if limit is not None else result

    def get(self, workspace_id: str, name: str) -> ConnectorItem | None:
        resolved = self._workspace_resources.resolve_resource(
            workspace_id=workspace_id,
            resource_kind=WorkspaceResourceKind.DATABASE_CONNECTOR,
            alias=name,
        )
        if resolved is None:
            return None
        with self._db.session() as session:
            row = session.get(ConnectorConfigOrm, resolved.resource_id)
        if row is None:
            return None
        return self._to_contract(row, workspace_id=workspace_id, name=name)

    def get_connection(self, workspace_id: str, name: str) -> EngineConnectionConfig | None:
        return self.get_connection_for_execution(workspace_id=workspace_id, name=name)

    def get_connection_for_execution(
        self, workspace_id: str, name: str
    ) -> EngineConnectionConfig | None:
        resolved = self._workspace_resources.resolve_resource(
            workspace_id=workspace_id,
            resource_kind=WorkspaceResourceKind.DATABASE_CONNECTOR,
            alias=name,
        )
        if resolved is None:
            return None
        with self._db.session() as session:
            stmt = select(ConnectorConfigOrm).where(
                ConnectorConfigOrm.id == resolved.resource_id,
                ConnectorConfigOrm.workspace_id == resolved.owner_workspace_id,
                ConnectorConfigOrm.is_active.is_(True),
            )
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return EngineConnectionConfig(
            engine=cast(EngineType, row.engine),
            host=row.host,
            port=row.port,
            username=row.username,
            password=self._resolve_password(row),
            usage_description=row.usage_description,
            database=row.database,
            catalog=row.catalog,
            schema=row.schema_name,
            secure=row.secure,
            verify=row.verify,
            extra_settings=row.extra_settings,
            extra_credentials=self._resolve_extra_credentials(row),
        )

    def get_connection_by_id_for_execution(
        self, *, workspace_id: str, connector_id: str
    ) -> tuple[str, EngineConnectionConfig] | None:
        """Resolve a live accessible connector by immutable ID, returning its workspace alias."""
        resolved = self._workspace_resources.resolve_resource_id(
            workspace_id=workspace_id,
            resource_kind=WorkspaceResourceKind.DATABASE_CONNECTOR,
            resource_id=connector_id,
        )
        if resolved is None:
            return None
        with self._db.session() as session:
            stmt = select(ConnectorConfigOrm).where(
                ConnectorConfigOrm.id == connector_id,
                ConnectorConfigOrm.workspace_id == resolved.owner_workspace_id,
                ConnectorConfigOrm.is_active.is_(True),
            )
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return (
            resolved.alias,
            EngineConnectionConfig(
                engine=cast(EngineType, row.engine),
                host=row.host,
                port=row.port,
                username=row.username,
                password=self._resolve_password(row),
                usage_description=row.usage_description,
                database=row.database,
                catalog=row.catalog,
                schema=row.schema_name,
                secure=row.secure,
                verify=row.verify,
                extra_settings=row.extra_settings,
                extra_credentials=self._resolve_extra_credentials(row),
            ),
        )

    def delete(self, connector_id: str, workspace_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(ConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return False
            self._delete_secret(row.workspace_id, row.password_secret_ref)
            for secret_ref in (row.extra_credential_secret_refs or {}).values():
                self._delete_secret(row.workspace_id, secret_ref)
            session.delete(row)
            session.commit()
            return True

    @staticmethod
    def _to_contract(
        row: ConnectorConfigOrm,
        *,
        workspace_id: str | None = None,
        name: str | None = None,
    ) -> ConnectorItem:
        connection = EngineConnectionConfig(
            engine=cast(EngineType, row.engine),
            host=row.host,
            port=row.port,
            username=row.username,
            password=None,
            usage_description=row.usage_description,
            database=row.database,
            catalog=row.catalog,
            schema=row.schema_name,
            secure=row.secure,
            verify=row.verify,
            extra_settings=row.extra_settings,
            extra_credentials=dict.fromkeys(
                sorted((row.extra_credential_secret_refs or {}).keys())
            ),
        )
        return ConnectorItem(
            id=row.id,
            name=name or row.name,
            workspace_id=workspace_id or row.workspace_id,
            is_active=row.is_active,
            connection=connection,
            has_password=bool(row.password_secret_ref or row.password),
            has_extra_credentials=bool(row.extra_credential_secret_refs),
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

    def _resolve_extra_credentials(self, row: ConnectorConfigOrm) -> dict[str, str | None]:
        resolved: dict[str, str | None] = {}
        for name, secret_ref in sorted((row.extra_credential_secret_refs or {}).items()):
            value = self._secret_store.get_secret(
                workspace_id=row.workspace_id,
                secret_key=secret_ref,
            )
            if value is None:
                raise SecretStoreError(f"connector_extra_credential_missing:{name}")
            resolved[name] = value
        return resolved

    def _sync_extra_credentials(
        self,
        row: ConnectorConfigOrm,
        *,
        requested: dict[str, str | None] | None,
        is_new: bool,
    ) -> None:
        if requested is None:
            return

        current_refs = dict(row.extra_credential_secret_refs or {})
        next_refs: dict[str, str] = {}
        for name, value in requested.items():
            secret_ref = current_refs.get(name)
            if value is None:
                if is_new or secret_ref is None:
                    raise ValueError(f"extra_credential_value_required:{name}")
                next_refs[name] = secret_ref
                continue

            if secret_ref is None:
                secret_ref = self._extra_credential_secret_ref(
                    workspace_id=row.workspace_id,
                    connector_id=row.id,
                    credential_name=name,
                )
            self._secret_store.set_secret(
                workspace_id=row.workspace_id,
                secret_key=secret_ref,
                value=value,
            )
            next_refs[name] = secret_ref

        for name in current_refs.keys() - next_refs.keys():
            self._delete_secret(row.workspace_id, current_refs[name])
        row.extra_credential_secret_refs = next_refs

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

    @staticmethod
    def _extra_credential_secret_ref(
        workspace_id: str,
        connector_id: str,
        credential_name: str,
    ) -> str:
        digest = sha256(credential_name.encode("utf-8")).hexdigest()[:16]
        return f"connector/{workspace_id}/{connector_id}/extra_credential/{digest}"
