"""Index OAuth token grant retention cleanup.

Revision ID: a2b4c6d8e0f1
Revises: 9e1a3c5d7f8b
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2b4c6d8e0f1"
down_revision: str | Sequence[str] | None = "9e1a3c5d7f8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "oauth_token_grant" not in set(inspector.get_table_names()):
        return
    indexes = {index["name"] for index in inspector.get_indexes("oauth_token_grant")}
    if "ix_oauth_grant_refresh_expires" not in indexes:
        op.create_index(
            "ix_oauth_grant_refresh_expires",
            "oauth_token_grant",
            ["refresh_expires_at"],
        )
    if "ix_oauth_grant_revoked_at" not in indexes:
        op.create_index(
            "ix_oauth_grant_revoked_at",
            "oauth_token_grant",
            ["revoked_at"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "oauth_token_grant" not in set(inspector.get_table_names()):
        return
    indexes = {index["name"] for index in inspector.get_indexes("oauth_token_grant")}
    if "ix_oauth_grant_revoked_at" in indexes:
        op.drop_index("ix_oauth_grant_revoked_at", table_name="oauth_token_grant")
    if "ix_oauth_grant_refresh_expires" in indexes:
        op.drop_index(
            "ix_oauth_grant_refresh_expires",
            table_name="oauth_token_grant",
        )
