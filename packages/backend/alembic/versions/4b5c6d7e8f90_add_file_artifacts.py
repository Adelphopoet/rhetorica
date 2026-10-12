"""add immutable file artifacts and message associations

Revision ID: 4b5c6d7e8f90
Revises: 3a4b5c6d7e8f
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4b5c6d7e8f90"
down_revision: str | Sequence[str] | None = "3a4b5c6d7e8f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "file_artifact",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_artifact_ids", sa.JSON(), nullable=False),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["chat_conversation.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_account.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_file_artifact_owner_status", "file_artifact", ["owner_user_id", "status"])
    op.create_index("ix_file_artifact_workspace_status", "file_artifact", ["workspace_id", "status"])
    op.create_index("ix_file_artifact_conversation_created", "file_artifact", ["conversation_id", "created_at"])
    op.create_table(
        "chat_message_file",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("relation", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["file_artifact.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["chat_message.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", "artifact_id", name="uq_chat_message_file_link"),
        sa.UniqueConstraint("message_id", "relation", "position", name="uq_chat_message_file_order"),
    )
    op.create_index("ix_chat_message_file_artifact", "chat_message_file", ["artifact_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_message_file_artifact", table_name="chat_message_file")
    op.drop_table("chat_message_file")
    op.drop_index("ix_file_artifact_conversation_created", table_name="file_artifact")
    op.drop_index("ix_file_artifact_workspace_status", table_name="file_artifact")
    op.drop_index("ix_file_artifact_owner_status", table_name="file_artifact")
    op.drop_table("file_artifact")
