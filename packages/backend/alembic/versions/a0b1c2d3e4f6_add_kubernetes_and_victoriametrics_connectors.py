"""add kubernetes and victoriametrics connector config

Revision ID: a0b1c2d3e4f6
Revises: 9f0a1b2c3d4e
Create Date: 2026-06-05 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a0b1c2d3e4f6"
down_revision: str | Sequence[str] | None = "9f0a1b2c3d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    existing_indexes = {
        table_name: {index["name"] for index in inspector.get_indexes(table_name)}
        for table_name in tables
    }

    if "kubernetes_connector_config" not in tables:
        op.create_table(
            "kubernetes_connector_config",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(
                "visible_to_default_assistant",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("provider", sa.String(length=64), nullable=False, server_default="kubernetes"),
            sa.Column("base_url", sa.String(length=1024), nullable=False),
            sa.Column("bearer_token", sa.String(length=4096), nullable=True),
            sa.Column("bearer_token_secret_ref", sa.String(length=512), nullable=True),
            sa.Column("use_when", sa.Text(), nullable=False),
            sa.Column("auth_type", sa.String(length=32), nullable=False, server_default="bearer"),
            sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("namespace_allowlist", sa.JSON(), nullable=False),
            sa.Column("default_limit", sa.Integer(), nullable=False, server_default="25"),
            sa.Column("max_limit", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("log_max_chars", sa.Integer(), nullable=False, server_default="8000"),
            sa.Column("timeout_seconds", sa.Float(), nullable=False, server_default="20.0"),
            sa.Column("extra_settings", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "name", name="uq_kubernetes_connector_workspace_name"),
        )
    _create_index_if_missing(
        existing_indexes,
        "ix_k8s_connector_workspace_id",
        "kubernetes_connector_config",
        ["workspace_id"],
    )
    _create_index_if_missing(
        existing_indexes,
        "ix_k8s_connector_is_active",
        "kubernetes_connector_config",
        ["is_active"],
    )
    _create_index_if_missing(
        existing_indexes,
        "ix_k8s_connector_visible_default",
        "kubernetes_connector_config",
        ["visible_to_default_assistant"],
    )

    if "victoriametrics_connector_config" not in tables:
        op.create_table(
            "victoriametrics_connector_config",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(
                "visible_to_default_assistant",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "provider",
                sa.String(length=64),
                nullable=False,
                server_default="victoriametrics",
            ),
            sa.Column("base_url", sa.String(length=1024), nullable=False),
            sa.Column("bearer_token", sa.String(length=4096), nullable=True),
            sa.Column("bearer_token_secret_ref", sa.String(length=512), nullable=True),
            sa.Column("use_when", sa.Text(), nullable=False),
            sa.Column("auth_type", sa.String(length=32), nullable=False, server_default="none"),
            sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(
                "query_path",
                sa.String(length=255),
                nullable=False,
                server_default="/prometheus/api/v1/query",
            ),
            sa.Column(
                "query_range_path",
                sa.String(length=255),
                nullable=False,
                server_default="/prometheus/api/v1/query_range",
            ),
            sa.Column("templates", sa.JSON(), nullable=False),
            sa.Column("timeout_seconds", sa.Float(), nullable=False, server_default="20.0"),
            sa.Column("max_series", sa.Integer(), nullable=False, server_default="20"),
            sa.Column("max_datapoints", sa.Integer(), nullable=False, server_default="300"),
            sa.Column("extra_settings", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "workspace_id",
                "name",
                name="uq_victoriametrics_connector_workspace_name",
            ),
        )
    _create_index_if_missing(
        existing_indexes,
        "ix_vm_connector_workspace_id",
        "victoriametrics_connector_config",
        ["workspace_id"],
    )
    _create_index_if_missing(
        existing_indexes,
        "ix_vm_connector_is_active",
        "victoriametrics_connector_config",
        ["is_active"],
    )
    _create_index_if_missing(
        existing_indexes,
        "ix_vm_connector_visible_default",
        "victoriametrics_connector_config",
        ["visible_to_default_assistant"],
    )

    agent_columns = (
        {column["name"] for column in inspector.get_columns("agent_profile")}
        if "agent_profile" in tables
        else set()
    )
    if "agent_profile" in tables and "allowed_kubernetes_connector_names" not in agent_columns:
        op.add_column(
            "agent_profile",
            sa.Column(
                "allowed_kubernetes_connector_names",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )
    if "agent_profile" in tables and "allowed_victoriametrics_connector_names" not in agent_columns:
        op.add_column(
            "agent_profile",
            sa.Column(
                "allowed_victoriametrics_connector_names",
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
        if "allowed_victoriametrics_connector_names" in columns:
            op.drop_column("agent_profile", "allowed_victoriametrics_connector_names")
        if "allowed_kubernetes_connector_names" in columns:
            op.drop_column("agent_profile", "allowed_kubernetes_connector_names")

    if "victoriametrics_connector_config" in tables:
        _drop_index_if_exists(inspector, "ix_vm_connector_visible_default", "victoriametrics_connector_config")
        _drop_index_if_exists(inspector, "ix_vm_connector_is_active", "victoriametrics_connector_config")
        _drop_index_if_exists(inspector, "ix_vm_connector_workspace_id", "victoriametrics_connector_config")
        _drop_index_if_exists(inspector, "ix_victoriametrics_connector_config_visible_to_default__b84f", "victoriametrics_connector_config")
        _drop_index_if_exists(inspector, "ix_victoriametrics_connector_config_is_active", "victoriametrics_connector_config")
        _drop_index_if_exists(inspector, "ix_victoriametrics_connector_config_workspace_id", "victoriametrics_connector_config")
        op.drop_table("victoriametrics_connector_config")

    if "kubernetes_connector_config" in tables:
        _drop_index_if_exists(inspector, "ix_k8s_connector_visible_default", "kubernetes_connector_config")
        _drop_index_if_exists(inspector, "ix_k8s_connector_is_active", "kubernetes_connector_config")
        _drop_index_if_exists(inspector, "ix_k8s_connector_workspace_id", "kubernetes_connector_config")
        _drop_index_if_exists(inspector, "ix_kubernetes_connector_config_visible_to_default_assistant", "kubernetes_connector_config")
        _drop_index_if_exists(inspector, "ix_kubernetes_connector_config_is_active", "kubernetes_connector_config")
        _drop_index_if_exists(inspector, "ix_kubernetes_connector_config_workspace_id", "kubernetes_connector_config")
        op.drop_table("kubernetes_connector_config")


def _create_index_if_missing(
    existing_indexes: dict[str, set[str]],
    index_name: str,
    table_name: str,
    columns: list[str],
) -> None:
    if index_name in existing_indexes.get(table_name, set()):
        return
    op.create_index(index_name, table_name, columns, unique=False)
    existing_indexes.setdefault(table_name, set()).add(index_name)


def _drop_index_if_exists(inspector: sa.Inspector, index_name: str, table_name: str) -> None:
    index_names = {index["name"] for index in inspector.get_indexes(table_name)}
    if index_name in index_names:
        op.drop_index(index_name, table_name=table_name)
