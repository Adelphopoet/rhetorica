"""add confluence connector config

Revision ID: 7b3c2d1e9f44
Revises: 3e1f4a9b2c77
Create Date: 2026-04-02 10:10:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7b3c2d1e9f44"
down_revision: str | None = "3e1f4a9b2c77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "confluence_connector_config",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="confluence"),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("api_token", sa.String(length=4096), nullable=True),
        sa.Column("api_token_secret_ref", sa.String(length=512), nullable=True),
        sa.Column("auth_type", sa.String(length=32), nullable=False, server_default="bearer"),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("space_key", sa.String(length=64), nullable=True),
        sa.Column("page_limit", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("extra_settings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_confluence_connector_workspace_name"),
    )
    op.create_index(
        "ix_confluence_connector_config_workspace_id",
        "confluence_connector_config",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_confluence_connector_config_workspace_id", table_name="confluence_connector_config")
    op.drop_table("confluence_connector_config")
