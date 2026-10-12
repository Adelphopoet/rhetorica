"""Remember the last explicitly selected workspace per account.

Revision ID: 8ef9a0b1c2d3
Revises: 7de8f9a0b1c2
"""
import sqlalchemy as sa
from alembic import op

revision = "8ef9a0b1c2d3"
down_revision = "7de8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_account", sa.Column("last_workspace_id", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("user_account", "last_workspace_id")
