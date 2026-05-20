"""add rag connector config

Revision ID: 3e1f4a9b2c77
Revises: 9a1d5e2b7f3c
Create Date: 2026-04-02 00:15:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3e1f4a9b2c77"
down_revision: str | None = "9a1d5e2b7f3c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rag_connector_config",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="qdrant"),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("api_key", sa.String(length=4096), nullable=True),
        sa.Column("api_key_secret_ref", sa.String(length=512), nullable=True),
        sa.Column(
            "collection_name",
            sa.String(length=255),
            nullable=False,
            server_default="rag_insights",
        ),
        sa.Column("vector_size", sa.Integer(), nullable=False, server_default="384"),
        sa.Column("distance", sa.String(length=32), nullable=False, server_default="cosine"),
        sa.Column("prefer_grpc", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "embedding_model",
            sa.String(length=255),
            nullable=False,
            server_default="BAAI/bge-small-en-v1.5",
        ),
        sa.Column("confidence_threshold", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("extra_settings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_rag_connector_workspace_name"),
    )
    op.create_index(
        "ix_rag_connector_config_workspace_id",
        "rag_connector_config",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_rag_connector_config_workspace_id", table_name="rag_connector_config")
    op.drop_table("rag_connector_config")
