"""PostgreSQL-backed client-managed memory for the OpenAI Agents SDK."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from rhetorica_backend.infra.postgres.chat_models import (
    ChatAgentSessionItemOrm,
    ChatAgentSessionOrm,
    ChatConversationOrm,
    ChatMessageOrm,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from agents import SessionSettings
from agents.items import TResponseInputItem


class AgentSessionConversationNotFoundError(ValueError):
    """The requested conversation is missing or outside the owner boundary."""


class PostgresAgentSessionStore:
    """Creates SDK sessions and owns their transactional persistence."""

    _LEGACY_BOOTSTRAP_MESSAGE_LIMIT = 20

    def __init__(self, db: DatabaseRuntime, owner_user_id: str | None = None) -> None:
        self._db = db
        self._owner_user_id = owner_user_id

    def for_conversation(self, conversation_id: str) -> PostgresAgentSession:
        """Return an initialized SDK Session for one authorized conversation."""
        self._ensure_initialized(conversation_id)
        return PostgresAgentSession(store=self, conversation_id=conversation_id)

    def get_items(
        self,
        conversation_id: str,
        limit: int | None = None,
    ) -> list[TResponseInputItem]:
        if limit is not None and limit <= 0:
            return []
        with self._db.session() as session:
            self._require_header(session, conversation_id)
            statement = select(ChatAgentSessionItemOrm).where(
                ChatAgentSessionItemOrm.conversation_id == conversation_id
            )
            if limit is None:
                rows = list(
                    session.execute(
                        statement.order_by(ChatAgentSessionItemOrm.sequence.asc())
                    ).scalars()
                )
            else:
                rows = list(
                    session.execute(
                        statement.order_by(ChatAgentSessionItemOrm.sequence.desc()).limit(limit)
                    ).scalars()
                )
                rows.reverse()
            return [cast(TResponseInputItem, dict(row.item_data)) for row in rows]

    def add_items(
        self,
        conversation_id: str,
        items: list[TResponseInputItem],
    ) -> None:
        normalized = [self._normalize_item(item) for item in items]
        if not normalized:
            return
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            header = self._require_header(session, conversation_id, lock=True)
            start = header.next_sequence
            session.add_all(
                ChatAgentSessionItemOrm(
                    id=str(uuid4()),
                    conversation_id=conversation_id,
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

    def pop_item(self, conversation_id: str) -> TResponseInputItem | None:
        with self._db.session() as session:
            header = self._require_header(session, conversation_id, lock=True)
            row = session.execute(
                select(ChatAgentSessionItemOrm)
                .where(ChatAgentSessionItemOrm.conversation_id == conversation_id)
                .order_by(ChatAgentSessionItemOrm.sequence.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            item = cast(TResponseInputItem, dict(row.item_data))
            session.delete(row)
            header.updated_at = datetime.now(tz=UTC)
            session.commit()
            return item

    def clear_session(self, conversation_id: str) -> None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            header = self._require_header(session, conversation_id, lock=True)
            session.execute(
                delete(ChatAgentSessionItemOrm).where(
                    ChatAgentSessionItemOrm.conversation_id == conversation_id
                )
            )
            header.next_sequence = 0
            header.updated_at = now
            session.commit()

    def replace_items(
        self,
        conversation_id: str,
        items: list[TResponseInputItem],
    ) -> None:
        """Atomically replace history, used when manual compaction creates a checkpoint."""
        normalized = [self._normalize_item(item) for item in items]
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            header = self._require_header(session, conversation_id, lock=True)
            session.execute(
                delete(ChatAgentSessionItemOrm).where(
                    ChatAgentSessionItemOrm.conversation_id == conversation_id
                )
            )
            session.add_all(
                ChatAgentSessionItemOrm(
                    id=str(uuid4()),
                    conversation_id=conversation_id,
                    sequence=sequence,
                    item_type=self._item_type(item),
                    item_data=item,
                    created_at=now,
                )
                for sequence, item in enumerate(normalized)
            )
            header.next_sequence = len(normalized)
            header.updated_at = now
            session.commit()

    def _ensure_initialized(self, conversation_id: str) -> None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            conversation_statement = select(ChatConversationOrm).where(
                ChatConversationOrm.id == conversation_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
            if self._owner_user_id is not None:
                conversation_statement = conversation_statement.where(
                    ChatConversationOrm.owner_user_id == self._owner_user_id
                )
            conversation = session.execute(
                conversation_statement.with_for_update()
            ).scalar_one_or_none()
            if conversation is None:
                raise AgentSessionConversationNotFoundError(conversation_id)

            header = session.get(ChatAgentSessionOrm, conversation_id)
            if header is not None:
                return

            legacy_rows = list(
                session.execute(
                    select(ChatMessageOrm)
                    .where(ChatMessageOrm.conversation_id == conversation_id)
                    .order_by(ChatMessageOrm.created_at.desc(), ChatMessageOrm.id.desc())
                    .limit(self._LEGACY_BOOTSTRAP_MESSAGE_LIMIT)
                ).scalars()
            )
            legacy_rows.reverse()
            header = ChatAgentSessionOrm(
                conversation_id=conversation_id,
                next_sequence=len(legacy_rows),
                created_at=now,
                updated_at=now,
            )
            session.add(header)
            session.flush()
            session.add_all(
                ChatAgentSessionItemOrm(
                    id=str(uuid4()),
                    conversation_id=conversation_id,
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
        conversation_id: str,
        *,
        lock: bool = False,
    ) -> ChatAgentSessionOrm:
        statement = (
            select(ChatAgentSessionOrm)
            .join(
                ChatConversationOrm,
                ChatConversationOrm.id == ChatAgentSessionOrm.conversation_id,
            )
            .where(
                ChatAgentSessionOrm.conversation_id == conversation_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
        )
        if self._owner_user_id is not None:
            statement = statement.where(ChatConversationOrm.owner_user_id == self._owner_user_id)
        if lock:
            statement = statement.with_for_update()
        header = session.execute(statement).scalar_one_or_none()
        if header is None:
            raise AgentSessionConversationNotFoundError(conversation_id)
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

    def __init__(self, *, store: PostgresAgentSessionStore, conversation_id: str) -> None:
        self._store = store
        self.session_id = conversation_id

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        return await asyncio.to_thread(self._store.get_items, self.session_id, limit)

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        await asyncio.shield(asyncio.to_thread(self._store.add_items, self.session_id, items))

    async def pop_item(self) -> TResponseInputItem | None:
        return await asyncio.shield(asyncio.to_thread(self._store.pop_item, self.session_id))

    async def clear_session(self) -> None:
        await asyncio.shield(asyncio.to_thread(self._store.clear_session, self.session_id))

    async def replace_items(self, items: list[TResponseInputItem]) -> None:
        await asyncio.shield(asyncio.to_thread(self._store.replace_items, self.session_id, items))
