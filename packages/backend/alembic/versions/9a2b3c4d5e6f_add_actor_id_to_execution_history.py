"""add actor_id to query_execution_history

Revision ID: 9a2b3c4d5e6f
Revises: a12d5c6e7f89
Create Date: 2026-05-04 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a2b3c4d5e6f"
down_revision: str | None = "a12d5c6e7f89"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("query_execution_history", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "actor_id",
                sa.String(length=36),
                nullable=True,
            )
        )
    # Create index separately (batch operations don't support CREATE INDEX in all dialects)
    op.create_index(
        "ix_query_execution_history_actor_id",
        "query_execution_history",
        ["actor_id"],
        unique=False,
    )


def downgrade() -> None:
    with op.batch_alter_table("query_execution_history", schema=None) as batch_op:
        batch_op.drop_column("actor_id")
