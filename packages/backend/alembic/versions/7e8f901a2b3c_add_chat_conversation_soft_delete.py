"""add chat conversation soft delete timestamp

Revision ID: 7e8f901a2b3c
Revises: 6d7e8f901a2b
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7e8f901a2b3c"
down_revision: str | Sequence[str] | None = "6d7e8f901a2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_conversation",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_chat_conversation_deleted_at",
        "chat_conversation",
        ["deleted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_conversation_deleted_at", table_name="chat_conversation")
    op.drop_column("chat_conversation", "deleted_at")
