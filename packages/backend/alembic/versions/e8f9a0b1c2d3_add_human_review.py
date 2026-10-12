"""add_langfuse_feedback_delivery

Revision ID: e8f9a0b1c2d3
Revises: a1b2c3d4e5f6
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8f9a0b1c2d3"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.add_column(sa.Column("feedback_what_went_wrong", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("feedback_expected_answer", sa.Text(), nullable=True))
    op.create_table(
        "chat_answer_trace",
        sa.Column("assistant_message_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("tool_call_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tool_error_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["chat_message.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["chat_conversation.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("assistant_message_id"),
        sa.UniqueConstraint("trace_id"),
    )
    op.create_index("ix_chat_answer_trace_conversation_id", "chat_answer_trace", ["conversation_id"])
    op.create_table(
        "langfuse_review_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assistant_message_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("delivery_state", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("claim_token", sa.String(length=36), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["chat_message.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assistant_message_id", "event_type", name="uq_langfuse_review_outbox_event"),
    )
    op.create_index("ix_langfuse_review_outbox_delivery", "langfuse_review_outbox", ["delivery_state", "created_at"])


def downgrade() -> None:
    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.drop_column("feedback_expected_answer")
        batch_op.drop_column("feedback_what_went_wrong")
    op.drop_index("ix_langfuse_review_outbox_delivery", table_name="langfuse_review_outbox")
    op.drop_table("langfuse_review_outbox")
    op.drop_index("ix_chat_answer_trace_conversation_id", table_name="chat_answer_trace")
    op.drop_table("chat_answer_trace")
