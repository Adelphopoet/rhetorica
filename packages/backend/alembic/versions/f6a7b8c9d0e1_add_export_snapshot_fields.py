"""add export snapshot fields

Revision ID: f6a7b8c9d0e1
Revises: b8c9f2e1a4d0
Create Date: 2026-04-04 13:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e4f1c2b3a4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("query_execution_history", schema=None) as batch_op:
        batch_op.add_column(sa.Column("preview_payload", sa.JSON(), nullable=True))

    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.add_column(sa.Column("visualization_spec", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.drop_column("visualization_spec")

    with op.batch_alter_table("query_execution_history", schema=None) as batch_op:
        batch_op.drop_column("preview_payload")
