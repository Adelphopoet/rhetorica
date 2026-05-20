"""add conversation state

Revision ID: e5a6b7c8d9f0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-25 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e5a6b7c8d9f0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("chat_conversation")}
    if "conversation_state" not in columns:
        with op.batch_alter_table("chat_conversation", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "conversation_state",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'{}'"),
                )
            )
        op.alter_column("chat_conversation", "conversation_state", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("chat_conversation")}
    if "conversation_state" in columns:
        with op.batch_alter_table("chat_conversation", schema=None) as batch_op:
            batch_op.drop_column("conversation_state")
