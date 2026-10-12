"""add versioned agent and default assistant governed policies

Revision ID: f5c6d7e8f9a0
Revises: a5c6d7e8f901
Create Date: 2026-09-25
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "f5c6d7e8f9a0"
down_revision: str | Sequence[str] | None = "a5c6d7e8f901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_governed_policy",
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_agent_governed_policy_revision_positive"),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_profile.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["user_account.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["user_account.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id"),
    )
    op.create_index(
        "ix_agent_governed_policy_workspace",
        "agent_governed_policy",
        ["workspace_id"],
    )
    op.create_table(
        "default_assistant_policy",
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_default_assistant_policy_revision_positive"),
        sa.ForeignKeyConstraint(["created_by"], ["user_account.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["user_account.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id"),
    )

    bind = op.get_bind()
    now = datetime.now(tz=UTC)
    metadata = sa.MetaData()
    agents = sa.Table("agent_profile", metadata, autoload_with=bind)
    workspaces = sa.Table("workspace", metadata, autoload_with=bind)
    agent_policies = sa.Table("agent_governed_policy", metadata, autoload_with=bind)
    default_policies = sa.Table("default_assistant_policy", metadata, autoload_with=bind)
    empty_policy = {
        "gitlab_write_scopes": [],
        "allowed_subagent_ids": [],
        "delegation_grants": [],
        "max_iterations": 100,
        "max_runtime_seconds": 1200,
    }
    agent_rows = list(
        bind.execute(sa.select(agents.c.id, agents.c.workspace_id, agents.c.created_by)).mappings()
    )
    if agent_rows:
        bind.execute(
            agent_policies.insert(),
            [
                {
                    "agent_id": row["id"],
                    "workspace_id": row["workspace_id"],
                    "revision": 1,
                    "policy_json": dict(empty_policy),
                    "created_by": row["created_by"],
                    "updated_by": row["created_by"],
                    "created_at": now,
                    "updated_at": now,
                }
                for row in agent_rows
            ],
        )
    workspace_ids = list(bind.execute(sa.select(workspaces.c.id)).scalars())
    if workspace_ids:
        bind.execute(
            default_policies.insert(),
            [
                {
                    "workspace_id": workspace_id,
                    "revision": 1,
                    "policy_json": dict(empty_policy),
                    "created_by": None,
                    "updated_by": None,
                    "created_at": now,
                    "updated_at": now,
                }
                for workspace_id in workspace_ids
            ],
        )


def downgrade() -> None:
    op.drop_table("default_assistant_policy")
    op.drop_index("ix_agent_governed_policy_workspace", table_name="agent_governed_policy")
    op.drop_table("agent_governed_policy")
