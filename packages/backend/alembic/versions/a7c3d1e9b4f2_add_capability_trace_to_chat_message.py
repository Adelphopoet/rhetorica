"""add capability trace to chat message

Revision ID: a7c3d1e9b4f2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-04 16:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3d1e9b4f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "capability_trace",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )

    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.alter_column("capability_trace", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.drop_column("capability_trace")
