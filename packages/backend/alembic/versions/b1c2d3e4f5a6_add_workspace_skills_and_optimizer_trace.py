"""add workspace skills and optimizer trace

Revision ID: b1c2d3e4f5a6
Revises: a7c3d1e9b4f2
Create Date: 2026-04-04 18:20:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a7c3d1e9b4f2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "chat_message",
        sa.Column("optimization_trace", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column("chat_message", "optimization_trace", server_default=None)

    op.create_table(
        "workspace_skill",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("capability_family", sa.String(length=64), nullable=False),
        sa.Column("engine_scope", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.String(length=500), nullable=True),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=128), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_workspace_skill_name"),
    )
    op.create_index("ix_workspace_skill_workspace_id", "workspace_skill", ["workspace_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_workspace_skill_workspace_id", table_name="workspace_skill")
    op.drop_table("workspace_skill")
    op.drop_column("chat_message", "optimization_trace")
