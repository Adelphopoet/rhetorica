from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from rhetorica_backend.infra.postgres.base import Base
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship


class ScenarioDefinitionOrm(Base):
    __tablename__ = "scenario_definition"
    __table_args__ = (UniqueConstraint("workspace_id", "slug", name="uq_scenario_workspace_slug"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    owner_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    scope: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(String(280), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    tool_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    default_connector_bindings: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner = relationship("UserAccountOrm")
    runs: Mapped[list[ScenarioRunOrm]] = relationship(
        "ScenarioRunOrm",
        back_populates="scenario_definition",
    )


class ScenarioRunOrm(Base):
    __tablename__ = "scenario_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    scenario_definition_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("scenario_definition.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    resolved_version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chat_conversation.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    scenario_definition: Mapped[ScenarioDefinitionOrm] = relationship(
        "ScenarioDefinitionOrm",
        back_populates="runs",
    )
