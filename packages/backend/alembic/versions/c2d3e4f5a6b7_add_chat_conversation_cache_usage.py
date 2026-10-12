"""Add cumulative prompt-cache usage to chat conversations."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | Sequence[str] | None = "a0b1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("chat_conversation") as batch_op:
        batch_op.add_column(
            sa.Column(
                "llm_cached_input_tokens",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "llm_cache_write_input_tokens",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "llm_cache_usage_reported_calls",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.create_check_constraint(
            "ck_chat_conv_cached_input_nonneg",
            "llm_cached_input_tokens >= 0",
        )
        batch_op.create_check_constraint(
            "ck_chat_conv_cache_write_nonneg",
            "llm_cache_write_input_tokens >= 0",
        )
        batch_op.create_check_constraint(
            "ck_chat_conv_cache_reported_nonneg",
            "llm_cache_usage_reported_calls >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("chat_conversation") as batch_op:
        batch_op.drop_constraint(
            "ck_chat_conv_cache_reported_nonneg",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_chat_conv_cache_write_nonneg",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_chat_conv_cached_input_nonneg",
            type_="check",
        )
        batch_op.drop_column("llm_cache_usage_reported_calls")
        batch_op.drop_column("llm_cache_write_input_tokens")
        batch_op.drop_column("llm_cached_input_tokens")
