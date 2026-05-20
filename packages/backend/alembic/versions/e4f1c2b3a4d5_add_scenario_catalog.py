"""add scenario catalog

Revision ID: e4f1c2b3a4d5
Revises: d1e2f3a4b5c6
Create Date: 2026-04-04 15:20:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f1c2b3a4d5"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scenario_definition",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.String(length=280), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column("tool_policy", sa.JSON(), nullable=False),
        sa.Column("default_connector_bindings", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_account.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_scenario_workspace_slug"),
    )
    op.create_index(
        "ix_scenario_definition_slug", "scenario_definition", ["slug"], unique=False
    )
    op.create_index(
        "ix_scenario_definition_workspace_id",
        "scenario_definition",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_scenario_definition_owner_user_id",
        "scenario_definition",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_scenario_definition_scope", "scenario_definition", ["scope"], unique=False
    )
    op.create_index(
        "ix_scenario_definition_status", "scenario_definition", ["status"], unique=False
    )

    op.create_table(
        "scenario_run",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scenario_definition_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("resolved_version", sa.Integer(), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["chat_conversation.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["user_account.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["scenario_definition_id"], ["scenario_definition.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scenario_run_workspace_id", "scenario_run", ["workspace_id"], unique=False)
    op.create_index(
        "ix_scenario_run_owner_user_id", "scenario_run", ["owner_user_id"], unique=False
    )
    op.create_index(
        "ix_scenario_run_conversation_id", "scenario_run", ["conversation_id"], unique=False
    )
    op.create_index(
        "ix_scenario_run_scenario_definition_id",
        "scenario_run",
        ["scenario_definition_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_scenario_run_scenario_definition_id", table_name="scenario_run")
    op.drop_index("ix_scenario_run_conversation_id", table_name="scenario_run")
    op.drop_index("ix_scenario_run_owner_user_id", table_name="scenario_run")
    op.drop_index("ix_scenario_run_workspace_id", table_name="scenario_run")
    op.drop_table("scenario_run")

    op.drop_index("ix_scenario_definition_status", table_name="scenario_definition")
    op.drop_index("ix_scenario_definition_scope", table_name="scenario_definition")
    op.drop_index("ix_scenario_definition_owner_user_id", table_name="scenario_definition")
    op.drop_index("ix_scenario_definition_workspace_id", table_name="scenario_definition")
    op.drop_index("ix_scenario_definition_slug", table_name="scenario_definition")
    op.drop_table("scenario_definition")
