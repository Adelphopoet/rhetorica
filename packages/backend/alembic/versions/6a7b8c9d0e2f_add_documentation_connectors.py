"""add documentation connectors

Revision ID: 6a7b8c9d0e2f
Revises: 5d6e7f8a9b01
Create Date: 2026-05-26 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6a7b8c9d0e2f"
down_revision: str | Sequence[str] | None = "5d6e7f8a9b01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documentation_connector_config",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "visible_to_default_assistant",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="docsy_hugo"),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("index_url", sa.String(length=1024), nullable=True),
        sa.Column("content_format", sa.String(length=32), nullable=False, server_default="markdown"),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("api_token", sa.String(length=4096), nullable=True),
        sa.Column("api_token_secret_ref", sa.String(length=512), nullable=True),
        sa.Column("usage_description", sa.Text(), nullable=True),
        sa.Column("auth_type", sa.String(length=32), nullable=False, server_default="none"),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("include_paths", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("exclude_paths", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("sync_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sync_interval_minutes", sa.Integer(), nullable=False, server_default="1440"),
        sa.Column("qdrant_base_url", sa.String(length=1024), nullable=False),
        sa.Column("qdrant_api_key", sa.String(length=4096), nullable=True),
        sa.Column("qdrant_api_key_secret_ref", sa.String(length=512), nullable=True),
        sa.Column(
            "qdrant_collection_name",
            sa.String(length=255),
            nullable=False,
            server_default="documentation_chunks",
        ),
        sa.Column("qdrant_prefer_grpc", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("vector_size", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("embedding_base_url", sa.String(length=1024), nullable=True),
        sa.Column("embedding_api_key", sa.String(length=4096), nullable=True),
        sa.Column("embedding_api_key_secret_ref", sa.String(length=512), nullable=True),
        sa.Column(
            "embedding_model",
            sa.String(length=255),
            nullable=False,
            server_default="BAAI/bge-small-en-v1.5",
        ),
        sa.Column(
            "embedding_endpoint_path",
            sa.String(length=255),
            nullable=False,
            server_default="/v1/embeddings",
        ),
        sa.Column("embedding_timeout_seconds", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("last_sync_status", sa.String(length=32), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extra_settings", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_documentation_connector_workspace_name",
        ),
    )
    op.create_index(
        "ix_documentation_connector_config_workspace_id",
        "documentation_connector_config",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_documentation_connector_config_next_sync_at",
        "documentation_connector_config",
        ["next_sync_at"],
        unique=False,
    )
    op.create_index(
        "ix_documentation_connector_config_visible_to_default_assistant",
        "documentation_connector_config",
        ["visible_to_default_assistant"],
        unique=False,
    )

    op.create_table(
        "documentation_page",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("connector_name", sa.String(length=128), nullable=False),
        sa.Column("page_ref", sa.String(length=1024), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("source_path", sa.String(length=2048), nullable=True),
        sa.Column("markdown_url", sa.String(length=2048), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_hash", sa.String(length=128), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "connector_name",
            "page_ref",
            name="uq_documentation_page_scope_ref",
        ),
    )
    for column in ("workspace_id", "connector_name", "page_ref", "body_hash", "deleted_at"):
        op.create_index(f"ix_documentation_page_{column}", "documentation_page", [column])

    op.create_table(
        "documentation_chunk",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("connector_name", sa.String(length=128), nullable=False),
        sa.Column("page_id", sa.String(length=36), nullable=False),
        sa.Column("page_ref", sa.String(length=1024), nullable=False),
        sa.Column("chunk_ref", sa.String(length=128), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=500), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("qdrant_point_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["page_id"], ["documentation_page.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "connector_name",
            "chunk_ref",
            name="uq_documentation_chunk_scope_ref",
        ),
    )
    for column in ("workspace_id", "connector_name", "page_ref", "qdrant_point_id"):
        op.create_index(f"ix_documentation_chunk_{column}", "documentation_chunk", [column])

    op.create_table(
        "documentation_sync_run",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("connector_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("connector_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("pages_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunks_indexed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warnings", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("connector_id", "workspace_id", "connector_name", "status"):
        op.create_index(f"ix_documentation_sync_run_{column}", "documentation_sync_run", [column])

    with op.batch_alter_table("agent_profile", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "allowed_documentation_connector_names",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.alter_column("allowed_documentation_connector_names", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("agent_profile", schema=None) as batch_op:
        batch_op.drop_column("allowed_documentation_connector_names")
    op.drop_table("documentation_sync_run")
    op.drop_table("documentation_chunk")
    op.drop_table("documentation_page")
    op.drop_index(
        "ix_documentation_connector_config_visible_to_default_assistant",
        table_name="documentation_connector_config",
    )
    op.drop_index(
        "ix_documentation_connector_config_next_sync_at",
        table_name="documentation_connector_config",
    )
    op.drop_index(
        "ix_documentation_connector_config_workspace_id",
        table_name="documentation_connector_config",
    )
    op.drop_table("documentation_connector_config")
