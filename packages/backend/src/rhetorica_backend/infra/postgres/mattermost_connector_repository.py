from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from rhetorica_backend.contracts.admin_integrations import (
    MattermostConnectionConfig,
    MattermostConnectorItem,
    UpsertMattermostConnectorRequest,
)
from rhetorica_backend.infra.postgres.models import (
    MattermostConnectorConfigOrm,
    MattermostEventLedgerOrm,
    MattermostThreadBindingOrm,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.secrets import SecretStore, SecretStoreError, get_secret_store
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


class MattermostConnectorRepository:
    """Persistence gateway for Mattermost/Tag connector settings."""

    _DEFAULT_BINDING_TTL_SECONDS = 4 * 60 * 60

    def __init__(
        self,
        db: DatabaseRuntime,
        secret_store: SecretStore | None = None,
        binding_ttl_seconds: int = _DEFAULT_BINDING_TTL_SECONDS,
    ):
        self._db = db
        self._secret_store = secret_store or get_secret_store()
        self._binding_ttl = timedelta(seconds=binding_ttl_seconds)

    def upsert(self, request: UpsertMattermostConnectorRequest) -> MattermostConnectorItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.execute(
                select(MattermostConnectorConfigOrm).where(
                    MattermostConnectorConfigOrm.workspace_id == request.workspace_id,
                    MattermostConnectorConfigOrm.name == request.name,
                )
            ).scalar_one_or_none()
            if row is None:
                row = MattermostConnectorConfigOrm(
                    id=str(uuid4()),
                    workspace_id=request.workspace_id,
                    name=request.name,
                    is_active=request.is_active,
                    agent_id=request.agent_id,
                    provider=request.connection.provider,
                    base_url=request.connection.base_url,
                    bot_token=request.connection.bot_token,
                    bot_username=request.connection.bot_username,
                    verify_tls=request.connection.verify_tls,
                    listen_enabled=request.connection.listen_enabled,
                    send_sql_enabled=request.connection.send_sql_enabled,
                    ambient_listen_enabled=request.connection.ambient_listen_enabled,
                    ambient_channel_ids=request.connection.ambient_channel_ids,
                    ambient_decision_prompt=request.connection.ambient_decision_prompt,
                    extra_settings=request.connection.extra_settings,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.updated_at = now
                row.is_active = request.is_active
                row.agent_id = request.agent_id
                row.provider = request.connection.provider
                row.base_url = request.connection.base_url
                row.bot_token = request.connection.bot_token or row.bot_token
                row.bot_username = request.connection.bot_username
                row.verify_tls = request.connection.verify_tls
                row.listen_enabled = request.connection.listen_enabled
                row.send_sql_enabled = request.connection.send_sql_enabled
                row.ambient_listen_enabled = request.connection.ambient_listen_enabled
                row.ambient_channel_ids = request.connection.ambient_channel_ids
                row.ambient_decision_prompt = request.connection.ambient_decision_prompt
                row.extra_settings = request.connection.extra_settings

            if request.connection.bot_token is not None:
                secret_ref = row.bot_token_secret_ref or self._bot_token_secret_ref(
                    workspace_id=request.workspace_id,
                    connector_id=row.id,
                )
                self._secret_store.set_secret(
                    workspace_id=request.workspace_id,
                    secret_key=secret_ref,
                    value=request.connection.bot_token,
                )
                row.bot_token_secret_ref = secret_ref
                row.bot_token = None

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
        request: UpsertMattermostConnectorRequest,
    ) -> MattermostConnectorItem | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(MattermostConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return None

            row.updated_at = now
            row.name = request.name
            row.is_active = request.is_active
            row.agent_id = request.agent_id
            row.provider = request.connection.provider
            row.base_url = request.connection.base_url
            row.bot_token = request.connection.bot_token or row.bot_token
            row.bot_username = request.connection.bot_username
            row.verify_tls = request.connection.verify_tls
            row.listen_enabled = request.connection.listen_enabled
            row.send_sql_enabled = request.connection.send_sql_enabled
            row.ambient_listen_enabled = request.connection.ambient_listen_enabled
            row.ambient_channel_ids = request.connection.ambient_channel_ids
            row.ambient_decision_prompt = request.connection.ambient_decision_prompt
            row.extra_settings = request.connection.extra_settings

            if request.connection.bot_token is not None:
                secret_ref = row.bot_token_secret_ref or self._bot_token_secret_ref(
                    workspace_id=workspace_id,
                    connector_id=row.id,
                )
                self._secret_store.set_secret(
                    workspace_id=workspace_id,
                    secret_key=secret_ref,
                    value=request.connection.bot_token,
                )
                row.bot_token_secret_ref = secret_ref
                row.bot_token = None

            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row)

    def list(self, workspace_id: str, limit: int) -> list[MattermostConnectorItem]:
        with self._db.session() as session:
            rows = (
                session.execute(
                    select(MattermostConnectorConfigOrm)
                    .where(MattermostConnectorConfigOrm.workspace_id == workspace_id)
                    .order_by(MattermostConnectorConfigOrm.updated_at.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        return [self._to_contract(row) for row in rows]

    def list_enabled(self) -> list[MattermostConnectorItem]:
        with self._db.session() as session:
            rows = (
                session.execute(
                    select(MattermostConnectorConfigOrm).where(
                        MattermostConnectorConfigOrm.is_active.is_(True),
                        MattermostConnectorConfigOrm.listen_enabled.is_(True),
                    )
                )
                .scalars()
                .all()
            )
        return [self._to_contract_with_secret(row) for row in rows]

    def get_connection(
        self,
        workspace_id: str,
        name: str | None = None,
    ) -> MattermostConnectionConfig | None:
        with self._db.session() as session:
            stmt = select(MattermostConnectorConfigOrm).where(
                MattermostConnectorConfigOrm.workspace_id == workspace_id,
                MattermostConnectorConfigOrm.is_active.is_(True),
            )
            if name:
                stmt = stmt.where(MattermostConnectorConfigOrm.name == name)
            row = session.execute(
                stmt.order_by(MattermostConnectorConfigOrm.updated_at.desc()).limit(1)
            ).scalar_one_or_none()
        if row is None:
            return None
        return self._connection_from_row(row, include_secret=True)

    def delete(self, connector_id: str, workspace_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(MattermostConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return False
            self._delete_secret(row.workspace_id, row.bot_token_secret_ref)
            session.delete(row)
            session.commit()
            return True

    def get_or_create_binding(
        self,
        *,
        workspace_id: str,
        connector_id: str,
        channel_id: str,
        thread_key: str,
        conversation_id_factory,
        conversation_exists: Callable[[str], bool] | None = None,
    ) -> str:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.execute(
                select(MattermostThreadBindingOrm).where(
                    MattermostThreadBindingOrm.connector_id == connector_id,
                    MattermostThreadBindingOrm.channel_id == channel_id,
                    MattermostThreadBindingOrm.thread_key == thread_key,
                )
            ).scalar_one_or_none()
            if row is not None:
                if (conversation_exists is not None and not conversation_exists(row.conversation_id)) or self._binding_is_expired(row.updated_at, now):
                    session.delete(row)
                    session.commit()
                else:
                    row.updated_at = now
                    session.commit()
                    return row.conversation_id

        conversation_id = conversation_id_factory()
        with self._db.session() as session:
            row = session.execute(
                select(MattermostThreadBindingOrm).where(
                    MattermostThreadBindingOrm.connector_id == connector_id,
                    MattermostThreadBindingOrm.channel_id == channel_id,
                    MattermostThreadBindingOrm.thread_key == thread_key,
                )
            ).scalar_one_or_none()
            if row is not None:
                if (
                    (conversation_exists is None or conversation_exists(row.conversation_id))
                    and not self._binding_is_expired(row.updated_at, now)
                ):
                    row.updated_at = now
                    session.commit()
                    return row.conversation_id
                session.delete(row)
                session.commit()

        with self._db.session() as session:
            row = MattermostThreadBindingOrm(
                id=str(uuid4()),
                workspace_id=workspace_id,
                connector_id=connector_id,
                channel_id=channel_id,
                thread_key=thread_key,
                conversation_id=conversation_id,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.execute(
                    select(MattermostThreadBindingOrm).where(
                        MattermostThreadBindingOrm.connector_id == connector_id,
                        MattermostThreadBindingOrm.channel_id == channel_id,
                        MattermostThreadBindingOrm.thread_key == thread_key,
                    )
                ).scalar_one()
                return existing.conversation_id
        return conversation_id

    def list_conversation_post_ids(
        self,
        *,
        connector_id: str,
        conversation_id: str,
    ) -> set[str]:
        """Return inbound Mattermost posts already completed for a conversation."""
        with self._db.session() as session:
            values = session.execute(
                select(MattermostEventLedgerOrm.post_id).where(
                    MattermostEventLedgerOrm.connector_id == connector_id,
                    MattermostEventLedgerOrm.conversation_id == conversation_id,
                    MattermostEventLedgerOrm.status == "responded",
                )
            ).scalars().all()
        return {value for value in values if isinstance(value, str) and value}

    def claim_event(
        self,
        *,
        workspace_id: str,
        connector_id: str,
        channel_id: str,
        post_id: str,
        trigger: str,
    ) -> bool:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = MattermostEventLedgerOrm(
                id=str(uuid4()),
                workspace_id=workspace_id,
                connector_id=connector_id,
                channel_id=channel_id,
                post_id=post_id,
                trigger=trigger,
                status="processing",
                reason=None,
                conversation_id=None,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return False
        return True

    def complete_event(
        self,
        *,
        connector_id: str,
        post_id: str,
        status: str,
        reason: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.execute(
                select(MattermostEventLedgerOrm).where(
                    MattermostEventLedgerOrm.connector_id == connector_id,
                    MattermostEventLedgerOrm.post_id == post_id,
                )
            ).scalar_one_or_none()
            if row is None:
                return
            row.status = status
            row.reason = reason
            row.conversation_id = conversation_id
            row.updated_at = now
            session.commit()

    def _binding_is_expired(self, updated_at: datetime, now: datetime) -> bool:
        if self._binding_ttl.total_seconds() <= 0:
            return False
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return now - updated_at > self._binding_ttl

    def _to_contract(self, row: MattermostConnectorConfigOrm) -> MattermostConnectorItem:
        return MattermostConnectorItem(
            id=row.id,
            name=row.name,
            workspace_id=row.workspace_id,
            is_active=row.is_active,
            agent_id=row.agent_id,
            connection=self._connection_from_row(row, include_secret=False),
            has_bot_token=bool(row.bot_token_secret_ref or row.bot_token),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _to_contract_with_secret(
        self, row: MattermostConnectorConfigOrm
    ) -> MattermostConnectorItem:
        return MattermostConnectorItem(
            id=row.id,
            name=row.name,
            workspace_id=row.workspace_id,
            is_active=row.is_active,
            agent_id=row.agent_id,
            connection=self._connection_from_row(row, include_secret=True),
            has_bot_token=bool(row.bot_token_secret_ref or row.bot_token),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _connection_from_row(
        self,
        row: MattermostConnectorConfigOrm,
        *,
        include_secret: bool,
    ) -> MattermostConnectionConfig:
        return MattermostConnectionConfig(
            provider="mattermost",
            base_url=row.base_url,
            bot_token=self._resolve_bot_token(row) if include_secret else None,
            bot_username=row.bot_username,
            verify_tls=row.verify_tls,
            listen_enabled=row.listen_enabled,
            send_sql_enabled=getattr(row, "send_sql_enabled", False),
            ambient_listen_enabled=getattr(row, "ambient_listen_enabled", False),
            ambient_channel_ids=list(getattr(row, "ambient_channel_ids", []) or []),
            ambient_decision_prompt=getattr(row, "ambient_decision_prompt", None),
            extra_settings=row.extra_settings,
        )

    def _resolve_bot_token(self, row: MattermostConnectorConfigOrm) -> str | None:
        if row.bot_token_secret_ref:
            token = self._secret_store.get_secret(
                workspace_id=row.workspace_id,
                secret_key=row.bot_token_secret_ref,
            )
            if token is not None:
                return token
        return row.bot_token

    def _delete_secret(self, workspace_id: str, secret_ref: str | None) -> None:
        if not secret_ref:
            return
        try:
            self._secret_store.delete_secret(workspace_id=workspace_id, secret_key=secret_ref)
        except SecretStoreError:
            return

    @staticmethod
    def _bot_token_secret_ref(workspace_id: str, connector_id: str) -> str:
        return f"mattermost_connector/{workspace_id}/{connector_id}/bot_token"
