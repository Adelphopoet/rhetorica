"""Persist the immutable team revision selected by a coding run.

Revision ID: 5bc6d7e8f9a0
Revises: 4ab5c6d7e8f9
"""

import sqlalchemy as sa
from alembic import op

revision = "5bc6d7e8f9a0"
down_revision = "4ab5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("coding_run", sa.Column("team_snapshot_json", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("coding_run", "team_snapshot_json")
