from pathlib import Path

import pytest
from alembic import command
from rhetorica_backend.contracts.scenarios import ScenarioConnectorBindings, ScenarioToolPolicy
from rhetorica_backend.infra.postgres.base import Base
from rhetorica_backend.infra.postgres.migrations import get_alembic_config
from rhetorica_backend.infra.postgres.scenario_repository import ScenarioRepository
from rhetorica_backend.infra.postgres.scenario_workflow_models import ScenarioVersionOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from sqlalchemy import select


@pytest.mark.parametrize("precreated", [False, True])
def test_migration_preserves_legacy_and_tolerates_create_all(
    tmp_path: Path, monkeypatch, precreated: bool
) -> None:
    url = f"sqlite:///{tmp_path}/migration.db"
    db = DatabaseRuntime(url)
    db.create_schema()
    user = UserAccountRepository(db).ensure_bootstrap_user("default")
    scenario = ScenarioRepository(db).create_definition(
        slug="legacy",
        workspace_id="default",
        owner_user_id=user.id,
        scope="personal",
        title="Legacy",
        summary="Legacy",
        description="",
        input_schema={},
        prompt_template="Original prompt",
        tool_policy=ScenarioToolPolicy(),
        default_connector_bindings=ScenarioConnectorBindings(),
        status="published",
    )
    if not precreated:
        existing = {"scenario_definition", "scenario_run"}
        for table in reversed(Base.metadata.sorted_tables):
            if table.name.startswith("scenario_") and table.name not in existing:
                table.drop(db.engine)
    monkeypatch.setenv("DATABASE_URL", url)
    config = get_alembic_config()
    command.stamp(config, "d6e7f8a9b0c1")
    command.upgrade(config, "e7f8a9b0c1d2")
    with db.session() as session:
        versions = list(session.scalars(select(ScenarioVersionOrm)))
        assert len(versions) == 1
        assert versions[0].scenario_id == scenario.id
        assert versions[0].kind == "legacy"
        assert versions[0].definition["prompt_template"] == "Original prompt"
