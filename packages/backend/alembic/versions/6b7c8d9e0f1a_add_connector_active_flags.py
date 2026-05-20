"""add connector active flags

Revision ID: 6b7c8d9e0f1a
Revises: f3a4b5c6d7e8
Create Date: 2026-05-16 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6b7c8d9e0f1a"
down_revision: str | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "connector_config",
    "llm_connector_config",
    "openmetadata_connector_config",
    "rag_connector_config",
    "confluence_connector_config",
    "jira_connector_config",
    "mattermost_connector_config",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for table_name in TABLES:
        if table_name not in existing_tables:
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "is_active" in columns:
            continue
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "is_active",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.true(),
                )
            )
            batch_op.create_index(
                f"ix_{table_name}_is_active",
                ["is_active"],
                unique=False,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for table_name in reversed(TABLES):
        if table_name not in existing_tables:
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "is_active" not in columns:
            continue
        indexes = {index["name"] for index in inspector.get_indexes(table_name)}
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            if f"ix_{table_name}_is_active" in indexes:
                batch_op.drop_index(f"ix_{table_name}_is_active")
            batch_op.drop_column("is_active")
