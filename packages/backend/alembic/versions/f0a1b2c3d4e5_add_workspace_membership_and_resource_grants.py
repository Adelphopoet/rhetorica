"""Add workspace memberships, resource grants and policy audit.

Revision ID: f0a1b2c3d4e5
Revises: e8f9a0b1c2d3
Create Date: 2026-09-22
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: str | Sequence[str] | None = "e8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workspace_is_active", "workspace", ["is_active"])
    op.create_table(
        "workspace_membership",
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner', 'analyst', 'viewer')",
            name="ck_workspace_member_role",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user_account.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
    )
    op.create_index(
        "ix_workspace_membership_user_active",
        "workspace_membership",
        ["user_id", "is_active"],
    )
    op.create_table(
        "workspace_resource_grant",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("resource_kind", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("owner_workspace_id", sa.String(length=128), nullable=False),
        sa.Column("recipient_workspace_id", sa.String(length=128), nullable=False),
        sa.Column("alias", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["user_account.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recipient_workspace_id",
            "resource_kind",
            "alias",
            name="uq_workspace_resource_grant_alias",
        ),
        sa.UniqueConstraint(
            "recipient_workspace_id",
            "resource_kind",
            "resource_id",
            "owner_workspace_id",
            name="uq_workspace_resource_grant_resource",
        ),
    )
    op.create_index(
        "ix_workspace_resource_grant_recipient_active",
        "workspace_resource_grant",
        ["recipient_workspace_id", "resource_kind", "is_active"],
    )
    op.create_table(
        "workspace_policy_audit_event",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("previous_value", sa.JSON(), nullable=True),
        sa.Column("current_value", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["user_account.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_policy_audit_workspace_created",
        "workspace_policy_audit_event",
        ["workspace_id", "created_at"],
    )

    bind = op.get_bind()
    now = datetime.now(tz=UTC)
    metadata = sa.MetaData()
    users = sa.Table("user_account", metadata, autoload_with=bind)
    workspaces = sa.Table("workspace", metadata, autoload_with=bind)
    memberships = sa.Table("workspace_membership", metadata, autoload_with=bind)
    workspace_ids = {
        row[0] for row in bind.execute(sa.select(users.c.workspace_id).distinct()) if row[0]
    }
    resource_tables = (
        "connector_config",
        "llm_connector_config",
        "rag_connector_config",
        "documentation_connector_config",
        "gitlab_connector_config",
        "mcp_connector_config",
        "airflow_connector_config",
        "kubernetes_connector_config",
        "victoriametrics_connector_config",
        "mattermost_connector_config",
        "workspace_skill",
    )
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    for table_name in resource_tables:
        if table_name not in existing_tables:
            continue
        table = sa.Table(table_name, metadata, autoload_with=bind)
        workspace_ids.update(
            row[0] for row in bind.execute(sa.select(table.c.workspace_id).distinct()) if row[0]
        )
    if workspace_ids:
        bind.execute(
            workspaces.insert(),
            [
                {
                    "id": workspace_id,
                    "name": workspace_id,
                    "is_active": True,
                    "metadata_json": {},
                    "created_at": now,
                    "updated_at": now,
                }
                for workspace_id in sorted(workspace_ids)
            ],
        )
    user_rows = bind.execute(
        sa.select(users.c.id, users.c.workspace_id, users.c.role, users.c.is_active)
    ).mappings()
    membership_rows = [
        {
            "workspace_id": row["workspace_id"],
            "user_id": row["id"],
            "role": "owner" if row["role"] == "admin" else "analyst",
            "is_active": bool(row["is_active"]),
            "created_at": now,
            "updated_at": now,
        }
        for row in user_rows
    ]
    if membership_rows:
        bind.execute(memberships.insert(), membership_rows)


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_policy_audit_workspace_created",
        table_name="workspace_policy_audit_event",
    )
    op.drop_table("workspace_policy_audit_event")
    op.drop_index(
        "ix_workspace_resource_grant_recipient_active",
        table_name="workspace_resource_grant",
    )
    op.drop_table("workspace_resource_grant")
    op.drop_index("ix_workspace_membership_user_active", table_name="workspace_membership")
    op.drop_table("workspace_membership")
    op.drop_index("ix_workspace_is_active", table_name="workspace")
    op.drop_table("workspace")
