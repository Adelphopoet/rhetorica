"""Persist provider usage on existing coding iteration reservations."""

import sqlalchemy as sa
from alembic import op

revision = "39ea2d615c70"
down_revision = "8b32f90d6a41"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "coding_iteration_reservation", sa.Column("task_id", sa.String(36), nullable=True)
    )
    op.add_column("coding_iteration_reservation", sa.Column("usage_json", sa.JSON(), nullable=True))
    op.create_foreign_key(
        "fk_coding_iteration_task",
        "coding_iteration_reservation",
        "coding_agent_task",
        ["task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_coding_iteration_reservation_task_id", "coding_iteration_reservation", ["task_id"]
    )


def downgrade():
    op.drop_index(
        "ix_coding_iteration_reservation_task_id", table_name="coding_iteration_reservation"
    )
    op.drop_constraint(
        "fk_coding_iteration_task", "coding_iteration_reservation", type_="foreignkey"
    )
    op.drop_column("coding_iteration_reservation", "usage_json")
    op.drop_column("coding_iteration_reservation", "task_id")
