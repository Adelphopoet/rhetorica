"""merge_feedback_and_metric_heads

Revision ID: 2054007cd373
Revises: a8f9e2d1c3b5, e9f8a7b6c5d4
Create Date: 2026-05-05 14:51:04.162521

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = '2054007cd373'
down_revision: str | None = ('a8f9e2d1c3b5', 'e9f8a7b6c5d4')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
