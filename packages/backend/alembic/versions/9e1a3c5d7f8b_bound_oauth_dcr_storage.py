"""Bound OAuth DCR storage and track client activity.

Revision ID: 9e1a3c5d7f8b
Revises: 8d0f2b4c6e7a
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9e1a3c5d7f8b"
down_revision: str | Sequence[str] | None = "8d0f2b4c6e7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "oauth_client" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("oauth_client")}
    if "registration_source_digest" not in columns:
        op.add_column(
            "oauth_client",
            sa.Column("registration_source_digest", sa.String(length=64), nullable=True),
        )
    if "last_used_at" not in columns:
        op.add_column(
            "oauth_client",
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        )
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("oauth_client")}
    if "ix_oauth_client_registration_source_created" not in indexes:
        op.create_index(
            "ix_oauth_client_registration_source_created",
            "oauth_client",
            ["registration_source_digest", "created_at"],
        )
    if "ix_oauth_client_last_used" not in indexes:
        op.create_index(
            "ix_oauth_client_last_used",
            "oauth_client",
            ["last_used_at"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "oauth_client" not in set(inspector.get_table_names()):
        return
    indexes = {index["name"] for index in inspector.get_indexes("oauth_client")}
    if "ix_oauth_client_last_used" in indexes:
        op.drop_index("ix_oauth_client_last_used", table_name="oauth_client")
    if "ix_oauth_client_registration_source_created" in indexes:
        op.drop_index(
            "ix_oauth_client_registration_source_created",
            table_name="oauth_client",
        )
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("oauth_client")}
    if "last_used_at" in columns:
        op.drop_column("oauth_client", "last_used_at")
    if "registration_source_digest" in columns:
        op.drop_column("oauth_client", "registration_source_digest")
