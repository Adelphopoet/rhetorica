"""add metric definitions table for governed metric registry

Revision ID: e9f8a7b6c5d4
Revises: 9e984173be52
Create Date: 2026-05-05 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9f8a7b6c5d4"
down_revision: str | None = "9e984173be52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metric_definitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("metric_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False),
        sa.Column("grain", sa.String(length=64), nullable=False),
        sa.Column("allowed_dimensions", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("default_filters", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("owner", sa.String(length=128), nullable=True),
        sa.Column("certified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source_tables", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("join_policy", sa.String(length=64), nullable=True),
        sa.Column("freshness_hours", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "metric_id",
            name="uq_metric_definitions_workspace_metric",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_metric_definitions_workspace_name",
        ),
    )
    op.create_index(
        "ix_metric_definitions_workspace_id",
        "metric_definitions",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_metric_definitions_workspace_id", table_name="metric_definitions")
    op.drop_table("metric_definitions")
