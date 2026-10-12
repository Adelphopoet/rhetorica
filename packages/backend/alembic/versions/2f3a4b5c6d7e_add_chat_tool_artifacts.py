"""add conversation-scoped temporary tool artifacts

Revision ID: 2f3a4b5c6d7e
Revises: 1e2f3a4b5c6d
Create Date: 2026-07-20 11:15:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2f3a4b5c6d7e"
down_revision: str | Sequence[str] | None = "1e2f3a4b5c6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_tool_artifact",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=512), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_char_count", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat_conversation.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "artifact_id",
            name="uq_chat_tool_artifact_conversation_artifact",
        ),
    )
    op.create_index(
        "ix_chat_tool_artifact_conversation_id",
        "chat_tool_artifact",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_tool_artifact_expires_at",
        "chat_tool_artifact",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_tool_artifact_expires_at", table_name="chat_tool_artifact")
    op.drop_index("ix_chat_tool_artifact_conversation_id", table_name="chat_tool_artifact")
    op.drop_table("chat_tool_artifact")
