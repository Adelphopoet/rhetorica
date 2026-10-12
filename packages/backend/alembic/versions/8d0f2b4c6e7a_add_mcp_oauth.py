"""Add OAuth 2.1 client, consent, code and token-family storage.

Revision ID: 8d0f2b4c6e7a
Revises: 7c9e1a3b5d6f
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8d0f2b4c6e7a"
down_revision: str | Sequence[str] | None = "7c9e1a3b5d6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "oauth_client" not in existing:
        op.create_table(
            "oauth_client",
            sa.Column("client_id", sa.String(length=2048), primary_key=True),
            sa.Column("client_name", sa.String(length=255), nullable=False),
            sa.Column("client_uri", sa.Text(), nullable=True),
            sa.Column("redirect_uris", sa.JSON(), nullable=False),
            sa.Column("grant_types", sa.JSON(), nullable=False),
            sa.Column("response_types", sa.JSON(), nullable=False),
            sa.Column("token_endpoint_auth_method", sa.String(length=64), nullable=False),
            sa.Column("client_secret_prefix", sa.String(length=48), nullable=True),
            sa.Column("client_secret_digest", sa.String(length=64), nullable=True, unique=True),
            sa.Column("client_secret_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "oauth_authorization_transaction" not in existing:
        op.create_table(
            "oauth_authorization_transaction",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("transaction_prefix", sa.String(length=48), nullable=False, unique=True),
            sa.Column("transaction_digest", sa.String(length=64), nullable=False, unique=True),
            sa.Column("client_id", sa.Text(), nullable=False),
            sa.Column("client_name", sa.String(length=255), nullable=False),
            sa.Column("client_uri", sa.Text(), nullable=True),
            sa.Column("redirect_uri", sa.Text(), nullable=False),
            sa.Column("scopes", sa.JSON(), nullable=False),
            sa.Column("state", sa.Text(), nullable=True),
            sa.Column("code_challenge", sa.String(length=128), nullable=False),
            sa.Column("resource", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_oauth_transaction_active",
            "oauth_authorization_transaction",
            ["expires_at", "consumed_at"],
        )
    if "oauth_authorization_code" not in existing:
        op.create_table(
            "oauth_authorization_code",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("code_prefix", sa.String(length=48), nullable=False, unique=True),
            sa.Column("code_digest", sa.String(length=64), nullable=False, unique=True),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("user_account.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("workspace_id", sa.String(length=128), nullable=False),
            sa.Column("client_id", sa.Text(), nullable=False),
            sa.Column("redirect_uri", sa.Text(), nullable=False),
            sa.Column("scopes", sa.JSON(), nullable=False),
            sa.Column("code_challenge", sa.String(length=128), nullable=False),
            sa.Column("resource", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_oauth_authorization_code_user_id", "oauth_authorization_code", ["user_id"])
        op.create_index(
            "ix_oauth_authorization_code_workspace_id",
            "oauth_authorization_code",
            ["workspace_id"],
        )
        op.create_index(
            "ix_oauth_authorization_code_active",
            "oauth_authorization_code",
            ["expires_at", "consumed_at"],
        )
    if "oauth_token_grant" not in existing:
        op.create_table(
            "oauth_token_grant",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("family_id", sa.String(length=36), nullable=False),
            sa.Column("generation", sa.Integer(), nullable=False),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("user_account.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("workspace_id", sa.String(length=128), nullable=False),
            sa.Column("client_id", sa.Text(), nullable=False),
            sa.Column("scopes", sa.JSON(), nullable=False),
            sa.Column("resource", sa.Text(), nullable=False),
            sa.Column("access_token_prefix", sa.String(length=48), nullable=False, unique=True),
            sa.Column("access_token_digest", sa.String(length=64), nullable=False, unique=True),
            sa.Column("refresh_token_prefix", sa.String(length=48), nullable=False, unique=True),
            sa.Column("refresh_token_digest", sa.String(length=64), nullable=False, unique=True),
            sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("family_id", "generation", name="uq_oauth_family_generation"),
        )
        op.create_index("ix_oauth_token_grant_user_id", "oauth_token_grant", ["user_id"])
        op.create_index(
            "ix_oauth_token_grant_workspace_id", "oauth_token_grant", ["workspace_id"]
        )
        op.create_index("ix_oauth_token_family", "oauth_token_grant", ["family_id", "revoked_at"])
        op.create_index(
            "ix_oauth_access_active",
            "oauth_token_grant",
            ["access_token_prefix", "access_expires_at"],
        )
        op.create_index(
            "ix_oauth_refresh_active",
            "oauth_token_grant",
            ["refresh_token_prefix", "refresh_expires_at"],
        )


def downgrade() -> None:
    for table_name in (
        "oauth_token_grant",
        "oauth_authorization_code",
        "oauth_authorization_transaction",
        "oauth_client",
    ):
        if table_name in sa.inspect(op.get_bind()).get_table_names():
            op.drop_table(table_name)
