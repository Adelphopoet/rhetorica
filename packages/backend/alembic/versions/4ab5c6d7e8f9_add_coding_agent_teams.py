"""Add revisioned administrator-managed coding agent teams.

Revision ID: 4ab5c6d7e8f9
Revises: 39ea2d615c70
"""

import sqlalchemy as sa
from alembic import op

revision = "4ab5c6d7e8f9"
down_revision = "39ea2d615c70"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "coding_agent_team",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("active_revision_id", sa.String(36), nullable=True),
        sa.Column("latest_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_coding_agent_team_name"),
    )
    op.create_index("ix_coding_agent_team_workspace_id", "coding_agent_team", ["workspace_id"])
    op.create_index(
        "ix_coding_agent_team_active_revision_id", "coding_agent_team", ["active_revision_id"]
    )
    op.create_table(
        "coding_agent_team_revision",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("team_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("coordinator_agent_profile_id", sa.String(36), nullable=False),
        sa.Column("coordinator_instruction", sa.String(12000), nullable=False, server_default=""),
        sa.Column("members_json", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["coding_agent_team.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["user_account.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "revision", name="uq_coding_agent_team_revision"),
    )
    op.create_index(
        "ix_coding_agent_team_revision_workspace",
        "coding_agent_team_revision",
        ["workspace_id", "team_id", "revision"],
    )
    op.create_index(
        "ix_coding_agent_team_revision_team_id", "coding_agent_team_revision", ["team_id"]
    )


def downgrade():
    op.drop_index("ix_coding_agent_team_revision_team_id", table_name="coding_agent_team_revision")
    op.drop_index(
        "ix_coding_agent_team_revision_workspace", table_name="coding_agent_team_revision"
    )
    op.drop_table("coding_agent_team_revision")
    op.drop_index("ix_coding_agent_team_active_revision_id", table_name="coding_agent_team")
    op.drop_index("ix_coding_agent_team_workspace_id", table_name="coding_agent_team")
    op.drop_table("coding_agent_team")
