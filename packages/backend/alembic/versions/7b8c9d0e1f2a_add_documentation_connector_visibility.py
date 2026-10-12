"""add documentation connector default visibility

Revision ID: 7b8c9d0e1f2a
Revises: 6a7b8c9d0e2f
Create Date: 2026-05-26 15:25:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7b8c9d0e1f2a"
down_revision: str | Sequence[str] | None = "6a7b8c9d0e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE documentation_connector_config
        ADD COLUMN IF NOT EXISTS visible_to_default_assistant boolean NOT NULL DEFAULT true
        """
    )
    op.execute(
        """
        ALTER TABLE documentation_connector_config
        ADD COLUMN IF NOT EXISTS usage_description text
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_documentation_connector_config_visible_to_default_assistant
        ON documentation_connector_config (visible_to_default_assistant)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_documentation_connector_config_visible_to_default_assistant")
    op.execute(
        """
        ALTER TABLE documentation_connector_config
        DROP COLUMN IF EXISTS usage_description
        """
    )
    op.execute(
        """
        ALTER TABLE documentation_connector_config
        DROP COLUMN IF EXISTS visible_to_default_assistant
        """
    )
