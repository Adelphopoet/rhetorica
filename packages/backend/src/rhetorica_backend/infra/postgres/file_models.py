"""SQLAlchemy persistence for immutable user-visible file artifacts."""

from __future__ import annotations

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
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column


class FileArtifactOrm(Base):
    __tablename__ = "file_artifact"
    __table_args__ = (
        Index("ix_file_artifact_owner_status", "owner_user_id", "status"),
        Index("ix_file_artifact_workspace_status", "workspace_id", "status"),
        Index("ix_file_artifact_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chat_conversation.id", ondelete="SET NULL"), nullable=True
    )
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_artifact_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    failure_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FileQuotaUsageOrm(Base):
    """Atomic quota ledger; one row per workspace and per user."""

    __tablename__ = "file_quota_usage"
    __table_args__ = (CheckConstraint("used_bytes >= 0", name="ck_file_quota_usage_non_negative"),)

    scope_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatMessageFileOrm(Base):
    __tablename__ = "chat_message_file"
    __table_args__ = (
        UniqueConstraint("message_id", "artifact_id", name="uq_chat_message_file_link"),
        UniqueConstraint("message_id", "relation", "position", name="uq_chat_message_file_order"),
        Index("ix_chat_message_file_artifact", "artifact_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_message.id", ondelete="CASCADE"), nullable=False
    )
    artifact_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("file_artifact.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
