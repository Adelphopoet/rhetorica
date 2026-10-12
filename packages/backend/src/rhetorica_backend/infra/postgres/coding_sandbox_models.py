from __future__ import annotations

from datetime import datetime

from rhetorica_backend.infra.postgres.base import Base
from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column


class WorkspaceCodingDefaultsOrm(Base):
    __tablename__ = "workspace_coding_defaults"

    workspace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("workspace.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    resources_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(36), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CodingSandboxLeaseOrm(Base):
    __tablename__ = "coding_sandbox_lease"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "run_id", "task_id", "attempt", name="uq_coding_sandbox_task_attempt"
        ),
        Index("ix_coding_sandbox_reconcile", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    environment_profile_id: Mapped[str] = mapped_column(String(128), nullable=False)
    environment_profile_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    image_digest: Mapped[str] = mapped_column(String(512), nullable=False)
    runtime_class_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_versions: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    software_manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    job_name: Mapped[str] = mapped_column(String(63), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    checkpoint_ref: Mapped[str | None] = mapped_column(String(2048))
    candidate_artifact_id: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CodingEnvironmentProfileOrm(Base):
    __tablename__ = "coding_environment_profile"
    __table_args__ = (Index("ix_coding_environment_profile_latest", "id", "revision"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_digest: Mapped[str] = mapped_column(String(512), nullable=False)
    runtime_class_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_versions: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    max_runtime_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    cpu_limit: Mapped[str] = mapped_column(String(32), nullable=False)
    memory_limit: Mapped[str] = mapped_column(String(32), nullable=False)
    disk_limit_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pid_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    output_limit_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    software_manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    dependency_sources: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    pip_dependencies: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
