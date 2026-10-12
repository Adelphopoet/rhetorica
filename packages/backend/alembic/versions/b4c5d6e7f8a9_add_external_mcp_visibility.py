"""add external MCP connector visibility

Revision ID: b4c5d6e7f8a9
Revises: a2b4c6d8e0f1
Create Date: 2026-09-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4c5d6e7f8a9"
down_revision: str | Sequence[str] | None = "a2b4c6d8e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("mcp_connector_config") as batch_op:
        batch_op.add_column(
            sa.Column(
                "visible_to_external_mcp",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
    with op.batch_alter_table("mcp_connector_config") as batch_op:
        batch_op.alter_column(
            "visible_to_external_mcp",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.false(),
        )


def downgrade() -> None:
    with op.batch_alter_table("mcp_connector_config") as batch_op:
        batch_op.drop_column("visible_to_external_mcp")
