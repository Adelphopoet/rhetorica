"""add persistent OpenAI Agents SDK sessions

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-09-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: str | Sequence[str] | None = "b4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_agent_session",
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("next_sequence", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "next_sequence >= 0",
            name="ck_chat_agent_session_next_sequence_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat_conversation.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_table(
        "chat_agent_session_item",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("item_type", sa.String(length=64), nullable=False),
        sa.Column("item_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sequence >= 0",
            name="ck_chat_agent_session_item_sequence_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat_agent_session.conversation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_chat_agent_session_item_sequence",
        ),
    )
    op.create_index(
        "ix_chat_agent_session_item_conversation_sequence",
        "chat_agent_session_item",
        ["conversation_id", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_agent_session_item_conversation_sequence",
        table_name="chat_agent_session_item",
    )
    op.drop_table("chat_agent_session_item")
    op.drop_table("chat_agent_session")
