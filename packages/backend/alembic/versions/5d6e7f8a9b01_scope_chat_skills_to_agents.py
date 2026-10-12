"""scope chat skills to agents

Revision ID: 5d6e7f8a9b01
Revises: 4c5d6e7f8a90
Create Date: 2026-05-26 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d6e7f8a9b01"
down_revision: str | Sequence[str] | None = "4c5d6e7f8a90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workspace_skill", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "visible_to_default_assistant",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
    with op.batch_alter_table("agent_profile", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "allowed_workspace_skill_ids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
    with op.batch_alter_table("workspace_skill", schema=None) as batch_op:
        batch_op.alter_column("visible_to_default_assistant", server_default=None)
    with op.batch_alter_table("agent_profile", schema=None) as batch_op:
        batch_op.alter_column("allowed_workspace_skill_ids", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("agent_profile", schema=None) as batch_op:
        batch_op.drop_column("allowed_workspace_skill_ids")
    with op.batch_alter_table("workspace_skill", schema=None) as batch_op:
        batch_op.drop_column("visible_to_default_assistant")
