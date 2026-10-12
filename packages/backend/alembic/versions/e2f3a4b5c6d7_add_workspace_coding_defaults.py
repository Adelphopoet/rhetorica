"""add workspace coding defaults

Revision ID: e2f3a4b5c6d7
Revises: e1f2a3b4c5d6
"""

import sqlalchemy as sa
from alembic import op

revision = "e2f3a4b5c6d7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "pip_dependencies" not in {
        column["name"] for column in inspector.get_columns("coding_environment_profile")
    }:
        op.add_column(
            "coding_environment_profile",
            sa.Column("pip_dependencies", sa.JSON(), nullable=False, server_default="[]"),
        )
    if "workspace_coding_defaults" not in inspector.get_table_names():
        op.create_table(
            "workspace_coding_defaults",
            sa.Column(
                "workspace_id",
                sa.String(128),
                sa.ForeignKey("workspace.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("resources_json", sa.JSON(), nullable=True),
            sa.Column("updated_by", sa.String(36), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    else:
        required = {"workspace_id", "revision", "resources_json", "updated_by", "updated_at"}
        if required != {
            column["name"] for column in inspector.get_columns("workspace_coding_defaults")
        }:
            raise RuntimeError("coding_defaults_schema_mismatch")


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "workspace_coding_defaults" in inspector.get_table_names():
        op.drop_table("workspace_coding_defaults")
    if "pip_dependencies" in {
        column["name"] for column in inspector.get_columns("coding_environment_profile")
    }:
        op.drop_column("coding_environment_profile", "pip_dependencies")
