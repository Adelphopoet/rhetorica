"""add agent catalog

Revision ID: a2b4c6d8e0f2
Revises: 6b7c8d9e0f1a
Create Date: 2026-05-22 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a2b4c6d8e0f2"
down_revision: str | None = "6b7c8d9e0f1a"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "agent_profile",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("admin_prompt", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("llm_connector_name", sa.String(length=128), nullable=True),
        sa.Column("allowed_database_connector_names", sa.JSON(), nullable=False),
        sa.Column("allowed_metadata_connector_names", sa.JSON(), nullable=False),
        sa.Column("allowed_rag_connector_names", sa.JSON(), nullable=False),
        sa.Column("allowed_confluence_connector_names", sa.JSON(), nullable=False),
        sa.Column("allowed_jira_connector_names", sa.JSON(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["user_account.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["user_account.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_agent_profile_workspace_slug"),
    )
    op.create_index("ix_agent_profile_workspace_id", "agent_profile", ["workspace_id"])
    op.create_index("ix_agent_profile_is_active", "agent_profile", ["is_active"])

    op.create_table(
        "agent_star",
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_profile.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id", "user_id"),
        sa.UniqueConstraint("agent_id", "user_id", name="uq_agent_star_agent_user"),
    )
    op.create_table(
        "agent_favorite",
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_profile.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id", "user_id"),
        sa.UniqueConstraint("agent_id", "user_id", name="uq_agent_favorite_agent_user"),
    )

    with op.batch_alter_table("chat_conversation") as batch_op:
        batch_op.add_column(sa.Column("agent_id", sa.String(length=36), nullable=True))
        batch_op.create_index("ix_chat_conversation_agent_id", ["agent_id"])
        batch_op.create_foreign_key(
            "fk_chat_conversation_agent_id",
            "agent_profile",
            ["agent_id"],
            ["id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table("chat_message") as batch_op:
        batch_op.add_column(sa.Column("agent_id", sa.String(length=36), nullable=True))
        batch_op.create_index("ix_chat_message_agent_id", ["agent_id"])
        batch_op.create_foreign_key(
            "fk_chat_message_agent_id",
            "agent_profile",
            ["agent_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("chat_message") as batch_op:
        batch_op.drop_constraint("fk_chat_message_agent_id", type_="foreignkey")
        batch_op.drop_index("ix_chat_message_agent_id")
        batch_op.drop_column("agent_id")
    with op.batch_alter_table("chat_conversation") as batch_op:
        batch_op.drop_constraint("fk_chat_conversation_agent_id", type_="foreignkey")
        batch_op.drop_index("ix_chat_conversation_agent_id")
        batch_op.drop_column("agent_id")

    op.drop_table("agent_favorite")
    op.drop_table("agent_star")
    op.drop_index("ix_agent_profile_is_active", table_name="agent_profile")
    op.drop_index("ix_agent_profile_workspace_id", table_name="agent_profile")
    op.drop_table("agent_profile")
