"""add workspace skill content bundle

Revision ID: f1e2d3c4b5a6
Revises: c7f9a1b2d3e4
Create Date: 2026-04-08 16:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1e2d3c4b5a6"
down_revision: str | None = "c7f9a1b2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("workspace_skill")}

    if "content_bundle" in columns:
        return

    with op.batch_alter_table("workspace_skill", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "content_bundle",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("workspace_skill")}

    if "content_bundle" not in columns:
        return

    with op.batch_alter_table("workspace_skill", schema=None) as batch_op:
        batch_op.drop_column("content_bundle")
