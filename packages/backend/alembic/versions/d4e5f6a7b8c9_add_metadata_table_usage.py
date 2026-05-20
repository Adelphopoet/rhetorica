"""add metadata table usage

Revision ID: d4e5f6a7b8c9
Revises: b3d4e5f6a7b8
Create Date: 2026-04-25 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "b3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "metadata_table_usage" in tables:
        return

    op.create_table(
        "metadata_table_usage",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("connector_name", sa.String(length=128), nullable=False),
        sa.Column("table_fqn", sa.String(length=1024), nullable=False),
        sa.Column("positive_score", sa.Float(), nullable=False),
        sa.Column("negative_score", sa.Float(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "connector_name",
            "table_fqn",
            name="uq_metadata_table_usage_scope_table",
        ),
    )
    op.create_index(
        "ix_metadata_table_usage_workspace_id",
        "metadata_table_usage",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_metadata_table_usage_connector_name",
        "metadata_table_usage",
        ["connector_name"],
        unique=False,
    )
    op.create_index(
        "ix_metadata_table_usage_table_fqn",
        "metadata_table_usage",
        ["table_fqn"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "metadata_table_usage" not in tables:
        return

    op.drop_index("ix_metadata_table_usage_table_fqn", table_name="metadata_table_usage")
    op.drop_index("ix_metadata_table_usage_connector_name", table_name="metadata_table_usage")
    op.drop_index("ix_metadata_table_usage_workspace_id", table_name="metadata_table_usage")
    op.drop_table("metadata_table_usage")
