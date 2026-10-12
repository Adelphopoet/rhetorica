"""Workspace membership, shared-resource and policy-audit persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from rhetorica_backend.infra.postgres.base import Base
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column


class WorkspaceOrm(Base):
    __tablename__ = "workspace"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    metadata_json: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceMembershipOrm(Base):
    __tablename__ = "workspace_membership"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'analyst', 'viewer')", name="ck_workspace_member_role"),
        Index("ix_workspace_membership_user_active", "user_id", "is_active"),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceResourceGrantOrm(Base):
    __tablename__ = "workspace_resource_grant"
    __table_args__ = (
        UniqueConstraint(
            "recipient_workspace_id",
            "resource_kind",
            "alias",
            name="uq_workspace_resource_grant_alias",
        ),
        UniqueConstraint(
            "recipient_workspace_id",
            "resource_kind",
            "resource_id",
            "owner_workspace_id",
            name="uq_workspace_resource_grant_resource",
        ),
        Index(
            "ix_workspace_resource_grant_recipient_active",
            "recipient_workspace_id",
            "resource_kind",
            "is_active",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    resource_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    recipient_workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspacePolicyAuditEventOrm(Base):
    __tablename__ = "workspace_policy_audit_event"
    __table_args__ = (
        Index("ix_workspace_policy_audit_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    current_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
