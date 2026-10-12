from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from rhetorica_backend.contracts.admin_integrations import (
    LlmConnectionConfig,
    LlmConnectorItem,
    UpsertLlmConnectorRequest,
)
from rhetorica_backend.contracts.workspaces import WorkspaceResourceKind
from rhetorica_backend.infra.postgres.models import LlmConnectorConfigOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository
from rhetorica_backend.infra.secrets import SecretStore, SecretStoreError, get_secret_store
from sqlalchemy import select, update


@dataclass(frozen=True, slots=True)
class LlmConnectionResolution:
    """Resolved connector config plus internal identity for credential affinity."""

    connection: LlmConnectionConfig
    connector_id: str
    credential_set_version: int


class LlmConnectorRepository:
    """Persistence gateway for LLM connector settings."""

    def __init__(self, db: DatabaseRuntime, secret_store: SecretStore | None = None):
        self._db = db
        self._secret_store = secret_store or get_secret_store()
        self._workspace_resources = WorkspaceRepository(db=db)

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
            ).with_for_update()
            row = session.execute(stmt).scalar_one_or_none()
            is_new = row is None
            if row is None:
                row = LlmConnectorConfigOrm(
                    id=str(uuid4()),
                    workspace_id=request.workspace_id,
                    name=request.name,
                    is_active=request.is_active,
                    is_default=False,
                    created_at=now,
                    updated_at=now,
                    base_url=request.connection.base_url,
                    model=request.connection.model,
                    api_key=None,
                    endpoint_path=request.connection.endpoint_path,
                    temperature=request.connection.temperature,
                    top_p=request.connection.top_p,
                    reasoning_effort=request.connection.reasoning_effort,
                    max_tokens=request.connection.max_tokens,
                    max_tokens_parameter=request.connection.max_tokens_parameter,
                    headers=request.connection.headers,
                    extra_settings=extra_settings,
                    credential_set_version=1,
                )
                session.add(row)
            else:
                row.updated_at = now
                row.is_active = request.is_active
                row.base_url = request.connection.base_url
                row.model = request.connection.model
                row.endpoint_path = request.connection.endpoint_path
                row.top_p = request.connection.top_p
                row.reasoning_effort = request.connection.reasoning_effort
                row.temperature = request.connection.temperature
                row.max_tokens = request.connection.max_tokens
                row.max_tokens_parameter = request.connection.max_tokens_parameter
                row.headers = request.connection.headers
                row.extra_settings = extra_settings

            if request.is_default is not None:
                self._set_default_state(session, row=row, is_default=request.is_default)
            elif not request.is_active and row.is_default:
                row.is_default = False

            api_keys_to_store = self._api_keys_to_store(row, request.connection)
            previous_secret_ref: str | None = None
            previous_secret_value: str | None = None
            written_secret_ref: str | None = None
            if api_keys_to_store is not None:
                existing_api_keys = [] if is_new else self._resolve_api_keys(row)
                secret_ref = row.api_key_secret_ref or self._api_key_secret_ref(
                    workspace_id=request.workspace_id,
                    connector_id=row.id,
                )
                previous_secret_ref = row.api_key_secret_ref
                if previous_secret_ref:
                    previous_secret_value = self._secret_store.get_secret(
                        workspace_id=request.workspace_id,
                        secret_key=previous_secret_ref,
                    )
                self._secret_store.set_secret(
                    workspace_id=request.workspace_id,
                    secret_key=secret_ref,
                    value=self._serialize_api_keys(api_keys_to_store),
                )
                written_secret_ref = secret_ref
                row.api_key_secret_ref = secret_ref
                row.api_key = None
                if not is_new and existing_api_keys != api_keys_to_store:
                    row.credential_set_version += 1

            try:
                session.commit()
            except Exception:
                try:
                    if written_secret_ref is not None:
                        self._compensate_secret_write(
                            workspace_id=request.workspace_id,
                            written_secret_ref=written_secret_ref,
                            previous_secret_ref=previous_secret_ref,
                            previous_secret_value=previous_secret_value,
                        )
                finally:
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
            row = session.execute(
                select(LlmConnectorConfigOrm)
                .where(
                    LlmConnectorConfigOrm.id == connector_id,
                    LlmConnectorConfigOrm.workspace_id == workspace_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return None
            row.updated_at = now
            row.name = request.name
            row.is_active = request.is_active
            if request.is_default is not None:
                self._set_default_state(session, row=row, is_default=request.is_default)
            elif not request.is_active and row.is_default:
                row.is_default = False
            row.base_url = request.connection.base_url
            row.model = request.connection.model
            row.endpoint_path = request.connection.endpoint_path
            row.top_p = request.connection.top_p
            row.reasoning_effort = request.connection.reasoning_effort
            row.temperature = request.connection.temperature
            row.max_tokens = request.connection.max_tokens
            row.max_tokens_parameter = request.connection.max_tokens_parameter
            row.headers = request.connection.headers
            row.extra_settings = self._build_extra_settings(
                base=request.connection.extra_settings,
                verify_tls=request.connection.verify_tls,
            )
            api_keys_to_store = self._api_keys_to_store(row, request.connection)
            previous_secret_ref: str | None = None
            previous_secret_value: str | None = None
            written_secret_ref: str | None = None
            if api_keys_to_store is not None:
                existing_api_keys = self._resolve_api_keys(row)
                secret_ref = row.api_key_secret_ref or self._api_key_secret_ref(
                    workspace_id=workspace_id,
                    connector_id=row.id,
                )
                previous_secret_ref = row.api_key_secret_ref
                if previous_secret_ref:
                    previous_secret_value = self._secret_store.get_secret(
                        workspace_id=workspace_id,
                        secret_key=previous_secret_ref,
                    )
                self._secret_store.set_secret(
                    workspace_id=workspace_id,
                    secret_key=secret_ref,
                    value=self._serialize_api_keys(api_keys_to_store),
                )
                written_secret_ref = secret_ref
                row.api_key_secret_ref = secret_ref
                row.api_key = None
                if existing_api_keys != api_keys_to_store:
                    row.credential_set_version += 1

            try:
                session.commit()
            except Exception:
                try:
                    if written_secret_ref is not None:
                        self._compensate_secret_write(
                            workspace_id=workspace_id,
                            written_secret_ref=written_secret_ref,
                            previous_secret_ref=previous_secret_ref,
                            previous_secret_value=previous_secret_value,
                        )
                finally:
                    session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row)

    def list(self, workspace_id: str, limit: int) -> list[LlmConnectorItem]:
        with self._db.session() as session:
            stmt = (
                select(LlmConnectorConfigOrm)
                .where(LlmConnectorConfigOrm.workspace_id == workspace_id)
                .order_by(
                    LlmConnectorConfigOrm.is_default.desc(),
                    LlmConnectorConfigOrm.updated_at.desc(),
                )
                .limit(limit)
            )
            rows = session.execute(stmt).scalars().all()

        result = [self._to_contract(row) for row in rows]
        local_names = {item.name for item in result}
        for grant in self._workspace_resources.list_resource_grants(workspace_id=workspace_id):
            if (
                not grant.is_active
                or grant.resource_kind != WorkspaceResourceKind.LLM_CONNECTOR
                or grant.alias in local_names
            ):
                continue
            with self._db.session() as session:
                row = session.get(LlmConnectorConfigOrm, grant.resource_id)
            if row is None or row.workspace_id != grant.owner_workspace_id or not row.is_active:
                continue
            result.append(
                self._to_contract(
                    row,
                    workspace_id=workspace_id,
                    name=grant.alias,
                    is_default=False,
                )
            )
            if len(result) >= limit:
                break
        return result[:limit]

    def get(self, workspace_id: str, name: str | None = None) -> LlmConnectorItem | None:
        if name:
            resolved = self._workspace_resources.resolve_resource(
                workspace_id=workspace_id,
                resource_kind=WorkspaceResourceKind.LLM_CONNECTOR,
                alias=name,
            )
            if resolved is None:
                return None
            with self._db.session() as session:
                row = session.get(LlmConnectorConfigOrm, resolved.resource_id)
            if row is None:
                return None
            return self._to_contract(
                row,
                workspace_id=workspace_id,
                name=name,
                is_default=False if resolved.is_shared else row.is_default,
            )
        with self._db.session() as session:
            stmt = select(LlmConnectorConfigOrm).where(
                LlmConnectorConfigOrm.workspace_id == workspace_id,
                LlmConnectorConfigOrm.is_active.is_(True),
            )
            stmt = stmt.where(LlmConnectorConfigOrm.is_default.is_(True))
            stmt = stmt.limit(1)
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

    def _to_contract(
        self,
        row: LlmConnectorConfigOrm,
        *,
        workspace_id: str | None = None,
        name: str | None = None,
        is_default: bool | None = None,
    ) -> LlmConnectorItem:
        api_keys = self._resolve_api_keys(row)
        connection = LlmConnectionConfig(
            base_url=row.base_url,
            model=row.model,
            api_key=None,
            api_keys=None,
            retained_api_key_indexes=None,
            endpoint_path=row.endpoint_path,
            verify_tls=LlmConnectorRepository._resolve_verify_tls(row.extra_settings),
            temperature=row.temperature,
            top_p=row.top_p,
            reasoning_effort=row.reasoning_effort,
            max_tokens=row.max_tokens,
            max_tokens_parameter=row.max_tokens_parameter,
            headers=row.headers,
            extra_settings=row.extra_settings,
        )
        return LlmConnectorItem(
            id=row.id,
            name=name or row.name,
            workspace_id=workspace_id or row.workspace_id,
            is_active=row.is_active,
            is_default=row.is_default if is_default is None else is_default,
            connection=connection,
            has_api_key=bool(api_keys),
            api_key_count=len(api_keys),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def get_connection(
        self, workspace_id: str, name: str | None = None
    ) -> LlmConnectionConfig | None:
        resolution = self.get_connection_resolution(workspace_id=workspace_id, name=name)
        return resolution.connection if resolution is not None else None

    def get_connection_resolution(
        self, workspace_id: str, name: str | None = None
    ) -> LlmConnectionResolution | None:
        resolved = None
        if name:
            resolved = self._workspace_resources.resolve_resource(
                workspace_id=workspace_id,
                resource_kind=WorkspaceResourceKind.LLM_CONNECTOR,
                alias=name,
            )
            if resolved is None:
                return None
        with self._db.session() as session:
            stmt = select(LlmConnectorConfigOrm).where(
                LlmConnectorConfigOrm.workspace_id
                == (resolved.owner_workspace_id if resolved is not None else workspace_id),
                LlmConnectorConfigOrm.is_active.is_(True),
            )
            if resolved is not None:
                stmt = stmt.where(LlmConnectorConfigOrm.id == resolved.resource_id)
            else:
                stmt = stmt.where(LlmConnectorConfigOrm.is_default.is_(True))
            stmt = stmt.limit(1).with_for_update(read=True)
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                return None
            api_keys = self._resolve_api_keys(row)
            connection = LlmConnectionConfig(
                base_url=row.base_url,
                model=row.model,
                api_key=api_keys[0] if api_keys else None,
                api_keys=api_keys,
                endpoint_path=row.endpoint_path,
                verify_tls=self._resolve_verify_tls(row.extra_settings),
                temperature=row.temperature,
                top_p=row.top_p,
                reasoning_effort=row.reasoning_effort,
                max_tokens=row.max_tokens,
                max_tokens_parameter=row.max_tokens_parameter,
                headers=row.headers,
                extra_settings=row.extra_settings,
            )
            connection.configure_connector_identity(
                connector_id=row.id,
                credential_set_version=row.credential_set_version,
            )
            return LlmConnectionResolution(
                connection=connection,
                connector_id=row.id,
                credential_set_version=row.credential_set_version,
            )

    @staticmethod
    def _set_default_state(session, *, row: LlmConnectorConfigOrm, is_default: bool) -> None:
        if is_default:
            session.execute(
                update(LlmConnectorConfigOrm)
                .where(
                    LlmConnectorConfigOrm.workspace_id == row.workspace_id,
                    LlmConnectorConfigOrm.id != row.id,
                    LlmConnectorConfigOrm.is_default.is_(True),
                )
                .values(is_default=False)
            )
        row.is_default = is_default

    def _api_keys_to_store(
        self,
        row: LlmConnectorConfigOrm,
        connection: LlmConnectionConfig,
    ) -> list[str] | None:
        submitted = self._submitted_api_keys(connection)
        retained_indexes = connection.retained_api_key_indexes
        if submitted is None and retained_indexes is None:
            return None

        existing = self._resolve_api_keys(row)
        retained = (
            [existing[index] for index in retained_indexes if index < len(existing)]
            if retained_indexes is not None
            else []
        )
        return self._dedupe_api_keys([*retained, *(submitted or [])])

    def _resolve_api_keys(self, row: LlmConnectorConfigOrm) -> list[str]:
        if row.api_key_secret_ref:
            raw_secret = self._secret_store.get_secret(
                workspace_id=row.workspace_id,
                secret_key=row.api_key_secret_ref,
            )
            if raw_secret is not None:
                return self._deserialize_api_keys(raw_secret)
        return [row.api_key] if row.api_key else []

    @staticmethod
    def _submitted_api_keys(connection: LlmConnectionConfig) -> list[str] | None:
        if connection.api_keys is not None:
            return connection.api_keys
        if connection.api_key is not None:
            api_key = connection.api_key.strip()
            return [api_key] if api_key else []
        return None

    @staticmethod
    def _serialize_api_keys(api_keys: list[str]) -> str:
        return json.dumps(api_keys, separators=(",", ":"))

    @staticmethod
    def _deserialize_api_keys(raw_secret: str) -> list[str]:
        try:
            loaded = json.loads(raw_secret)
        except json.JSONDecodeError:
            return [raw_secret] if raw_secret else []
        if not isinstance(loaded, list):
            return []
        return LlmConnectorRepository._dedupe_api_keys(
            [item for item in loaded if isinstance(item, str)]
        )

    @staticmethod
    def _dedupe_api_keys(api_keys: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in api_keys:
            candidate = item.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    def _delete_secret(self, workspace_id: str, secret_ref: str | None) -> None:
        if not secret_ref:
            return
        try:
            self._secret_store.delete_secret(workspace_id=workspace_id, secret_key=secret_ref)
        except SecretStoreError:
            return

    def _compensate_secret_write(
        self,
        *,
        workspace_id: str,
        written_secret_ref: str,
        previous_secret_ref: str | None,
        previous_secret_value: str | None,
    ) -> None:
        """Best-effort rollback for a secret write when the DB commit fails."""
        try:
            if previous_secret_ref == written_secret_ref and previous_secret_value is not None:
                self._secret_store.set_secret(
                    workspace_id=workspace_id,
                    secret_key=written_secret_ref,
                    value=previous_secret_value,
                )
            else:
                self._secret_store.delete_secret(
                    workspace_id=workspace_id,
                    secret_key=written_secret_ref,
                )
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
