"""Repair columns edited into already-applied revisions.

Revision ID: 9fa0b1c2d3e4
Revises: 8ef9a0b1c2d3

d8e9f0a1b2c3 gained reason_code and detail after some databases had already
applied it. d9e0f1a2b3c4 creates current_version_digest, but databases that
recorded that revision without the column still fail skill reads.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "9fa0b1c2d3e4"
down_revision: str | Sequence[str] | None = "8ef9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _add_publication_receipt_columns(inspector)
    if _add_skill_version_digest(inspector):
        _backfill_skill_version_digests(bind)


def downgrade() -> None:
    # These columns belong to d8e9f0a1b2c3 and d9e0f1a2b3c4. This revision only
    # repairs databases that applied those files before the columns existed.
    return


def _add_publication_receipt_columns(inspector: sa.Inspector) -> None:
    if "coding_publication_operation" not in inspector.get_table_names():
        return
    columns = {
        column["name"] for column in inspector.get_columns("coding_publication_operation")
    }
    if "reason_code" not in columns:
        op.add_column(
            "coding_publication_operation",
            sa.Column("reason_code", sa.String(length=64), nullable=True),
        )
    if "detail" not in columns:
        op.add_column(
            "coding_publication_operation",
            sa.Column("detail", sa.String(length=512), nullable=True),
        )


def _add_skill_version_digest(inspector: sa.Inspector) -> bool:
    if "workspace_skill" not in inspector.get_table_names():
        return False
    columns = {column["name"] for column in inspector.get_columns("workspace_skill")}
    added = "current_version_digest" not in columns
    if added:
        op.add_column(
            "workspace_skill",
            sa.Column("current_version_digest", sa.String(length=128), nullable=True),
        )
    index_names = {index["name"] for index in inspector.get_indexes("workspace_skill")}
    if "ix_workspace_skill_current_version_digest" not in index_names:
        op.create_index(
            "ix_workspace_skill_current_version_digest",
            "workspace_skill",
            ["current_version_digest"],
            unique=False,
        )
    return added


def _backfill_skill_version_digests(bind: sa.Connection) -> None:
    metadata = sa.MetaData()
    skills = sa.Table("workspace_skill", metadata, autoload_with=bind)
    versions = None
    if "workspace_skill_version" in sa.inspect(bind).get_table_names():
        versions = sa.Table("workspace_skill_version", metadata, autoload_with=bind)
    rows = list(
        bind.execute(sa.select(skills).where(skills.c.current_version_digest.is_(None))).mappings()
    )
    for row in rows:
        content = str(row["content_markdown"] or "")
        digest = str(row.get("content_sha256") or "")
        if not digest:
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if versions is not None:
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
        bind.execute(
            skills.update()
            .where(skills.c.id == row["id"])
            .values(current_version_digest=digest)
        )
