"""add coding sandbox leases

Revision ID: a6b7c8d9e0f1
Revises: f5c6d7e8f9a0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: str | None = "f5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coding_sandbox_lease",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("environment_profile_id", sa.String(length=128), nullable=False),
        sa.Column("environment_profile_revision", sa.Integer(), nullable=False),
        sa.Column("image_digest", sa.String(length=512), nullable=False),
        sa.Column("runtime_class_name", sa.String(length=128), nullable=False),
        sa.Column("tool_versions", sa.JSON(), nullable=False),
        sa.Column("software_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("job_name", sa.String(length=63), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("checkpoint_ref", sa.String(length=2048), nullable=True),
        sa.Column("candidate_artifact_id", sa.String(length=2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_name", name="uq_coding_sandbox_lease_job_name"),
        sa.UniqueConstraint(
            "workspace_id", "run_id", "task_id", "attempt", name="uq_coding_sandbox_task_attempt"
        ),
    )
    op.create_index("ix_coding_sandbox_lease_workspace_id", "coding_sandbox_lease", ["workspace_id"])
    op.create_index("ix_coding_sandbox_lease_owner_user_id", "coding_sandbox_lease", ["owner_user_id"])
    op.create_index("ix_coding_sandbox_lease_run_id", "coding_sandbox_lease", ["run_id"])
    op.create_index("ix_coding_sandbox_lease_task_id", "coding_sandbox_lease", ["task_id"])
    op.create_index("ix_coding_sandbox_lease_status", "coding_sandbox_lease", ["status"])
    op.create_index(
        "ix_coding_sandbox_reconcile", "coding_sandbox_lease", ["status", "expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_coding_sandbox_reconcile", table_name="coding_sandbox_lease")
    op.drop_index("ix_coding_sandbox_lease_status", table_name="coding_sandbox_lease")
    op.drop_index("ix_coding_sandbox_lease_task_id", table_name="coding_sandbox_lease")
    op.drop_index("ix_coding_sandbox_lease_run_id", table_name="coding_sandbox_lease")
    op.drop_index("ix_coding_sandbox_lease_owner_user_id", table_name="coding_sandbox_lease")
    op.drop_index("ix_coding_sandbox_lease_workspace_id", table_name="coding_sandbox_lease")
    op.drop_table("coding_sandbox_lease")
