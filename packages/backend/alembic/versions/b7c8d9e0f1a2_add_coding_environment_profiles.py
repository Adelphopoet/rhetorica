"""add coding environment profiles

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "a6b7c8d9e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coding_environment_profile",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("image_digest", sa.String(length=512), nullable=False),
        sa.Column("runtime_class_name", sa.String(length=128), nullable=False),
        sa.Column("tool_versions", sa.JSON(), nullable=False),
        sa.Column("max_runtime_seconds", sa.Integer(), nullable=False),
        sa.Column("cpu_limit", sa.String(length=32), nullable=False),
        sa.Column("memory_limit", sa.String(length=32), nullable=False),
        sa.Column("disk_limit_bytes", sa.BigInteger(), nullable=False),
        sa.Column("pid_limit", sa.Integer(), nullable=False),
        sa.Column("output_limit_bytes", sa.BigInteger(), nullable=False),
        sa.Column("software_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("dependency_sources", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", "revision"),
    )
    op.create_index(
        "ix_coding_environment_profile_latest",
        "coding_environment_profile",
        ["id", "revision"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_coding_environment_profile_latest", table_name="coding_environment_profile"
    )
    op.drop_table("coding_environment_profile")
