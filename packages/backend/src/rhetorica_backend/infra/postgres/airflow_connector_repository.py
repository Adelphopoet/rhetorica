from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from rhetorica_backend.contracts.admin_integrations import (
    AirflowAuthType,
    AirflowConnectionConfig,
    AirflowConnectorItem,
    AirflowProvider,
    UpsertAirflowConnectorRequest,
)
from rhetorica_backend.contracts.workspaces import WorkspaceResourceKind
from rhetorica_backend.infra.postgres.models import AirflowConnectorConfigOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.workspace_resource_access import (
    WorkspaceResourceAccess,
    present_resource,
)
from rhetorica_backend.infra.secrets import SecretStore, SecretStoreError, get_secret_store
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


class AirflowConnectorRepository:
    """Persistence gateway for read-only Airflow connector settings."""

    def __init__(self, db: DatabaseRuntime, secret_store: SecretStore | None = None):
        self._db = db
        self._secret_store = secret_store or get_secret_store()
        self._resources = WorkspaceResourceAccess(
            db,
            kind=WorkspaceResourceKind.AIRFLOW_CONNECTOR,
            model=AirflowConnectorConfigOrm,
        )

    def upsert(self, request: UpsertAirflowConnectorRequest) -> AirflowConnectorItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.execute(
                select(AirflowConnectorConfigOrm).where(
                    AirflowConnectorConfigOrm.workspace_id == request.workspace_id,
                    AirflowConnectorConfigOrm.name == request.name,
                )
            ).scalar_one_or_none()
            if row is None:
                row = AirflowConnectorConfigOrm(
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
        request: UpsertAirflowConnectorRequest,
    ) -> AirflowConnectorItem | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(AirflowConnectorConfigOrm, connector_id)
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

    def list(self, workspace_id: str, limit: int) -> list[AirflowConnectorItem]:
        return [
            present_resource(self._to_contract(resource.row), resource)
            for resource in self._resources.list(workspace_id=workspace_id, limit=limit)
        ]

    def get(
        self,
        workspace_id: str,
        name: str | None = None,
    ) -> AirflowConnectorItem | None:
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
        self,
        workspace_id: str,
        name: str | None = None,
    ) -> AirflowConnectionConfig | None:
        resource = self._resources.first_active(workspace_id=workspace_id, name=name)
        if resource is None:
            return None
        row = resource.row
        return AirflowConnectionConfig(
            provider=cast(AirflowProvider, row.provider),
            base_url=row.base_url,
            username=row.username,
            password=self._resolve_password(row),
            use_when=row.use_when,
            auth_type=cast(AirflowAuthType, row.auth_type),
            verify_tls=row.verify_tls,
            api_path=row.api_path,
            dag_limit=row.dag_limit,
            dag_run_limit=row.dag_run_limit,
            task_limit=row.task_limit,
            log_max_chars=row.log_max_chars,
            timeout_seconds=row.timeout_seconds,
            extra_settings=row.extra_settings,
        )

    def delete(self, connector_id: str, workspace_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(AirflowConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return False
            self._delete_secret(row.workspace_id, row.password_secret_ref)
            session.delete(row)
            session.commit()
            return True

    def _apply_connection(
        self,
        *,
        row: AirflowConnectorConfigOrm,
        request: UpsertAirflowConnectorRequest,
        now: datetime,
    ) -> None:
        row.updated_at = now
        row.is_active = request.is_active
        row.provider = request.connection.provider
        row.base_url = request.connection.base_url
        row.username = request.connection.username
        row.use_when = request.connection.use_when
        row.auth_type = request.connection.auth_type
        row.verify_tls = request.connection.verify_tls
        row.api_path = request.connection.api_path
        row.dag_limit = request.connection.dag_limit
        row.dag_run_limit = request.connection.dag_run_limit
        row.task_limit = request.connection.task_limit
        row.log_max_chars = request.connection.log_max_chars
        row.timeout_seconds = request.connection.timeout_seconds
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

    @staticmethod
    def _to_contract(row: AirflowConnectorConfigOrm) -> AirflowConnectorItem:
        return AirflowConnectorItem(
            id=row.id,
            name=row.name,
            workspace_id=row.workspace_id,
            is_active=row.is_active,
            connection=AirflowConnectionConfig(
                provider=cast(AirflowProvider, row.provider),
                base_url=row.base_url,
                username=row.username,
                password=None,
                use_when=row.use_when,
                auth_type=cast(AirflowAuthType, row.auth_type),
                verify_tls=row.verify_tls,
                api_path=row.api_path,
                dag_limit=row.dag_limit,
                dag_run_limit=row.dag_run_limit,
                task_limit=row.task_limit,
                log_max_chars=row.log_max_chars,
                timeout_seconds=row.timeout_seconds,
                extra_settings=row.extra_settings,
            ),
            has_password=bool(row.password_secret_ref or row.password),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _resolve_password(self, row: AirflowConnectorConfigOrm) -> str | None:
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
        return f"airflow_connector/{workspace_id}/{connector_id}/password"
