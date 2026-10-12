"""PostgreSQL-backed client-managed memory for the OpenAI Agents SDK."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from rhetorica_backend.infra.postgres.agent_session_models import (
    AgentSessionItemOrm,
    AgentSessionOrm,
)
from rhetorica_backend.infra.postgres.chat_models import (
    ChatConversationOrm,
    ChatMessageOrm,
)
from rhetorica_backend.infra.postgres.coding_run_models import CodingAgentTaskOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from agents import SessionSettings
from agents.items import TResponseInputItem


class AgentSessionConversationNotFoundError(ValueError):
    """The requested conversation is missing or outside the owner boundary."""


class PostgresAgentSessionStore:
    """Creates SDK sessions and owns their transactional persistence."""

    _LEGACY_BOOTSTRAP_MESSAGE_LIMIT = 20

    def __init__(
        self,
        db: DatabaseRuntime,
        owner_user_id: str | None = None,
        *,
        workspace_id: str | None = None,
    ) -> None:
        self._db = db
        self._owner_user_id = owner_user_id
        self._workspace_id = workspace_id

    def for_conversation(self, conversation_id: str) -> PostgresAgentSession:
        """Return an initialized SDK Session for one authorized conversation."""
        self._ensure_initialized(conversation_id)
        return PostgresAgentSession(store=self, session_id=conversation_id)

    def for_task(self, task_id: str) -> PostgresAgentSession:
        """Create isolated memory for an owned task; never bootstrap coordinator history."""
        if self._owner_user_id is None or self._workspace_id is None:
            raise AgentSessionConversationNotFoundError(task_id)
        with self._db.session() as session:
            task = session.execute(
                select(CodingAgentTaskOrm)
                .where(
                    CodingAgentTaskOrm.id == task_id,
                    CodingAgentTaskOrm.owner_user_id == self._owner_user_id,
                    CodingAgentTaskOrm.workspace_id == self._workspace_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if task is None:
                raise AgentSessionConversationNotFoundError(task_id)
            header = session.get(AgentSessionOrm, task_id)
            if header is not None and header.task_id != task_id:
                raise AgentSessionConversationNotFoundError(task_id)
            if header is None:
                now = datetime.now(UTC)
                session.add(
                    AgentSessionOrm(
                        session_id=task_id,
                        task_id=task_id,
                        next_sequence=0,
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.commit()
            self._require_header(session, task_id)
        return PostgresAgentSession(store=self, session_id=task_id)

    def begin_operation(self, session_id: str, operation_id: str) -> None:
        """Reset a retried operation to its original input for durable receipt replay."""
        with self._db.session() as session:
            header = self._require_header(session, session_id, lock=True)
            metadata = dict(header.context_metadata or {})
            operation = metadata.get("operation")
            if operation is not None and operation["id"] == operation_id:
                sequence = operation["start_sequence"]
                session.execute(
                    delete(AgentSessionItemOrm).where(
                        AgentSessionItemOrm.session_id == session_id,
                        AgentSessionItemOrm.sequence >= sequence,
                    )
                )
                header.next_sequence = sequence
            else:
                metadata["operation"] = {"id": operation_id, "start_sequence": header.next_sequence}
                header.context_metadata = metadata
            header.updated_at = datetime.now(UTC)
            session.commit()

    def get_items(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> list[TResponseInputItem]:
        if limit is not None and limit <= 0:
            return []
        with self._db.session() as session:
            self._require_header(session, session_id)
            statement = select(AgentSessionItemOrm).where(
                AgentSessionItemOrm.session_id == session_id
            )
            if limit is None:
                rows = list(
                    session.execute(
                        statement.order_by(AgentSessionItemOrm.sequence.asc())
                    ).scalars()
                )
            else:
                rows = list(
                    session.execute(
                        statement.order_by(AgentSessionItemOrm.sequence.desc()).limit(limit)
                    ).scalars()
                )
                rows.reverse()
            return [cast(TResponseInputItem, dict(row.item_data)) for row in rows]

    def add_items(
        self,
        session_id: str,
        items: list[TResponseInputItem],
    ) -> None:
        normalized = [self._normalize_item(item) for item in items]
        if not normalized:
            return
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            header = self._require_header(session, session_id, lock=True)
            start = header.next_sequence
            session.add_all(
                AgentSessionItemOrm(
                    id=str(uuid4()),
                    session_id=session_id,
                    sequence=start + offset,
                    item_type=self._item_type(item),
                    item_data=item,
                    created_at=now,
                )
                for offset, item in enumerate(normalized)
            )
            header.next_sequence = start + len(normalized)
            header.updated_at = now
            session.commit()

    def pop_item(self, session_id: str) -> TResponseInputItem | None:
        with self._db.session() as session:
            header = self._require_header(session, session_id, lock=True)
            row = session.execute(
                select(AgentSessionItemOrm)
                .where(AgentSessionItemOrm.session_id == session_id)
                .order_by(AgentSessionItemOrm.sequence.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            item = cast(TResponseInputItem, dict(row.item_data))
            session.delete(row)
            header.updated_at = datetime.now(tz=UTC)
            session.commit()
            return item

    def clear_session(self, session_id: str) -> None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            header = self._require_header(session, session_id, lock=True)
            session.execute(
                delete(AgentSessionItemOrm).where(AgentSessionItemOrm.session_id == session_id)
            )
            header.next_sequence = 0
            header.context_metadata = {}
            header.updated_at = now
            session.commit()

    def replace_items(
        self,
        session_id: str,
        items: list[TResponseInputItem],
        context_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Atomically replace history, used when manual compaction creates a checkpoint."""
        normalized = [self._normalize_item(item) for item in items]
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            header = self._require_header(session, session_id, lock=True)
            session.execute(
                delete(AgentSessionItemOrm).where(AgentSessionItemOrm.session_id == session_id)
            )
            session.add_all(
                AgentSessionItemOrm(
                    id=str(uuid4()),
                    session_id=session_id,
                    sequence=sequence,
                    item_type=self._item_type(item),
                    item_data=item,
                    created_at=now,
                )
                for sequence, item in enumerate(normalized)
            )
            header.next_sequence = len(normalized)
            if context_metadata is not None:
                header.context_metadata = context_metadata
            header.updated_at = now
            session.commit()

    def get_context_metadata(self, session_id: str) -> dict[str, Any]:
        with self._db.session() as session:
            return dict(self._require_header(session, session_id).context_metadata or {})

    def set_context_metadata(self, session_id: str, metadata: dict[str, Any]) -> None:
        with self._db.session() as session:
            header = self._require_header(session, session_id, lock=True)
            header.context_metadata = metadata
            header.updated_at = datetime.now(tz=UTC)
            session.commit()

    def _ensure_initialized(self, session_id: str) -> None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            conversation_statement = select(ChatConversationOrm).where(
                ChatConversationOrm.id == session_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
            if self._owner_user_id is not None:
                conversation_statement = conversation_statement.where(
                    ChatConversationOrm.owner_user_id == self._owner_user_id
                )
            if self._workspace_id is not None:
                conversation_statement = conversation_statement.where(
                    ChatConversationOrm.workspace_id == self._workspace_id
                )
            conversation = session.execute(
                conversation_statement.with_for_update()
            ).scalar_one_or_none()
            if conversation is None:
                raise AgentSessionConversationNotFoundError(session_id)

            header = session.get(AgentSessionOrm, session_id)
            if header is not None:
                if header.conversation_id != session_id:
                    raise AgentSessionConversationNotFoundError(session_id)
                return

            legacy_rows = list(
                session.execute(
                    select(ChatMessageOrm)
                    .where(ChatMessageOrm.conversation_id == session_id)
                    .order_by(ChatMessageOrm.created_at.desc(), ChatMessageOrm.id.desc())
                    .limit(self._LEGACY_BOOTSTRAP_MESSAGE_LIMIT)
                ).scalars()
            )
            legacy_rows.reverse()
            header = AgentSessionOrm(
                session_id=session_id,
                conversation_id=session_id,
                next_sequence=len(legacy_rows),
                created_at=now,
                updated_at=now,
            )
            session.add(header)
            session.flush()
            session.add_all(
                AgentSessionItemOrm(
                    id=str(uuid4()),
                    session_id=session_id,
                    sequence=sequence,
                    item_type=row.role,
                    item_data={"role": row.role, "content": row.content},
                    created_at=now,
                )
                for sequence, row in enumerate(legacy_rows)
                if row.role in {"user", "assistant"}
            )
            header.next_sequence = sum(
                1 for row in legacy_rows if row.role in {"user", "assistant"}
            )
            session.commit()

    def _require_header(
        self,
        session: Session,
        session_id: str,
        *,
        lock: bool = False,
    ) -> AgentSessionOrm:
        chat_scope = [ChatConversationOrm.deleted_at.is_(None), ChatConversationOrm.id.is_not(None)]
        task_scope = [CodingAgentTaskOrm.id.is_not(None)]
        if self._owner_user_id is not None:
            chat_scope.append(ChatConversationOrm.owner_user_id == self._owner_user_id)
            task_scope.append(CodingAgentTaskOrm.owner_user_id == self._owner_user_id)
        else:
            # Internal unscoped chat callers never gain access to task histories.
            task_scope.append(CodingAgentTaskOrm.id.is_(None))
        if self._workspace_id is not None:
            chat_scope.append(ChatConversationOrm.workspace_id == self._workspace_id)
            task_scope.append(CodingAgentTaskOrm.workspace_id == self._workspace_id)
        else:
            task_scope.append(CodingAgentTaskOrm.id.is_(None))
        statement = (
            select(AgentSessionOrm)
            .outerjoin(
                ChatConversationOrm, ChatConversationOrm.id == AgentSessionOrm.conversation_id
            )
            .outerjoin(CodingAgentTaskOrm, CodingAgentTaskOrm.id == AgentSessionOrm.task_id)
            .where(
                AgentSessionOrm.session_id == session_id, or_(and_(*chat_scope), and_(*task_scope))
            )
        )
        if lock:
            statement = statement.with_for_update(of=AgentSessionOrm)
        header = session.execute(statement).scalar_one_or_none()
        if header is None:
            raise AgentSessionConversationNotFoundError(session_id)
        return header

    @staticmethod
    def _normalize_item(item: TResponseInputItem) -> dict[str, Any]:
        if isinstance(item, dict):
            return dict(item)
        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            normalized = model_dump(mode="json")
            if isinstance(normalized, dict):
                return normalized
        raise TypeError(f"Unsupported Agents SDK session item: {type(item).__name__}")

    @staticmethod
    def _item_type(item: dict[str, Any]) -> str:
        value = item.get("type") or item.get("role") or "unknown"
        return str(value)[:64]


class PostgresAgentSession:
    """Async structural implementation of the Agents SDK Session protocol."""

    session_settings: SessionSettings | None = None

    def __init__(self, *, store: PostgresAgentSessionStore, session_id: str) -> None:
        self._store = store
        self.session_id = session_id

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        return await asyncio.to_thread(self._store.get_items, self.session_id, limit)

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        await asyncio.shield(asyncio.to_thread(self._store.add_items, self.session_id, items))

    async def pop_item(self) -> TResponseInputItem | None:
        return await asyncio.shield(asyncio.to_thread(self._store.pop_item, self.session_id))

    async def clear_session(self) -> None:
        await asyncio.shield(asyncio.to_thread(self._store.clear_session, self.session_id))

    async def replace_items(
        self, items: list[TResponseInputItem], *, context_metadata: dict[str, Any] | None = None
    ) -> None:
        await asyncio.shield(
            asyncio.to_thread(self._store.replace_items, self.session_id, items, context_metadata)
        )

    async def get_context_metadata(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._store.get_context_metadata, self.session_id)

    async def set_context_metadata(self, metadata: dict[str, Any]) -> None:
        await asyncio.shield(
            asyncio.to_thread(self._store.set_context_metadata, self.session_id, metadata)
        )
