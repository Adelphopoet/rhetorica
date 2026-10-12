"""merge rate limit and human review heads

Revision ID: f0a1b2c3d4e5
Revises: a1b2c3d4e5f7, e8f9a0b1c2d3
Create Date: 2026-09-23

"""

from collections.abc import Sequence

revision: str = "f0a1b2c3d4e5"
down_revision: str | Sequence[str] | None = ("a1b2c3d4e5f7", "e8f9a0b1c2d3")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
