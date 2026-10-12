"""add conversation-scoped LLM credential binding

Revision ID: a1b2c3d4e5f6
Revises: e7f8a9b0c1d2
Create Date: 2026-09-15 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("llm_connector_config", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "credential_set_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )

    op.create_table(
        "chat_llm_credential_binding",
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("llm_connector_id", sa.String(length=36), nullable=False),
        sa.Column("credential_index", sa.Integer(), nullable=False),
        sa.Column(
            "credential_set_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "credential_index >= 0",
            name="ck_chat_llm_credential_binding_index_non_negative",
        ),
        sa.CheckConstraint(
            "credential_set_version > 0",
            name="ck_chat_llm_credential_binding_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat_conversation.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["llm_connector_id"],
            ["llm_connector_config.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conversation_id", "llm_connector_id"),
    )
    op.create_index(
        "ix_chat_llm_credential_binding_connector",
        "chat_llm_credential_binding",
        ["llm_connector_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_llm_credential_binding_connector",
        table_name="chat_llm_credential_binding",
    )
    op.drop_table("chat_llm_credential_binding")
    with op.batch_alter_table("llm_connector_config", schema=None) as batch_op:
        batch_op.drop_column("credential_set_version")
