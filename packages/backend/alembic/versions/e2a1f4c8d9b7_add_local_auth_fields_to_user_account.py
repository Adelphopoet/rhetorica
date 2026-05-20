"""add local auth fields to user account

Revision ID: e2a1f4c8d9b7
Revises: c9d8e7f6a5b4
Create Date: 2026-04-05 12:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

LOCAL_AUTH_PROVIDER = "local"
BOOTSTRAP_AUTH_PROVIDER = "bootstrap"
BOOTSTRAP_AUTH_SUBJECT = "bootstrap-admin"

# revision identifiers, used by Alembic.
revision: str = "e2a1f4c8d9b7"
down_revision: str | None = "c9d8e7f6a5b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("user_account")}

    with op.batch_alter_table("user_account", schema=None) as batch_op:
        if "login" not in columns:
            batch_op.add_column(
                sa.Column("login", sa.String(length=128), nullable=True)
            )
        if "password_hash" not in columns:
            batch_op.add_column(sa.Column("password_hash", sa.String(length=512), nullable=True))
        if "role" not in columns:
            batch_op.add_column(
                sa.Column(
                    "role",
                    sa.String(length=32),
                    nullable=False,
                    server_default="admin",
                )
            )
        if "is_active" not in columns:
            batch_op.add_column(
                sa.Column(
                    "is_active",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.true(),
                )
            )

    bind.execute(
        sa.text(
            """
            UPDATE user_account
            SET login = COALESCE(login, CASE
                WHEN auth_provider = :bootstrap_provider THEN 'bootstrap-admin'
                ELSE lower(COALESCE(display_name, auth_subject, id))
            END),
                role = COALESCE(role, 'admin'),
                is_active = COALESCE(is_active, true),
                auth_provider = CASE
                    WHEN is_bootstrap_user = true THEN :bootstrap_provider
                    ELSE COALESCE(auth_provider, :local_provider)
                END,
                auth_subject = CASE
                    WHEN is_bootstrap_user = true THEN :bootstrap_subject
                    ELSE auth_subject
                END
            """
        ),
        {
            "bootstrap_provider": BOOTSTRAP_AUTH_PROVIDER,
            "bootstrap_subject": BOOTSTRAP_AUTH_SUBJECT,
            "local_provider": LOCAL_AUTH_PROVIDER,
        },
    )

    with op.batch_alter_table("user_account", schema=None) as batch_op:
        batch_op.alter_column("login", existing_type=sa.String(length=128), nullable=False)
        batch_op.create_unique_constraint(
            "uq_user_account_workspace_login",
            ["workspace_id", "login"],
        )
        batch_op.create_index("ix_user_account_is_active", ["is_active"], unique=False)
        batch_op.alter_column("role", existing_type=sa.String(length=32), server_default=None)
        batch_op.alter_column("is_active", existing_type=sa.Boolean(), server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("user_account", schema=None) as batch_op:
        batch_op.drop_index("ix_user_account_is_active")
        batch_op.drop_constraint("uq_user_account_workspace_login", type_="unique")
        batch_op.drop_column("is_active")
        batch_op.drop_column("role")
        batch_op.drop_column("password_hash")
        batch_op.drop_column("login")
