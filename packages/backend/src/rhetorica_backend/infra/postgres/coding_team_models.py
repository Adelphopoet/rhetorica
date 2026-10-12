"""Revisioned, non-secret coding-agent team configuration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rhetorica_backend.infra.postgres.base import Base
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class CodingAgentTeamOrm(Base):
    __tablename__ = "coding_agent_team"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_coding_agent_team_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    active_revision_id: Mapped[str | None] = mapped_column(String(36), index=True)
    latest_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CodingAgentTeamRevisionOrm(Base):
    __tablename__ = "coding_agent_team_revision"
    __table_args__ = (
        UniqueConstraint("team_id", "revision", name="uq_coding_agent_team_revision"),
        Index("ix_coding_agent_team_revision_workspace", "workspace_id", "team_id", "revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("coding_agent_team.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    coordinator_agent_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    coordinator_instruction: Mapped[str] = mapped_column(String(12_000), nullable=False, default="")
    members_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
