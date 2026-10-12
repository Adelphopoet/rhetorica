"""add gitlab connector config

Revision ID: 8c9d0e1f2a3b
Revises: 7b8c9d0e1f2a
Create Date: 2026-05-29 08:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c9d0e1f2a3b"
down_revision: str | Sequence[str] | None = "7b8c9d0e1f2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "gitlab_connector_config" not in tables:
        op.create_table(
            "gitlab_connector_config",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("provider", sa.String(length=64), nullable=False, server_default="gitlab"),
            sa.Column("base_url", sa.String(length=1024), nullable=False),
            sa.Column("api_token", sa.String(length=4096), nullable=True),
            sa.Column("api_token_secret_ref", sa.String(length=512), nullable=True),
            sa.Column("use_when", sa.Text(), nullable=False),
            sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("default_project_paths", sa.JSON(), nullable=False),
            sa.Column("default_group_paths", sa.JSON(), nullable=False),
            sa.Column("search_limit", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("read_max_tokens", sa.Integer(), nullable=False, server_default="1600"),
            sa.Column("extra_settings", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "name", name="uq_gitlab_connector_workspace_name"),
        )
        op.create_index(
            "ix_gitlab_connector_config_workspace_id",
            "gitlab_connector_config",
            ["workspace_id"],
            unique=False,
        )
        op.create_index(
            "ix_gitlab_connector_config_is_active",
            "gitlab_connector_config",
            ["is_active"],
            unique=False,
        )

    agent_columns = (
        {column["name"] for column in inspector.get_columns("agent_profile")}
        if "agent_profile" in tables
        else set()
    )
    if "agent_profile" in tables and "allowed_gitlab_connector_names" not in agent_columns:
        op.add_column(
            "agent_profile",
            sa.Column(
                "allowed_gitlab_connector_names",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "agent_profile" in tables:
        columns = {column["name"] for column in inspector.get_columns("agent_profile")}
        if "allowed_gitlab_connector_names" in columns:
            op.drop_column("agent_profile", "allowed_gitlab_connector_names")

    if "gitlab_connector_config" in tables:
        op.drop_index("ix_gitlab_connector_config_is_active", table_name="gitlab_connector_config")
        op.drop_index("ix_gitlab_connector_config_workspace_id", table_name="gitlab_connector_config")
        op.drop_table("gitlab_connector_config")
