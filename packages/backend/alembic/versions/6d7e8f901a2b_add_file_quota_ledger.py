"""add atomic file quota ledger

Revision ID: 6d7e8f901a2b
Revises: 5c6d7e8f901a
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6d7e8f901a2b"
down_revision: str | Sequence[str] | None = "5c6d7e8f901a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "file_quota_usage",
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.String(length=128), nullable=False),
        sa.Column("used_bytes", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "used_bytes >= 0",
            name="ck_file_quota_usage_non_negative",
        ),
        sa.PrimaryKeyConstraint("scope_type", "scope_id"),
    )
    op.execute(
        sa.text(
            """
        INSERT INTO file_quota_usage (scope_type, scope_id, used_bytes, updated_at)
        SELECT 'workspace', workspace_id, COALESCE(SUM(size_bytes), 0), CURRENT_TIMESTAMP
        FROM file_artifact
        GROUP BY workspace_id
        """
        )
    )
    op.execute(
        sa.text(
            """
        INSERT INTO file_quota_usage (scope_type, scope_id, used_bytes, updated_at)
        SELECT 'user', owner_user_id, COALESCE(SUM(size_bytes), 0), CURRENT_TIMESTAMP
        FROM file_artifact
        GROUP BY owner_user_id
        """
        )
    )


def downgrade() -> None:
    op.drop_table("file_quota_usage")
