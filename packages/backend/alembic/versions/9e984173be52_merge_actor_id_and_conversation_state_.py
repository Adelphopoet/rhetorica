"""merge actor_id and conversation_state heads

Revision ID: 9e984173be52
Revises: 9a2b3c4d5e6f, e5a6b7c8d9f0
Create Date: 2026-05-04 23:16:19.020417

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = '9e984173be52'
down_revision: str | None = ('9a2b3c4d5e6f', 'e5a6b7c8d9f0')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
