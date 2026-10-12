"""add agent mcp allowlist

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-07-02 14:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_profile",
        sa.Column(
            "allowed_mcp_connector_names",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    with op.batch_alter_table("agent_profile") as batch_op:
        batch_op.alter_column("allowed_mcp_connector_names", server_default=None)


def downgrade() -> None:
    op.drop_column("agent_profile", "allowed_mcp_connector_names")
