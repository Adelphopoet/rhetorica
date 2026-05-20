"""add jira connector config

Revision ID: b3d4e5f6a7b8
Revises: f1e2d3c4b5a6
Create Date: 2026-04-08 19:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d4e5f6a7b8"
down_revision: str | None = "f1e2d3c4b5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "jira_connector_config" in tables:
        return

    op.create_table(
        "jira_connector_config",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="jira"),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("api_token", sa.String(length=4096), nullable=True),
        sa.Column("api_token_secret_ref", sa.String(length=512), nullable=True),
        sa.Column("auth_type", sa.String(length=32), nullable=False, server_default="bearer"),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("project_keys", sa.JSON(), nullable=False),
        sa.Column("issue_limit", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("extra_settings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_jira_connector_workspace_name"),
    )
    op.create_index(
        "ix_jira_connector_config_workspace_id",
        "jira_connector_config",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "jira_connector_config" not in tables:
        return

    op.drop_index("ix_jira_connector_config_workspace_id", table_name="jira_connector_config")
    op.drop_table("jira_connector_config")
