"""add chat message source provenance

Revision ID: a0b1c2d3e4f5
Revises: 9a0b1c2d3e4f
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a0b1c2d3e4f5"
down_revision: str | Sequence[str] | None = "9a0b1c2d3e4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_message",
        sa.Column("source_provider", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "chat_message",
        sa.Column("source_message_id", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_chat_message_conversation_source",
        "chat_message",
        ["conversation_id", "source_provider", "source_message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_message_conversation_source",
        table_name="chat_message",
    )
    op.drop_column("chat_message", "source_message_id")
    op.drop_column("chat_message", "source_provider")
