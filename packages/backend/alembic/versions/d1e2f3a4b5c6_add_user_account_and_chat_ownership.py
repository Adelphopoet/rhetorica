"""add user account and chat ownership

Revision ID: d1e2f3a4b5c6
Revises: b8c9f2e1a4d0
Create Date: 2026-04-04 12:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

BOOTSTRAP_AUTH_PROVIDER = "local"
BOOTSTRAP_AUTH_SUBJECT = "bootstrap-analyst"
BOOTSTRAP_DISPLAY_NAME = "Bootstrap Analyst"

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "b8c9f2e1a4d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "user_account" not in inspector.get_table_names():
        op.create_table(
            "user_account",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=128), nullable=False),
            sa.Column("auth_provider", sa.String(length=64), nullable=False),
            sa.Column("auth_subject", sa.String(length=255), nullable=False),
            sa.Column("email", sa.String(length=320), nullable=True),
            sa.Column("display_name", sa.String(length=255), nullable=True),
            sa.Column(
                "is_bootstrap_user",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "workspace_id",
                "auth_provider",
                "auth_subject",
                name="uq_user_account_workspace_provider_subject",
            ),
        )
        op.create_index(
            "ix_user_account_workspace_id", "user_account", ["workspace_id"], unique=False
        )
        op.create_index(
            "ix_user_account_auth_provider", "user_account", ["auth_provider"], unique=False
        )
        op.create_index(
            "ix_user_account_auth_subject", "user_account", ["auth_subject"], unique=False
        )
        op.create_index(
            "ix_user_account_is_bootstrap_user", "user_account", ["is_bootstrap_user"], unique=False
        )

    conversation_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("chat_conversation")
    }
    owner_column_added = "owner_user_id" not in conversation_columns
    if owner_column_added:
        with op.batch_alter_table("chat_conversation", schema=None) as batch_op:
            batch_op.add_column(sa.Column("owner_user_id", sa.String(length=36), nullable=True))

    workspaces = {"default"}
    for workspace_id in bind.execute(
        sa.text("SELECT DISTINCT workspace_id FROM chat_conversation")
    ):
        if workspace_id[0]:
            workspaces.add(workspace_id[0])

    now = datetime.now(tz=UTC)
    for workspace_id in sorted(workspaces):
        existing_user_id = bind.execute(
            sa.text(
                """
                SELECT id
                FROM user_account
                WHERE workspace_id = :workspace_id
                  AND auth_provider = :auth_provider
                  AND auth_subject = :auth_subject
                LIMIT 1
                """
            ),
            {
                "workspace_id": workspace_id,
                "auth_provider": BOOTSTRAP_AUTH_PROVIDER,
                "auth_subject": BOOTSTRAP_AUTH_SUBJECT,
            },
        ).scalar_one_or_none()
        user_id = existing_user_id or str(uuid4())
        if existing_user_id is None:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO user_account (
                        id,
                        workspace_id,
                        auth_provider,
                        auth_subject,
                        email,
                        display_name,
                        is_bootstrap_user,
                        created_at,
                        updated_at,
                        last_seen_at
                    ) VALUES (
                        :id,
                        :workspace_id,
                        :auth_provider,
                        :auth_subject,
                        :email,
                        :display_name,
                        :is_bootstrap_user,
                        :created_at,
                        :updated_at,
                        :last_seen_at
                    )
                    """
                ),
                {
                    "id": user_id,
                    "workspace_id": workspace_id,
                    "auth_provider": BOOTSTRAP_AUTH_PROVIDER,
                    "auth_subject": BOOTSTRAP_AUTH_SUBJECT,
                    "email": None,
                    "display_name": BOOTSTRAP_DISPLAY_NAME,
                    "is_bootstrap_user": True,
                    "created_at": now,
                    "updated_at": now,
                    "last_seen_at": now,
                },
            )

        bind.execute(
            sa.text(
                """
                UPDATE user_account
                SET updated_at = :updated_at,
                    last_seen_at = COALESCE(last_seen_at, :last_seen_at)
                WHERE id = :id
                """
            ),
            {
                "id": user_id,
                "updated_at": now,
                "last_seen_at": now,
            },
        )
        bind.execute(
            sa.text(
                """
                UPDATE chat_conversation
                SET owner_user_id = :owner_user_id
                WHERE workspace_id = :workspace_id
                  AND owner_user_id IS NULL
                """
            ),
            {
                "owner_user_id": user_id,
                "workspace_id": workspace_id,
            },
        )

    if owner_column_added:
        with op.batch_alter_table("chat_conversation", schema=None) as batch_op:
            batch_op.alter_column(
                "owner_user_id", existing_type=sa.String(length=36), nullable=False
            )
            batch_op.create_index(
                "ix_chat_conversation_owner_user_id", ["owner_user_id"], unique=False
            )
            batch_op.create_foreign_key(
                "fk_chat_conversation_owner_user_id",
                "user_account",
                ["owner_user_id"],
                ["id"],
                ondelete="RESTRICT",
            )

    with op.batch_alter_table("user_account", schema=None) as batch_op:
        batch_op.alter_column("is_bootstrap_user", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("chat_conversation", schema=None) as batch_op:
        batch_op.drop_constraint("fk_chat_conversation_owner_user_id", type_="foreignkey")
        batch_op.drop_index("ix_chat_conversation_owner_user_id")
        batch_op.drop_column("owner_user_id")

    op.drop_index("ix_user_account_is_bootstrap_user", table_name="user_account")
    op.drop_index("ix_user_account_auth_subject", table_name="user_account")
    op.drop_index("ix_user_account_auth_provider", table_name="user_account")
    op.drop_index("ix_user_account_workspace_id", table_name="user_account")
    op.drop_table("user_account")
