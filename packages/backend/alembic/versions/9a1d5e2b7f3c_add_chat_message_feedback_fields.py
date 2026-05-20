"""add chat message feedback fields

Revision ID: 9a1d5e2b7f3c
Revises: c4d7fd82f4e1
Create Date: 2026-04-01 23:50:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a1d5e2b7f3c"
down_revision: str | None = "c4d7fd82f4e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.add_column(sa.Column("feedback", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("feedback_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.drop_column("feedback_updated_at")
        batch_op.drop_column("feedback")
