"""add airflow connector config

Revision ID: 9f0a1b2c3d4e
Revises: 8c9d0e1f2a3b
Create Date: 2026-06-04 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9f0a1b2c3d4e"
down_revision: str | Sequence[str] | None = "8c9d0e1f2a3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "airflow_connector_config" not in tables:
        op.create_table(
            "airflow_connector_config",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("provider", sa.String(length=64), nullable=False, server_default="airflow"),
            sa.Column("base_url", sa.String(length=1024), nullable=False),
            sa.Column("username", sa.String(length=255), nullable=False),
            sa.Column("password", sa.String(length=4096), nullable=True),
            sa.Column("password_secret_ref", sa.String(length=512), nullable=True),
            sa.Column("use_when", sa.Text(), nullable=False),
            sa.Column("auth_type", sa.String(length=32), nullable=False, server_default="basic"),
            sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("api_path", sa.String(length=255), nullable=False, server_default="/api/v1"),
            sa.Column("dag_limit", sa.Integer(), nullable=False, server_default="25"),
            sa.Column("dag_run_limit", sa.Integer(), nullable=False, server_default="20"),
            sa.Column("task_limit", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("log_max_chars", sa.Integer(), nullable=False, server_default="8000"),
            sa.Column("timeout_seconds", sa.Float(), nullable=False, server_default="20.0"),
            sa.Column("extra_settings", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "name", name="uq_airflow_connector_workspace_name"),
        )
        op.create_index(
            "ix_airflow_connector_config_workspace_id",
            "airflow_connector_config",
            ["workspace_id"],
            unique=False,
        )
        op.create_index(
            "ix_airflow_connector_config_is_active",
            "airflow_connector_config",
            ["is_active"],
            unique=False,
        )

    agent_columns = (
        {column["name"] for column in inspector.get_columns("agent_profile")}
        if "agent_profile" in tables
        else set()
    )
    if "agent_profile" in tables and "allowed_airflow_connector_names" not in agent_columns:
        op.add_column(
            "agent_profile",
            sa.Column(
                "allowed_airflow_connector_names",
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
        if "allowed_airflow_connector_names" in columns:
            op.drop_column("agent_profile", "allowed_airflow_connector_names")

    if "airflow_connector_config" in tables:
        op.drop_index(
            "ix_airflow_connector_config_is_active", table_name="airflow_connector_config"
        )
        op.drop_index(
            "ix_airflow_connector_config_workspace_id", table_name="airflow_connector_config"
        )
        op.drop_table("airflow_connector_config")
