"""add mcp connectors

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-07-02 12:00:00.000000

"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: str | Sequence[str] | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ATLASSIAN_MCP_URL = "https://atlassian.example.com/mcp"


def upgrade() -> None:
    op.create_table(
        "mcp_connector_config",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="generic"),
        sa.Column(
            "transport",
            sa.String(length=32),
            nullable=False,
            server_default="streamable-http",
        ),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("headers", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("secret_header_refs", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("enabled_groups", sa.String(length=512), nullable=True),
        sa.Column("timeout_seconds", sa.Float(), nullable=False, server_default="30.0"),
        sa.Column("tool_name_prefix", sa.String(length=64), nullable=True),
        sa.Column("llm_connector_name", sa.String(length=128), nullable=True),
        sa.Column("usage_description", sa.Text(), nullable=True),
        sa.Column("last_sync_status", sa.String(length=32), nullable=True),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extra_settings", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_mcp_connector_workspace_name"),
    )
    op.create_index("ix_mcp_connector_workspace_id", "mcp_connector_config", ["workspace_id"])
    op.create_index("ix_mcp_connector_is_active", "mcp_connector_config", ["is_active"])

    op.create_table(
        "mcp_tool_catalog",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("connector_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("connector_name", sa.String(length=128), nullable=False),
        sa.Column("remote_tool_name", sa.String(length=255), nullable=False),
        sa.Column("exposed_tool_name", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("input_schema", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["connector_id"], ["mcp_connector_config.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "exposed_tool_name",
            name="uq_mcp_tool_workspace_exposed_name",
        ),
        sa.UniqueConstraint(
            "connector_id",
            "remote_tool_name",
            name="uq_mcp_tool_connector_remote_name",
        ),
    )
    op.create_index("ix_mcp_tool_workspace_id", "mcp_tool_catalog", ["workspace_id"])
    op.create_index("ix_mcp_tool_connector_id", "mcp_tool_catalog", ["connector_id"])
    op.create_index("ix_mcp_tool_is_active", "mcp_tool_catalog", ["is_active"])

    _seed_local_mcp_connectors()


def downgrade() -> None:
    op.drop_index("ix_mcp_tool_is_active", table_name="mcp_tool_catalog")
    op.drop_index("ix_mcp_tool_connector_id", table_name="mcp_tool_catalog")
    op.drop_index("ix_mcp_tool_workspace_id", table_name="mcp_tool_catalog")
    op.drop_table("mcp_tool_catalog")
    op.drop_index("ix_mcp_connector_is_active", table_name="mcp_connector_config")
    op.drop_index("ix_mcp_connector_workspace_id", table_name="mcp_connector_config")
    op.drop_table("mcp_connector_config")


def _seed_local_mcp_connectors() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if not {"jira_connector_config", "confluence_connector_config"} <= tables:
        return

    llm_connector_name = bind.execute(
        sa.text(
            """
            SELECT name
            FROM llm_connector_config
            WHERE workspace_id = 'default' AND name = 'example-llm' AND is_active IS TRUE
            LIMIT 1
            """
        )
    ).scalar_one_or_none()
    now = datetime.now(tz=UTC)

    jira_row = bind.execute(
        sa.text(
            """
            SELECT id, workspace_id, base_url, api_token_secret_ref
            FROM jira_connector_config
            WHERE workspace_id = 'default' AND name = 'jira-omni'
            LIMIT 1
            """
        )
    ).mappings().first()
    if jira_row and jira_row["api_token_secret_ref"]:
        _insert_mcp_seed(
            bind=bind,
            now=now,
            workspace_id=jira_row["workspace_id"],
            name="mcp-jira-omni",
            provider="jira",
            url=ATLASSIAN_MCP_URL,
            verify_tls=False,
            headers={
                "X-Atlassian-Jira-Url": jira_row["base_url"],
                "Enabled-Groups": "jira_read,jira_write",
            },
            secret_header_refs={
                "X-Atlassian-Jira-Personal-Token": {
                    "secret_ref": jira_row["api_token_secret_ref"],
                    "mode": "raw",
                }
            },
            enabled_groups="jira_read,jira_write",
            tool_name_prefix="mcp_jira_omni",
            llm_connector_name=llm_connector_name,
            usage_description="Jira MCP read/write connector seeded from jira-omni.",
        )

    confluence_row = bind.execute(
        sa.text(
            """
            SELECT id, workspace_id, base_url, api_token_secret_ref
            FROM confluence_connector_config
            WHERE workspace_id = 'default' AND name = 'confluence-main'
            LIMIT 1
            """
        )
    ).mappings().first()
    if confluence_row and confluence_row["api_token_secret_ref"]:
        _insert_mcp_seed(
            bind=bind,
            now=now,
            workspace_id=confluence_row["workspace_id"],
            name="mcp-confluence-main",
            provider="confluence",
            url=ATLASSIAN_MCP_URL,
            verify_tls=False,
            headers={
                "X-Atlassian-Confluence-Url": confluence_row["base_url"],
                "Enabled-Groups": "confluence_read",
            },
            secret_header_refs={
                "X-Atlassian-Confluence-Personal-Token": {
                    "secret_ref": confluence_row["api_token_secret_ref"],
                    "mode": "raw",
                }
            },
            enabled_groups="confluence_read",
            tool_name_prefix="mcp_confluence_main",
            llm_connector_name=llm_connector_name,
            usage_description="Confluence MCP read-only connector seeded from confluence-main.",
        )

def _insert_mcp_seed(
    *,
    bind: sa.Connection,
    now: datetime,
    workspace_id: str,
    name: str,
    provider: str,
    url: str,
    verify_tls: bool,
    headers: dict[str, str],
    secret_header_refs: dict[str, dict[str, str]],
    enabled_groups: str,
    tool_name_prefix: str,
    llm_connector_name: str | None,
    usage_description: str,
) -> None:
    exists = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM mcp_connector_config
            WHERE workspace_id = :workspace_id AND name = :name
            LIMIT 1
            """
        ),
        {"workspace_id": workspace_id, "name": name},
    ).scalar_one_or_none()
    if exists:
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO mcp_connector_config (
                id, workspace_id, name, is_active, provider, transport, url, verify_tls,
                headers, secret_header_refs, enabled_groups, timeout_seconds,
                tool_name_prefix, llm_connector_name, usage_description,
                last_sync_status, extra_settings, created_at, updated_at
            ) VALUES (
                :id, :workspace_id, :name, true, :provider, 'streamable-http', :url, :verify_tls,
                CAST(:headers AS JSON), CAST(:secret_header_refs AS JSON), :enabled_groups, 30.0,
                :tool_name_prefix, :llm_connector_name, :usage_description,
                'pending', CAST(:extra_settings AS JSON), :created_at, :updated_at
            )
            """
        ),
        {
            "id": str(uuid4()),
            "workspace_id": workspace_id,
            "name": name,
            "provider": provider,
            "url": url,
            "verify_tls": verify_tls,
            "headers": json.dumps(headers, separators=(",", ":")),
            "secret_header_refs": json.dumps(secret_header_refs, separators=(",", ":")),
            "enabled_groups": enabled_groups,
            "tool_name_prefix": tool_name_prefix,
            "llm_connector_name": llm_connector_name,
            "usage_description": usage_description,
            "extra_settings": "{}",
            "created_at": now,
            "updated_at": now,
        },
    )
