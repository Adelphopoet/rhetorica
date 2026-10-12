"""add mattermost ambient listening

Revision ID: c1d2e3f4a5b6
Revises: b2c3d4e5f6a7
Create Date: 2026-07-01 16:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    columns = {
        column["name"]
        for column in inspector.get_columns("mattermost_connector_config")
    }

    if "ambient_listen_enabled" not in columns:
        op.add_column(
            "mattermost_connector_config",
            sa.Column(
                "ambient_listen_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if "ambient_channel_ids" not in columns:
        op.add_column(
            "mattermost_connector_config",
            sa.Column(
                "ambient_channel_ids",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            ),
        )
    if "ambient_decision_prompt" not in columns:
        op.add_column(
            "mattermost_connector_config",
            sa.Column("ambient_decision_prompt", sa.Text(), nullable=True),
        )

    if "mattermost_event_ledger" not in tables:
        op.create_table(
            "mattermost_event_ledger",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=128), nullable=False),
            sa.Column("connector_id", sa.String(length=36), nullable=False),
            sa.Column("channel_id", sa.String(length=64), nullable=False),
            sa.Column("post_id", sa.String(length=64), nullable=False),
            sa.Column("trigger", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("conversation_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "connector_id",
                "post_id",
                name="uq_mattermost_event_connector_post",
            ),
        )
        op.create_index(
            "ix_mattermost_event_ledger_workspace_id",
            "mattermost_event_ledger",
            ["workspace_id"],
            unique=False,
        )
        op.create_index(
            "ix_mattermost_event_ledger_connector_id",
            "mattermost_event_ledger",
            ["connector_id"],
            unique=False,
        )
        op.create_index(
            "ix_mattermost_event_ledger_channel_id",
            "mattermost_event_ledger",
            ["channel_id"],
            unique=False,
        )
        op.create_index(
            "ix_mattermost_event_ledger_post_id",
            "mattermost_event_ledger",
            ["post_id"],
            unique=False,
        )
        op.create_index(
            "ix_mattermost_event_ledger_trigger",
            "mattermost_event_ledger",
            ["trigger"],
            unique=False,
        )
        op.create_index(
            "ix_mattermost_event_ledger_status",
            "mattermost_event_ledger",
            ["status"],
            unique=False,
        )
        op.create_index(
            "ix_mattermost_event_ledger_conversation_id",
            "mattermost_event_ledger",
            ["conversation_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "mattermost_event_ledger" in tables:
        op.drop_index(
            "ix_mattermost_event_ledger_conversation_id",
            table_name="mattermost_event_ledger",
        )
        op.drop_index(
            "ix_mattermost_event_ledger_status",
            table_name="mattermost_event_ledger",
        )
        op.drop_index(
            "ix_mattermost_event_ledger_trigger",
            table_name="mattermost_event_ledger",
        )
        op.drop_index(
            "ix_mattermost_event_ledger_post_id",
            table_name="mattermost_event_ledger",
        )
        op.drop_index(
            "ix_mattermost_event_ledger_channel_id",
            table_name="mattermost_event_ledger",
        )
        op.drop_index(
            "ix_mattermost_event_ledger_connector_id",
            table_name="mattermost_event_ledger",
        )
        op.drop_index(
            "ix_mattermost_event_ledger_workspace_id",
            table_name="mattermost_event_ledger",
        )
        op.drop_table("mattermost_event_ledger")

    columns = {
        column["name"]
        for column in inspector.get_columns("mattermost_connector_config")
    }
    for column_name in (
        "ambient_decision_prompt",
        "ambient_channel_ids",
        "ambient_listen_enabled",
    ):
        if column_name in columns:
            op.drop_column("mattermost_connector_config", column_name)
