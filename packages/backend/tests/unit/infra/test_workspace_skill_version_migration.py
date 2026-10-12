from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _migration_module():
    path = (
        Path(__file__).parents[3]
        / "alembic/versions/d9e0f1a2b3c4_add_workspace_skill_versions.py"
    )
    spec = importlib.util.spec_from_file_location("workspace_skill_version_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_preserves_legacy_skill_identity_bundle_and_agent_link() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    skills = sa.Table(
        "workspace_skill",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("capability_family", sa.String(64), nullable=False),
        sa.Column("visible_to_default_assistant", sa.Boolean, nullable=False),
        sa.Column("source_url", sa.String(1024)),
        sa.Column("content_markdown", sa.Text, nullable=False),
        sa.Column("content_bundle", sa.JSON, nullable=False),
        sa.Column("content_sha256", sa.String(128)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    profiles = sa.Table(
        "agent_profile",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("allowed_workspace_skill_ids", sa.JSON, nullable=False),
    )
    metadata.create_all(engine)
    legacy_bundle = {
        "assets": [
            {"path": "SKILL.md", "content_markdown": "# SQL guide"},
            {"path": "rules/read-only.md", "content_markdown": "No writes"},
        ]
    }
    with engine.begin() as connection:
        connection.execute(
            skills.insert(),
            [
                {
                    "id": "legacy-skill",
                    "workspace_id": "default",
                    "capability_family": "sql_optimization_advisor",
                    "visible_to_default_assistant": True,
                    "source_url": "https://example.invalid/legacy",
                    "content_markdown": "# SQL guide",
                    "content_bundle": legacy_bundle,
                    "content_sha256": "legacy-content",
                    "updated_at": datetime.now(tz=UTC),
                },
                {
                    "id": "normal-skill",
                    "workspace_id": "default",
                    "capability_family": "chat_instruction",
                    "visible_to_default_assistant": True,
                    "source_url": None,
                    "content_markdown": "# Existing chat skill",
                    "content_bundle": {"assets": [{"path": "SKILL.md"}]},
                    "content_sha256": "normal-content",
                    "updated_at": datetime.now(tz=UTC),
                },
            ],
        )
        connection.execute(
            profiles.insert().values(
                id="profile-1", allowed_workspace_skill_ids=["legacy-skill"]
            )
        )
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        operations._install_proxy()
        try:
            _migration_module().upgrade()
            # The migration is safe when deployment tooling retries before
            # recording the Alembic revision.
            _migration_module().upgrade()
        finally:
            operations._remove_proxy()

        upgraded = sa.Table("workspace_skill", sa.MetaData(), autoload_with=connection)
        versions = sa.Table("workspace_skill_version", sa.MetaData(), autoload_with=connection)
        legacy = connection.execute(
            sa.select(upgraded).where(upgraded.c.id == "legacy-skill")
        ).mappings().one()
        normal = connection.execute(
            sa.select(upgraded).where(upgraded.c.id == "normal-skill")
        ).mappings().one()

        assert legacy["id"] == "legacy-skill"
        assert legacy["capability_family"] == "chat_instruction"
        assert legacy["visible_to_default_assistant"] is False
        assert legacy["current_version_digest"] == "legacy-content"
        assert normal["visible_to_default_assistant"] is True
        assert connection.scalar(sa.select(sa.func.count()).select_from(versions)) == 2
        legacy_version = connection.execute(
            sa.select(versions).where(versions.c.skill_id == "legacy-skill")
        ).mappings().one()
        assert legacy_version["content_bundle"] == legacy_bundle
        assert legacy_version["source_url"] == "https://example.invalid/legacy"
        assert connection.scalar(
            sa.select(profiles.c.allowed_workspace_skill_ids).where(profiles.c.id == "profile-1")
        ) == ["legacy-skill"]
