"""Versioned persistence models for dangerous agent coding permissions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rhetorica_backend.infra.postgres.base import Base
from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class AgentGovernedPolicyOrm(Base):
    __tablename__ = "agent_governed_policy"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_agent_governed_policy_revision_positive"),
        Index("ix_agent_governed_policy_workspace", "workspace_id"),
    )

    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_profile.id", ondelete="CASCADE"), primary_key=True
    )
    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="SET NULL")
    )
    updated_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DefaultAssistantPolicyOrm(Base):
    __tablename__ = "default_assistant_policy"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_default_assistant_policy_revision_positive"),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="SET NULL")
    )
    updated_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
