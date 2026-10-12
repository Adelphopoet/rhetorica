"""Add usage descriptions to runtime connectors."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1f2e3d4c5b6a"
down_revision: str | Sequence[str] | None = ("a2b4c6d8e0f2", "e9f8a7b6c5d4")
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "connector_config",
        sa.Column("usage_description", sa.Text(), nullable=True),
    )
    op.add_column(
        "openmetadata_connector_config",
        sa.Column("usage_description", sa.Text(), nullable=True),
    )
    op.add_column(
        "confluence_connector_config",
        sa.Column("usage_description", sa.Text(), nullable=True),
    )
    op.add_column(
        "jira_connector_config",
        sa.Column("usage_description", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jira_connector_config", "usage_description")
    op.drop_column("confluence_connector_config", "usage_description")
    op.drop_column("openmetadata_connector_config", "usage_description")
    op.drop_column("connector_config", "usage_description")
