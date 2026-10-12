from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _migration_module():
    path = (
        Path(__file__).parents[3]
        / "alembic/versions/9fa0b1c2d3e4_repair_publication_and_skill_columns.py"
    )
    spec = importlib.util.spec_from_file_location("repair_publication_and_skill_columns", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_operations(connection: sa.Connection) -> Operations:
    operations = Operations(MigrationContext.configure(connection))
    operations._install_proxy()
    return operations


def test_upgrade_adds_missing_publication_and_skill_columns() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "coding_publication_operation",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("state", sa.String(32), nullable=False),
    )
    skills = sa.Table(
        "workspace_skill",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("source_url", sa.String(1024)),
        sa.Column("content_markdown", sa.Text, nullable=False),
        sa.Column("content_bundle", sa.JSON, nullable=False),
        sa.Column("content_sha256", sa.String(128)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    versions = sa.Table(
        "workspace_skill_version",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("skill_id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("version_digest", sa.String(128), nullable=False),
        sa.Column("source_url", sa.String(1024)),
        sa.Column("content_markdown", sa.Text, nullable=False),
        sa.Column("content_bundle", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)
    updated_at = datetime.now(tz=UTC)
    with engine.begin() as connection:
        connection.execute(
            skills.insert().values(
                id="skill-1",
                workspace_id="default",
                source_url="https://example.invalid/skill",
                content_markdown="# Skill",
                content_bundle={"assets": []},
                content_sha256="sha-from-content",
                updated_at=updated_at,
            )
        )
        operations = _install_operations(connection)
        try:
            module = _migration_module()
            module.upgrade()
            module.upgrade()
            module.downgrade()
        finally:
            operations._remove_proxy()

        inspector = sa.inspect(connection)
        publication = {
            column["name"]
            for column in inspector.get_columns("coding_publication_operation")
        }
        skill_columns = {
            column["name"] for column in inspector.get_columns("workspace_skill")
        }
        index_names = {index["name"] for index in inspector.get_indexes("workspace_skill")}
        upgraded_skills = sa.Table(
            "workspace_skill", sa.MetaData(), autoload_with=connection
        )
        skill = connection.execute(sa.select(upgraded_skills)).mappings().one()
        version_rows = connection.execute(sa.select(versions)).mappings().all()

    assert {"reason_code", "detail"} <= publication
    assert "current_version_digest" in skill_columns
    assert "ix_workspace_skill_current_version_digest" in index_names
    assert skill["current_version_digest"] == "sha-from-content"
    assert len(version_rows) == 1
    assert version_rows[0]["version_digest"] == "sha-from-content"
    assert version_rows[0]["source_url"] == "https://example.invalid/skill"


def test_upgrade_leaves_existing_publication_and_skill_columns() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    publication = sa.Table(
        "coding_publication_operation",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("reason_code", sa.String(64)),
        sa.Column("detail", sa.String(512)),
    )
    skills = sa.Table(
        "workspace_skill",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("content_markdown", sa.Text, nullable=False),
        sa.Column("content_sha256", sa.String(128)),
        sa.Column("current_version_digest", sa.String(128)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            publication.insert().values(id="op-1", reason_code="kept", detail="kept-detail")
        )
        connection.execute(
            skills.insert().values(
                id="skill-1",
                workspace_id="default",
                content_markdown="# Skill",
                content_sha256="other-sha",
                current_version_digest="already-set",
                updated_at=datetime.now(tz=UTC),
            )
        )
        operations = _install_operations(connection)
        try:
            _migration_module().upgrade()
        finally:
            operations._remove_proxy()

        stored_publication = connection.execute(sa.select(publication)).mappings().one()
        stored_skill = connection.execute(sa.select(skills)).mappings().one()

    assert stored_publication["reason_code"] == "kept"
    assert stored_publication["detail"] == "kept-detail"
    assert stored_skill["current_version_digest"] == "already-set"
