"""add explicit default llm connector

Revision ID: 8f901a2b3c4d
Revises: 7e8f901a2b3c
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f901a2b3c4d"
down_revision: str | Sequence[str] | None = "7e8f901a2b3c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_connector_config",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_llm_connector_config_is_default",
        "llm_connector_config",
        ["is_default"],
        unique=False,
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id, workspace_id
            FROM llm_connector_config
            WHERE is_active = true
            ORDER BY workspace_id, updated_at DESC, id
            """
        )
    )
    selected_workspaces: set[str] = set()
    for connector_id, workspace_id in rows:
        if workspace_id in selected_workspaces:
            continue
        connection.execute(
            sa.text("UPDATE llm_connector_config SET is_default = true WHERE id = :connector_id"),
            {"connector_id": connector_id},
        )
        selected_workspaces.add(workspace_id)

    op.create_index(
        "uq_llm_connector_workspace_default",
        "llm_connector_config",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
        sqlite_where=sa.text("is_default = 1"),
    )
    op.create_check_constraint(
        "ck_llm_connector_default_is_active",
        "llm_connector_config",
        "NOT is_default OR is_active",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_llm_connector_default_is_active",
        "llm_connector_config",
        type_="check",
    )
    op.drop_index(
        "uq_llm_connector_workspace_default",
        table_name="llm_connector_config",
    )
    op.drop_index(
        "ix_llm_connector_config_is_default",
        table_name="llm_connector_config",
    )
    op.drop_column("llm_connector_config", "is_default")
