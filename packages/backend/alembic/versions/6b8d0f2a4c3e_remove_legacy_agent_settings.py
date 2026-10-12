"""remove legacy agent settings

Revision ID: 6b8d0f2a4c3e
Revises: 5a7c9e1f3b2d
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6b8d0f2a4c3e"
down_revision: str | Sequence[str] | None = "5a7c9e1f3b2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if "agent_profile" not in sa.inspect(bind).get_table_names():
        return

    if bind.dialect.name == "postgresql":
        op.execute(
            """
            UPDATE agent_profile
            SET settings = (settings::jsonb - 'jira_write_allowed')::json
            WHERE settings::jsonb ? 'jira_write_allowed'
            """
        )
    elif bind.dialect.name == "sqlite":
        op.execute(
            """
            UPDATE agent_profile
            SET settings = json_remove(settings, '$.jira_write_allowed')
            WHERE json_valid(settings)
              AND json_type(settings, '$.jira_write_allowed') IS NOT NULL
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "agent_profile" not in sa.inspect(bind).get_table_names():
        return

    if bind.dialect.name == "postgresql":
        op.execute(
            """
            UPDATE agent_profile
            SET settings = jsonb_set(
                COALESCE(settings::jsonb, '{}'::jsonb),
                '{jira_write_allowed}',
                'false'::jsonb,
                true
            )::json
            WHERE settings IS NULL
               OR NOT settings::jsonb ? 'jira_write_allowed'
            """
        )
    elif bind.dialect.name == "sqlite":
        op.execute(
            """
            UPDATE agent_profile
            SET settings = json_set(settings, '$.jira_write_allowed', json('false'))
            WHERE json_valid(settings)
              AND json_type(settings, '$.jira_write_allowed') IS NULL
            """
        )
