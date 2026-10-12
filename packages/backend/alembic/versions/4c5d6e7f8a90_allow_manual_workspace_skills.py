"""allow manual workspace skills

Revision ID: 4c5d6e7f8a90
Revises: 1f2e3d4c5b6a
Create Date: 2026-05-26 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c5d6e7f8a90"
down_revision: str | Sequence[str] | None = "1f2e3d4c5b6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workspace_skill", schema=None) as batch_op:
        batch_op.alter_column(
            "source_url",
            existing_type=sa.String(length=1024),
            nullable=True,
        )


def downgrade() -> None:
    op.execute("UPDATE workspace_skill SET source_url = '' WHERE source_url IS NULL")
    with op.batch_alter_table("workspace_skill", schema=None) as batch_op:
        batch_op.alter_column(
            "source_url",
            existing_type=sa.String(length=1024),
            nullable=False,
        )
