"""Durable state for governed coding runs and delegated agent tasks.

Only server-owned snapshots, digests and artifact references are persisted here.
Skill content, repository files, credentials and raw DWH rows remain outside of
the control-plane database and Temporal history.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rhetorica_backend.infra.postgres.base import Base
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class CodingRunOrm(Base):
    __tablename__ = "coding_run"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "owner_user_id",
            "idempotency_key",
            name="uq_coding_run_owner_idempotency",
        ),
        Index("ix_coding_run_owner_created", "workspace_id", "owner_user_id", "created_at"),
        Index("ix_coding_run_status_deadline", "status", "deadline"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chat_conversation.id", ondelete="SET NULL"), index=True
    )
    scenario_id: Mapped[str | None] = mapped_column(String(36), index=True)
    root_agent_id: Mapped[str | None] = mapped_column(String(36), index=True)
    developer_agent_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    qa_agent_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    team_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    repository_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    data_warehouse_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    environment_profile_id: Mapped[str] = mapped_column(String(128), nullable=False)
    environment_profile_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    validation_report_id: Mapped[str | None] = mapped_column(String(36), index=True)
    publication_receipt_id: Mapped[str | None] = mapped_column(String(36), index=True)
    partial_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    principal_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    environment_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CodingCandidateOrm(Base):
    """One immutable candidate per digest; ``coding_run`` points at the latest."""

    __tablename__ = "coding_candidate"
    __table_args__ = (
        UniqueConstraint("run_id", "digest", name="uq_coding_candidate_run_digest"),
        Index("ix_coding_candidate_run_created", "run_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("coding_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    base_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    changed_paths: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CodingAgentTaskOrm(Base):
    __tablename__ = "coding_agent_task"
    __table_args__ = (
        Index("ix_coding_agent_task_run_role", "run_id", "role"),
        Index("ix_coding_agent_task_owner_status", "workspace_id", "owner_user_id", "status"),
        CheckConstraint("max_iterations > 0", name="ck_coding_agent_task_iterations_positive"),
        CheckConstraint("max_runtime_seconds > 0", name="ck_coding_agent_task_runtime_positive"),
        CheckConstraint("iterations_used >= 0", name="ck_coding_agent_task_iterations_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("coding_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("coding_agent_task.id", ondelete="RESTRICT"), index=True
    )
    agent_profile_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    team_member_id: Mapped[str | None] = mapped_column(String(36))
    team_member_label: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    iterations_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    max_runtime_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    instruction_artifact_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    skill_version_digests: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    skill_version_bindings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    skill_read_addresses: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    result_artifact_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkpoint_ref: Mapped[str | None] = mapped_column(String(2048))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CodingBudgetLedgerOrm(Base):
    __tablename__ = "coding_budget_ledger"
    __table_args__ = (
        UniqueConstraint("run_id", "budget_owner_agent_id", name="uq_coding_budget_run_owner"),
        CheckConstraint("max_iterations > 0", name="ck_coding_budget_max_positive"),
        CheckConstraint("reserved_iterations >= 0", name="ck_coding_budget_reserved_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("coding_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    budget_owner_agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CodingIterationReservationOrm(Base):
    __tablename__ = "coding_iteration_reservation"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "budget_owner_agent_id",
            "invocation_id",
            name="uq_coding_iteration_reservation_invocation",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("coding_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("coding_agent_task.id", ondelete="SET NULL"), index=True
    )
    usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    budget_owner_agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    invocation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CodingRunEventOrm(Base):
    __tablename__ = "coding_run_event"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("coding_run.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(96), nullable=False)
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("coding_agent_task.id", ondelete="SET NULL"), index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CodingValidationEvidenceOrm(Base):
    __tablename__ = "coding_validation_evidence"
    __table_args__ = (
        UniqueConstraint("run_id", "id", name="uq_coding_validation_evidence_run_id"),
        Index("ix_coding_validation_candidate", "run_id", "candidate_digest"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("coding_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("coding_agent_task.id", ondelete="SET NULL"), index=True
    )
    candidate_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(String(2048))
    reason: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CodingValidationReportOrm(Base):
    __tablename__ = "coding_validation_report"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "candidate_digest", name="uq_coding_validation_report_candidate"
        ),
        Index("ix_coding_validation_report_run", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("coding_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    report_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    qa_agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    qa_verdict_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    checks_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CodingRunOutboxOrm(Base):
    __tablename__ = "coding_run_outbox"
    __table_args__ = (Index("ix_coding_run_outbox_delivery", "delivered", "created_at"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("coding_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
