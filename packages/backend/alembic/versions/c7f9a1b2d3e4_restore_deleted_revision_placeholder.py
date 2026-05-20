"""restore deleted revision placeholder

Revision ID: c7f9a1b2d3e4
Revises: e2a1f4c8d9b7
Create Date: 2026-04-08 17:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "c7f9a1b2d3e4"
down_revision: str | None = "e2a1f4c8d9b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The original revision was deleted after it had already been stamped into
    # local databases. Keep this no-op placeholder so Alembic can traverse the
    # history and continue upgrading those databases.
    return


def downgrade() -> None:
    return
