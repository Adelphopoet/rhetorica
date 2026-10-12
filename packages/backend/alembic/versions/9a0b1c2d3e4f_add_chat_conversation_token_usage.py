"""add chat conversation token usage

Revision ID: 9a0b1c2d3e4f
Revises: 8f901a2b3c4d
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a0b1c2d3e4f"
down_revision: str | Sequence[str] | None = "8f901a2b3c4d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("chat_conversation") as batch_op:
        batch_op.add_column(
            sa.Column(
                "llm_input_tokens",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "llm_output_tokens",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.create_check_constraint(
            "ck_chat_conversation_llm_input_tokens_non_negative",
            "llm_input_tokens >= 0",
        )
        batch_op.create_check_constraint(
            "ck_chat_conversation_llm_output_tokens_non_negative",
            "llm_output_tokens >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("chat_conversation") as batch_op:
        batch_op.drop_constraint(
            "ck_chat_conversation_llm_output_tokens_non_negative",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_chat_conversation_llm_input_tokens_non_negative",
            type_="check",
        )
        batch_op.drop_column("llm_output_tokens")
        batch_op.drop_column("llm_input_tokens")
