"""add immutable workspace skill versions and retire optimizer family

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-09-29
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "d9e0f1a2b3c4"
down_revision: str | Sequence[str] | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "workspace_skill" not in inspector.get_table_names():
        return

    skill_columns = {column["name"] for column in inspector.get_columns("workspace_skill")}
    if "current_version_digest" not in skill_columns:
        with op.batch_alter_table("workspace_skill", schema=None) as batch_op:
            batch_op.add_column(sa.Column("current_version_digest", sa.String(length=128)))
        op.create_index(
            "ix_workspace_skill_current_version_digest",
            "workspace_skill",
            ["current_version_digest"],
            unique=False,
        )

    inspector = sa.inspect(bind)
    if "workspace_skill_version" not in inspector.get_table_names():
        op.create_table(
            "workspace_skill_version",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("skill_id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=128), nullable=False),
            sa.Column("version_digest", sa.String(length=128), nullable=False),
            sa.Column("source_url", sa.String(length=1024), nullable=True),
            sa.Column("content_markdown", sa.Text(), nullable=False),
            sa.Column("content_bundle", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "skill_id",
                "version_digest",
                name="uq_workspace_skill_version_digest",
            ),
        )
        op.create_index(
            "ix_workspace_skill_version_skill_id",
            "workspace_skill_version",
            ["skill_id"],
            unique=False,
        )
        op.create_index(
            "ix_workspace_skill_version_workspace_id",
            "workspace_skill_version",
            ["workspace_id"],
            unique=False,
        )

    metadata = sa.MetaData()
    skills = sa.Table("workspace_skill", metadata, autoload_with=bind)
    versions = sa.Table("workspace_skill_version", metadata, autoload_with=bind)
    rows = list(bind.execute(sa.select(skills)).mappings())
    for row in rows:
        content = str(row["content_markdown"] or "")
        digest = str(row.get("current_version_digest") or row.get("content_sha256") or "")
        if not digest:
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

        already_present = bind.scalar(
            sa.select(versions.c.id).where(
                versions.c.skill_id == row["id"],
                versions.c.version_digest == digest,
            )
        )
        if already_present is None:
            bind.execute(
                versions.insert().values(
                    id=str(uuid4()),
                    skill_id=row["id"],
                    workspace_id=row["workspace_id"],
                    version_digest=digest,
                    source_url=row.get("source_url"),
                    content_markdown=content,
                    content_bundle=row.get("content_bundle") or {},
                    created_at=row["updated_at"],
                )
            )

        values: dict[str, object] = {"current_version_digest": digest}
        if row["capability_family"] == "sql_optimization_advisor":
            values.update(
                capability_family="chat_instruction",
                visible_to_default_assistant=False,
            )
        bind.execute(skills.update().where(skills.c.id == row["id"]).values(**values))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "workspace_skill_version" in inspector.get_table_names():
        op.drop_index("ix_workspace_skill_version_workspace_id", table_name="workspace_skill_version")
        op.drop_index("ix_workspace_skill_version_skill_id", table_name="workspace_skill_version")
        op.drop_table("workspace_skill_version")
    if "workspace_skill" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("workspace_skill")}
        if "current_version_digest" in columns:
            op.drop_index("ix_workspace_skill_current_version_digest", table_name="workspace_skill")
            with op.batch_alter_table("workspace_skill", schema=None) as batch_op:
                batch_op.drop_column("current_version_digest")
