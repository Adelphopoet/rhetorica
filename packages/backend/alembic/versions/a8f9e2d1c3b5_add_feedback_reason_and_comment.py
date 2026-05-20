"""add feedback reason and comment fields to chat_message

Revision ID: a8f9e2d1c3b5
Revises: a12d5c6e7f89
Create Date: 2026-05-05 12:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8f9e2d1c3b5"
down_revision: str | None = "a12d5c6e7f89"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.add_column(sa.Column("feedback_reason", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("feedback_comment", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.drop_column("feedback_comment")
        batch_op.drop_column("feedback_reason")
