"""Add selectable Chat Completions token-limit parameter."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: str | Sequence[str] | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("llm_connector_config") as batch_op:
        batch_op.add_column(
            sa.Column(
                "max_tokens_parameter",
                sa.String(length=32),
                nullable=False,
                server_default="max_tokens",
            )
        )
        batch_op.create_check_constraint(
            "ck_llm_connector_max_tokens_parameter",
            "max_tokens_parameter IN ('max_tokens', 'max_completion_tokens')",
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_connector_config") as batch_op:
        batch_op.drop_constraint(
            "ck_llm_connector_max_tokens_parameter",
            type_="check",
        )
        batch_op.drop_column("max_tokens_parameter")
