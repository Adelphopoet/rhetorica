"""remove legacy connector stacks

Revision ID: 5a7c9e1f3b2d
Revises: 4f6a8c0e2b1d
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5a7c9e1f3b2d"
down_revision: str | Sequence[str] | None = "4f6a8c0e2b1d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_TABLES = (
    "metadata_table_usage",
    "openmetadata_connector_config",
    "confluence_connector_config",
    "jira_connector_config",
)

_LEGACY_AGENT_COLUMNS = (
    "allowed_metadata_connector_names",
    "allowed_confluence_connector_names",
    "allowed_jira_connector_names",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "agent_profile" in tables:
        columns = {column["name"] for column in inspector.get_columns("agent_profile")}
        with op.batch_alter_table("agent_profile", schema=None) as batch_op:
            for column_name in _LEGACY_AGENT_COLUMNS:
                if column_name in columns:
                    batch_op.drop_column(column_name)

    for table_name in _LEGACY_TABLES:
        if table_name in tables:
            op.drop_table(table_name)

    if "connector_config" in tables:
        if bind.dialect.name == "postgresql":
            op.execute(
                """
                UPDATE connector_config
                SET extra_settings = (
                    extra_settings::jsonb
                    - 'metadata_service'
                    - 'metadata_database'
                    - 'metadata_schema'
                )::json
                WHERE extra_settings::jsonb ?| ARRAY[
                    'metadata_service',
                    'metadata_database',
                    'metadata_schema'
                ]
                """
            )
        elif bind.dialect.name == "sqlite":
            op.execute(
                """
                UPDATE connector_config
                SET extra_settings = json_remove(
                    extra_settings,
                    '$.metadata_service',
                    '$.metadata_database',
                    '$.metadata_schema'
                )
                WHERE json_valid(extra_settings)
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "openmetadata_connector_config" not in tables:
        op.create_table(
            "openmetadata_connector_config",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("workspace_id", sa.String(128), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("provider", sa.String(64), nullable=False, server_default="openmetadata"),
            sa.Column("base_url", sa.String(1024), nullable=False),
            sa.Column("api_token", sa.String(4096), nullable=True),
            sa.Column("api_token_secret_ref", sa.String(512), nullable=True),
            sa.Column("usage_description", sa.Text(), nullable=True),
            sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("mcp_tables_url", sa.String(1024), nullable=True),
            sa.Column("service_name", sa.String(255), nullable=True),
            sa.Column("database_name", sa.String(255), nullable=True),
            sa.Column("schema_name", sa.String(255), nullable=True),
            sa.Column("table_limit", sa.Integer(), nullable=False, server_default="200"),
            sa.Column(
                "fields",
                sa.String(255),
                nullable=False,
                server_default="columns,database,databaseSchema",
            ),
            sa.Column("extra_settings", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "workspace_id",
                "name",
                name="uq_openmetadata_connector_workspace_name",
            ),
        )
        op.create_index(
            "ix_openmetadata_connector_config_workspace_id",
            "openmetadata_connector_config",
            ["workspace_id"],
        )
        op.create_index(
            "ix_openmetadata_connector_config_is_active",
            "openmetadata_connector_config",
            ["is_active"],
        )

    for table_name, provider, scope_column, limit_column in (
        ("confluence_connector_config", "confluence", "space_key", "page_limit"),
        ("jira_connector_config", "jira", "project_keys", "issue_limit"),
    ):
        if table_name in tables:
            continue
        scope_type = sa.JSON() if scope_column == "project_keys" else sa.String(64)
        op.create_table(
            table_name,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("workspace_id", sa.String(128), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("provider", sa.String(64), nullable=False, server_default=provider),
            sa.Column("base_url", sa.String(1024), nullable=False),
            sa.Column("username", sa.String(255), nullable=True),
            sa.Column("api_token", sa.String(4096), nullable=True),
            sa.Column("api_token_secret_ref", sa.String(512), nullable=True),
            sa.Column("usage_description", sa.Text(), nullable=True),
            sa.Column("auth_type", sa.String(32), nullable=False, server_default="bearer"),
            sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(scope_column, scope_type, nullable=True),
            sa.Column(limit_column, sa.Integer(), nullable=False, server_default="10"),
            sa.Column("extra_settings", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "workspace_id",
                "name",
                name=f"uq_{provider}_connector_workspace_name",
            ),
        )
        op.create_index(
            f"ix_{table_name}_workspace_id",
            table_name,
            ["workspace_id"],
        )
        op.create_index(f"ix_{table_name}_is_active", table_name, ["is_active"])

    if "metadata_table_usage" not in tables:
        op.create_table(
            "metadata_table_usage",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("workspace_id", sa.String(128), nullable=False),
            sa.Column("connector_name", sa.String(128), nullable=False),
            sa.Column("table_fqn", sa.String(1024), nullable=False),
            sa.Column("positive_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("negative_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "workspace_id",
                "connector_name",
                "table_fqn",
                name="uq_metadata_table_usage_scope_table",
            ),
        )

    columns = {column["name"] for column in sa.inspect(bind).get_columns("agent_profile")}
    with op.batch_alter_table("agent_profile", schema=None) as batch_op:
        for column_name in _LEGACY_AGENT_COLUMNS:
            if column_name not in columns:
                batch_op.add_column(
                    sa.Column(
                        column_name,
                        sa.JSON(),
                        nullable=False,
                        server_default=sa.text("'[]'"),
                    )
                )
