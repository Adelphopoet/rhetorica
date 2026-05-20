from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from rhetorica_backend.infra.postgres.base import Base
from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship


class ChatConversationOrm(Base):
    """Conversation container for multi-turn chat sessions."""

    __tablename__ = "chat_conversation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(255))
    conversation_state: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    owner = relationship("UserAccountOrm")
    messages: Mapped[list[ChatMessageOrm]] = relationship(
        "ChatMessageOrm",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class ChatMessageOrm(Base):
    """Single message within a conversation."""

    __tablename__ = "chat_message"
    __table_args__ = (
        UniqueConstraint("conversation_id", "created_at", "id", name="uq_chat_message_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_conversation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )  # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Execution metadata (optional, for assistant messages)
    generated_sql: Mapped[str | None] = mapped_column(Text)
    execution_id: Mapped[str | None] = mapped_column(String(36))
    context_tables: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    metadata_sources: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    claims: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    evidence: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    claim_evidence_links: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    uncertainty_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    capability_trace: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    optimization_trace: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    selected_connector: Mapped[str | None] = mapped_column(String(255))
    analysis_document: Mapped[dict[str, object] | None] = mapped_column(JSON)
    visualization_spec: Mapped[dict[str, object] | None] = mapped_column(JSON)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    feedback: Mapped[str | None] = mapped_column(String(32))
    feedback_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feedback_reason: Mapped[str | None] = mapped_column(String(32))
    feedback_comment: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    conversation: Mapped[ChatConversationOrm] = relationship(
        "ChatConversationOrm",
        back_populates="messages",
    )
