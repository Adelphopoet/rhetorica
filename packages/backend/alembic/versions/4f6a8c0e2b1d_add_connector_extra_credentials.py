"""add connector extra credential secret references

Revision ID: 4f6a8c0e2b1d
Revises: d3e4f5a6b7c8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4f6a8c0e2b1d"
down_revision: str | Sequence[str] | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connector_config",
        sa.Column(
            "extra_credential_secret_refs",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("connector_config", "extra_credential_secret_refs")
