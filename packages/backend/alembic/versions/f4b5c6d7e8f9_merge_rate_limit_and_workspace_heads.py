"""merge rate limit and workspace heads

Revision ID: f4b5c6d7e8f9
Revises: a1b2c3d4e5f7, f2c3d4e5f6a7
Create Date: 2026-09-23

"""

from collections.abc import Sequence

revision: str = "f4b5c6d7e8f9"
down_revision: str | Sequence[str] | None = ("a1b2c3d4e5f7", "f2c3d4e5f6a7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
