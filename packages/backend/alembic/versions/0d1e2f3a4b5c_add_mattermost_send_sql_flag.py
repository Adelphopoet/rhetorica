"""add mattermost send sql flag

Revision ID: 0d1e2f3a4b5c
Revises: e3f4a5b6c7d8
Create Date: 2026-07-15 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0d1e2f3a4b5c"
down_revision: str | Sequence[str] | None = "e3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mattermost_connector_config",
        sa.Column(
            "send_sql_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    with op.batch_alter_table("mattermost_connector_config") as batch_op:
        batch_op.alter_column("send_sql_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("mattermost_connector_config", "send_sql_enabled")
