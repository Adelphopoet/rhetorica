"""add fenced coding publication operation ledger

Revision ID: d8e9f0a1b2c3
Revises: d9e0f1a2b3c4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: str | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coding_publication_operation",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("candidate_digest", sa.String(length=64), nullable=False),
        sa.Column("validation_report_digest", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("branch_name", sa.String(length=255), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("merge_request_url", sa.String(length=2048), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.String(length=512), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "run_id", name="uq_coding_publication_workspace_run"),
    )
    op.create_index(
        op.f("ix_coding_publication_operation_workspace_id"),
        "coding_publication_operation",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_coding_publication_operation_owner_user_id"),
        "coding_publication_operation",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_coding_publication_state",
        "coding_publication_operation",
        ["state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_coding_publication_state", table_name="coding_publication_operation")
    op.drop_index(
        op.f("ix_coding_publication_operation_owner_user_id"),
        table_name="coding_publication_operation",
    )
    op.drop_index(
        op.f("ix_coding_publication_operation_workspace_id"),
        table_name="coding_publication_operation",
    )
    op.drop_table("coding_publication_operation")
