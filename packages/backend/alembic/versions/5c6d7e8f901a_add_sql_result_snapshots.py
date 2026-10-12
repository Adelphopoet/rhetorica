"""add persisted SQL result snapshot metadata

Revision ID: 5c6d7e8f901a
Revises: 4b5c6d7e8f90
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c6d7e8f901a"
down_revision: str | Sequence[str] | None = "4b5c6d7e8f90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "query_execution_history",
        sa.Column("snapshot_storage_key", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "query_execution_history",
        sa.Column("snapshot_columns", sa.JSON(), nullable=True),
    )
    op.add_column(
        "query_execution_history",
        sa.Column("snapshot_row_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "query_execution_history",
        sa.Column("snapshot_size_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "query_execution_history",
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "query_execution_history",
        sa.Column("snapshot_owner_user_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "query_execution_history",
        sa.Column("snapshot_conversation_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "query_execution_history",
        sa.Column("snapshot_possibly_truncated", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "query_execution_history",
        sa.Column("snapshot_created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "query_execution_history",
        sa.Column(
            "snapshot_delete_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_query_execution_snapshot_owner",
        "query_execution_history",
        ["snapshot_owner_user_id"],
    )
    op.create_index(
        "ix_query_execution_snapshot_conversation",
        "query_execution_history",
        ["snapshot_conversation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_query_execution_snapshot_conversation",
        table_name="query_execution_history",
    )
    op.drop_index(
        "ix_query_execution_snapshot_owner",
        table_name="query_execution_history",
    )
    for column in (
        "snapshot_created_at",
        "snapshot_delete_pending",
        "snapshot_possibly_truncated",
        "snapshot_conversation_id",
        "snapshot_owner_user_id",
        "snapshot_sha256",
        "snapshot_size_bytes",
        "snapshot_row_count",
        "snapshot_columns",
        "snapshot_storage_key",
    ):
        op.drop_column("query_execution_history", column)
