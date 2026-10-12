"""add mattermost connector agent binding

Revision ID: b2c3d4e5f6a7
Revises: a0b1c2d3e4f6
Create Date: 2026-07-01 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a0b1c2d3e4f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("mattermost_connector_config")}

    if "agent_id" not in columns:
        op.add_column(
            "mattermost_connector_config",
            sa.Column("agent_id", sa.String(length=36), nullable=True),
        )
        op.create_index(
            "ix_mattermost_connector_config_agent_id",
            "mattermost_connector_config",
            ["agent_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("mattermost_connector_config")}

    if "agent_id" in columns:
        op.drop_index(
            "ix_mattermost_connector_config_agent_id",
            table_name="mattermost_connector_config",
        )
        op.drop_column("mattermost_connector_config", "agent_id")
