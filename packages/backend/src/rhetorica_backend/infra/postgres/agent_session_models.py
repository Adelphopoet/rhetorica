"""Shared persistence for SDK sessions; ownership remains on the bound aggregate."""

from datetime import datetime
from uuid import uuid4

from rhetorica_backend.infra.postgres.base import Base
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column


class AgentSessionOrm(Base):
    """Shared SDK session with exactly one authorized conversation or task owner."""

    __tablename__ = "agent_session"
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_agent_session_conversation"),
        UniqueConstraint("task_id", name="uq_agent_session_task"),
        CheckConstraint(
            "(conversation_id IS NOT NULL AND task_id IS NULL) OR "
            "(conversation_id IS NULL AND task_id IS NOT NULL)",
            name="ck_agent_session_subject",
        ),
        CheckConstraint(
            "next_sequence >= 0",
            name="ck_agent_session_next_sequence_non_negative",
        ),
    )

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "chat_conversation.id", name="fk_agent_session_conversation", ondelete="CASCADE"
        ),
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("coding_agent_task.id", name="fk_agent_session_task", ondelete="CASCADE"),
    )
    next_sequence: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    context_metadata: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentSessionItemOrm(Base):
    """Ordered JSON item persisted by the OpenAI Agents SDK Session protocol."""

    __tablename__ = "agent_session_item"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "sequence",
            name="uq_agent_session_item_sequence",
        ),
        CheckConstraint(
            "sequence >= 0",
            name="ck_agent_session_item_sequence_non_negative",
        ),
        Index(
            "ix_agent_session_item_conversation_sequence",
            "session_id",
            "sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agent_session.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    item_type: Mapped[str] = mapped_column(String(64), nullable=False)
    item_data: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
