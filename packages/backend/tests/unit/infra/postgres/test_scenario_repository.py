from __future__ import annotations

from pathlib import Path

from rhetorica_backend.contracts.scenarios import ScenarioConnectorBindings, ScenarioToolPolicy
from rhetorica_backend.infra.postgres.scenario_models import ScenarioDefinitionOrm
from rhetorica_backend.infra.postgres.scenario_repository import ScenarioRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository


def _runtime(tmp_path: Path, db_name: str) -> DatabaseRuntime:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/{db_name}")
    runtime.create_schema()
    return runtime


def test_scenario_repository_filters_official_and_personal_visibility(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "scenario_repo.db")
    user_repository = UserAccountRepository(db=runtime)
    scenario_repository = ScenarioRepository(db=runtime)

    owner = user_repository.ensure_bootstrap_user("ws-1")
    other = user_repository.ensure_bootstrap_user("ws-2")

    scenario_repository.create_definition(
        slug="official-gmv",
        workspace_id="ws-1",
        owner_user_id=None,
        scope="official",
        title="Official GMV",
        summary="Official summary",
        description="Official description",
        input_schema={"type": "object", "properties": {}, "required": []},
        prompt_template="Prompt",
        tool_policy=ScenarioToolPolicy(),
        default_connector_bindings=ScenarioConnectorBindings(run_sql=True),
        status="published",
    )
    scenario_repository.create_definition(
        slug="personal-one",
        workspace_id="ws-1",
        owner_user_id=owner.id,
        scope="personal",
        title="Personal One",
        summary="Personal summary",
        description="Personal description",
        input_schema={"type": "object", "properties": {}, "required": []},
        prompt_template="Prompt",
        tool_policy=ScenarioToolPolicy(),
        default_connector_bindings=ScenarioConnectorBindings(run_sql=False),
        status="draft",
    )

    visible = scenario_repository.list_definitions(
        workspace_id="ws-1",
        owner_user_id=owner.id,
    )
    assert {(item.scope, item.slug) for item in visible} == {
        ("official", "official-gmv"),
        ("personal", "personal-one"),
    }

    hidden = scenario_repository.list_definitions(
        workspace_id="ws-1",
        owner_user_id=other.id,
    )
    assert [(item.scope, item.slug) for item in hidden] == [("official", "official-gmv")]


def test_scenario_repository_archives_without_destroying_definition(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "scenario_archive.db")
    owner = UserAccountRepository(db=runtime).ensure_bootstrap_user("ws-1")
    scenario_repository = ScenarioRepository(db=runtime)

    created = scenario_repository.create_definition(
        slug="personal-archive",
        workspace_id="ws-1",
        owner_user_id=owner.id,
        scope="personal",
        title="Archive me",
        summary="Summary",
        description="Description",
        input_schema={"type": "object", "properties": {}, "required": []},
        prompt_template="Prompt",
        tool_policy=ScenarioToolPolicy(),
        default_connector_bindings=ScenarioConnectorBindings(run_sql=True),
        status="published",
    )

    archived = scenario_repository.archive_definition(created.id)

    assert archived is not None
    assert archived.status == "archived"
    assert archived.version == 2
    assert (
        scenario_repository.get_definition_for_owner(
            created.id,
            workspace_id="ws-1",
            owner_user_id=owner.id,
        )
        is not None
    )


def test_scenario_repository_ignores_removed_metadata_binding_in_stored_rows(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path, "scenario_legacy_binding.db")
    owner = UserAccountRepository(db=runtime).ensure_bootstrap_user("ws-1")
    scenario_repository = ScenarioRepository(db=runtime)
    created = scenario_repository.create_definition(
        slug="legacy-metadata-binding",
        workspace_id="ws-1",
        owner_user_id=owner.id,
        scope="personal",
        title="Legacy binding",
        summary="Summary",
        description="Description",
        input_schema={"type": "object", "properties": {}, "required": []},
        prompt_template="Prompt",
        tool_policy=ScenarioToolPolicy(),
        default_connector_bindings=ScenarioConnectorBindings(run_sql=True),
        status="published",
    )
    with runtime.session() as session:
        row = session.get(ScenarioDefinitionOrm, created.id)
        assert row is not None
        row.default_connector_bindings = {
            "metadata_connector_name": None,
            "database_connector_name": "trino",
            "run_sql": True,
        }
        session.commit()

    loaded = scenario_repository.get_definition_for_owner(
        created.id,
        workspace_id="ws-1",
        owner_user_id=owner.id,
    )

    assert loaded is not None
    assert loaded.default_connector_bindings.database_connector_name == "trino"
    assert loaded.default_connector_bindings.run_sql is True
