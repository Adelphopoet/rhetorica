"""Ensure every user keeps analyst-or-higher access to the default workspace.

Revision ID: f1b2c3d4e5f6
Revises: f0a1b2c3d4e5
Create Date: 2026-09-22
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "f1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(tz=UTC)
    default_workspace_id = os.getenv("AUTH_DEFAULT_WORKSPACE_ID", "default").strip() or "default"
    metadata = sa.MetaData()
    users = sa.Table("user_account", metadata, autoload_with=bind)
    workspaces = sa.Table("workspace", metadata, autoload_with=bind)
    memberships = sa.Table("workspace_membership", metadata, autoload_with=bind)

    default_workspace = bind.execute(
        sa.select(workspaces.c.id).where(workspaces.c.id == default_workspace_id)
    ).scalar_one_or_none()
    if default_workspace is None:
        bind.execute(
            workspaces.insert().values(
                id=default_workspace_id,
                name="Default",
                is_active=True,
                metadata_json={},
                created_at=now,
                updated_at=now,
            )
        )
    else:
        bind.execute(
            workspaces.update()
            .where(workspaces.c.id == default_workspace_id)
            .values(is_active=True, updated_at=now)
        )

    existing_user_ids = set(
        bind.execute(
            sa.select(memberships.c.user_id).where(
                memberships.c.workspace_id == default_workspace_id
            )
        ).scalars()
    )
    missing_user_ids = [
        user_id
        for user_id in bind.execute(sa.select(users.c.id)).scalars()
        if user_id not in existing_user_ids
    ]
    if missing_user_ids:
        bind.execute(
            memberships.insert(),
            [
                {
                    "workspace_id": default_workspace_id,
                    "user_id": user_id,
                    "role": "analyst",
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
                for user_id in missing_user_ids
            ],
        )

    bind.execute(
        memberships.update()
        .where(
            memberships.c.workspace_id == default_workspace_id,
            sa.or_(memberships.c.role == "viewer", memberships.c.is_active.is_(False)),
        )
        .values(role="analyst", is_active=True, updated_at=now)
    )


def downgrade() -> None:
    # Access grants are retained to avoid deleting memberships that may have
    # become intentional after this data migration.
    pass
