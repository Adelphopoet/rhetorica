"""Reserve the local identity compatibility revision.

Revision ID: f2c3d4e5f6a7
Revises: f1b2c3d4e5f6
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "f2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "f1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Home workspace is part of the persisted auth identity and token binding.
    # Existing identities must remain where they were created.
    pass


def downgrade() -> None:
    pass
