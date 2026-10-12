"""add optional per-user API rate limit

Revision ID: a1b2c3d4e5f7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-17 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_account", schema=None) as batch_op:
        batch_op.add_column(sa.Column("api_rate_limit_per_hour", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "ck_user_account_api_rate_limit_positive",
            "api_rate_limit_per_hour IS NULL OR api_rate_limit_per_hour > 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("user_account", schema=None) as batch_op:
        batch_op.drop_constraint(
            "ck_user_account_api_rate_limit_positive",
            type_="check",
        )
        batch_op.drop_column("api_rate_limit_per_hour")
