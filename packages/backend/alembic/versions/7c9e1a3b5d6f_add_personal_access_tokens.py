"""add personal access tokens

Revision ID: 7c9e1a3b5d6f
Revises: 6b8d0f2a4c3e
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c9e1a3b5d6f"
down_revision: str | Sequence[str] | None = "6b8d0f2a4c3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "personal_access_token" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "personal_access_token",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("token_prefix", sa.String(length=48), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("lifetime_days", sa.Integer(), nullable=False),
        sa.Column("auto_renew", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_renewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lifetime_days IN (90, 180, 365)",
            name="ck_personal_access_token_lifetime_days",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest", name="uq_personal_access_token_digest"),
        sa.UniqueConstraint("token_prefix", name="uq_personal_access_token_prefix"),
    )
    op.create_index(
        "ix_personal_access_token_owner_active",
        "personal_access_token",
        ["user_id", "revoked_at", "expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_personal_access_token_user_id",
        "personal_access_token",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_personal_access_token_workspace_id",
        "personal_access_token",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_personal_access_token_workspace_id", table_name="personal_access_token")
    op.drop_index("ix_personal_access_token_user_id", table_name="personal_access_token")
    op.drop_index("ix_personal_access_token_owner_active", table_name="personal_access_token")
    op.drop_table("personal_access_token")
