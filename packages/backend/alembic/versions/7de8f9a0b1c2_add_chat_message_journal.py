"""Persist the public execution journal with its assistant message.

Revision ID: 7de8f9a0b1c2
Revises: 6cd7e8f9a0b1
"""
import sqlalchemy as sa
from alembic import op

revision = "7de8f9a0b1c2"
down_revision = "6cd7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_message", sa.Column("journal_events", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_message", "journal_events")
