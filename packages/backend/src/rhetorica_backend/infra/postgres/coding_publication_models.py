from __future__ import annotations

from datetime import datetime

from rhetorica_backend.infra.postgres.base import Base
from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class CodingPublicationOperationOrm(Base):
    __tablename__ = "coding_publication_operation"
    __table_args__ = (
        UniqueConstraint("workspace_id", "run_id", name="uq_coding_publication_workspace_run"),
        Index("ix_coding_publication_state", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    candidate_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_report_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    merge_request_url: Mapped[str | None] = mapped_column(String(2048))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(String(512))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
