"""add mattermost connector config

Revision ID: f3a4b5c6d7e8
Revises: 2054007cd373
Create Date: 2026-05-08 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "2054007cd373"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "mattermost_connector_config" not in tables:
        op.create_table(
            "mattermost_connector_config",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("provider", sa.String(length=64), nullable=False, server_default="mattermost"),
            sa.Column("base_url", sa.String(length=1024), nullable=False),
            sa.Column("bot_token", sa.String(length=4096), nullable=True),
            sa.Column("bot_token_secret_ref", sa.String(length=512), nullable=True),
            sa.Column("bot_username", sa.String(length=255), nullable=True),
            sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("listen_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("extra_settings", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "workspace_id",
                "name",
                name="uq_mattermost_connector_workspace_name",
            ),
        )
        op.create_index(
            "ix_mattermost_connector_config_workspace_id",
            "mattermost_connector_config",
            ["workspace_id"],
            unique=False,
        )

    if "mattermost_thread_binding" not in tables:
        op.create_table(
            "mattermost_thread_binding",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=128), nullable=False),
            sa.Column("connector_id", sa.String(length=36), nullable=False),
            sa.Column("channel_id", sa.String(length=64), nullable=False),
            sa.Column("thread_key", sa.String(length=64), nullable=False),
            sa.Column("conversation_id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "connector_id",
                "channel_id",
                "thread_key",
                name="uq_mattermost_binding_scope",
            ),
        )
        op.create_index(
            "ix_mattermost_thread_binding_workspace_id",
            "mattermost_thread_binding",
            ["workspace_id"],
            unique=False,
        )
        op.create_index(
            "ix_mattermost_thread_binding_connector_id",
            "mattermost_thread_binding",
            ["connector_id"],
            unique=False,
        )
        op.create_index(
            "ix_mattermost_thread_binding_channel_id",
            "mattermost_thread_binding",
            ["channel_id"],
            unique=False,
        )
        op.create_index(
            "ix_mattermost_thread_binding_thread_key",
            "mattermost_thread_binding",
            ["thread_key"],
            unique=False,
        )
        op.create_index(
            "ix_mattermost_thread_binding_conversation_id",
            "mattermost_thread_binding",
            ["conversation_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "mattermost_thread_binding" in tables:
        op.drop_index("ix_mattermost_thread_binding_conversation_id", table_name="mattermost_thread_binding")
        op.drop_index("ix_mattermost_thread_binding_thread_key", table_name="mattermost_thread_binding")
        op.drop_index("ix_mattermost_thread_binding_channel_id", table_name="mattermost_thread_binding")
        op.drop_index("ix_mattermost_thread_binding_connector_id", table_name="mattermost_thread_binding")
        op.drop_index("ix_mattermost_thread_binding_workspace_id", table_name="mattermost_thread_binding")
        op.drop_table("mattermost_thread_binding")

    if "mattermost_connector_config" in tables:
        op.drop_index("ix_mattermost_connector_config_workspace_id", table_name="mattermost_connector_config")
        op.drop_table("mattermost_connector_config")
