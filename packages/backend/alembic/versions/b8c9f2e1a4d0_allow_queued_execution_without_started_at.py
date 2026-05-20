"""allow queued execution without started_at

Revision ID: b8c9f2e1a4d0
Revises: a12d5c6e7f89
Create Date: 2026-04-02 13:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8c9f2e1a4d0"
down_revision: str | None = "a12d5c6e7f89"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("query_execution_history", schema=None) as batch_op:
        batch_op.alter_column("started_at", existing_type=sa.DateTime(timezone=True), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("query_execution_history", schema=None) as batch_op:
        batch_op.alter_column("started_at", existing_type=sa.DateTime(timezone=True), nullable=False)
