"""Persistence for scenario authoring and durable executions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from rhetorica_backend.infra.postgres.base import Base
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class ScenarioDraftOrm(Base):
    __tablename__ = "scenario_draft"

    scenario_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenario_definition.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    workflow: Mapped[dict[str, Any]] = mapped_column(JSON)
    layout: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    test_cases: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_by: Mapped[str] = mapped_column(String(36))


class ScenarioVersionOrm(Base):
    __tablename__ = "scenario_version"
    __table_args__ = (UniqueConstraint("scenario_id", "version", name="uq_scenario_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    scenario_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenario_definition.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(36))


class ScenarioWorkflowRunOrm(Base):
    __tablename__ = "scenario_workflow_run"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "idempotency_key", name="uq_scenario_run_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenario_definition.id", ondelete="RESTRICT"), index=True
    )
    scenario_title: Mapped[str] = mapped_column(String(255))
    workspace_id: Mapped[str] = mapped_column(String(128), index=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    agent_snapshots: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    test_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chat_conversation.id", ondelete="SET NULL"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    result_ref: Mapped[str | None] = mapped_column(String(1024))
    error: Mapped[str | None] = mapped_column(Text)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScenarioStepRunOrm(Base):
    __tablename__ = "scenario_step_run"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenario_workflow_run.id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    operation_id: Mapped[str] = mapped_column(String(36), index=True)
    input_ref: Mapped[str | None] = mapped_column(String(1024))
    output_ref: Mapped[str | None] = mapped_column(String(1024))
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    decision: Mapped[bool | None] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScenarioRunEventOrm(Base):
    __tablename__ = "scenario_run_event"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenario_workflow_run.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(64))
    node_id: Mapped[str | None] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScenarioOutboxOrm(Base):
    __tablename__ = "scenario_outbox"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenario_workflow_run.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScenarioArtifactOrm(Base):
    __tablename__ = "scenario_artifact"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenario_workflow_run.id", ondelete="CASCADE"), primary_key=True
    )
    artifact_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("file_artifact.id", ondelete="RESTRICT"), primary_key=True
    )
    node_id: Mapped[str | None] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(16), default="output")


class ScenarioExecutionOrm(Base):
    __tablename__ = "scenario_execution"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenario_workflow_run.id", ondelete="CASCADE"), primary_key=True
    )
    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(80))
