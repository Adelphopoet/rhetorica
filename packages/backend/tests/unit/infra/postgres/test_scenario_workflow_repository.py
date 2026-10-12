from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from rhetorica_backend.contracts.scenario_workflows import (
    SaveScenarioDraftRequest,
    StartScenarioRunRequest,
)
from rhetorica_backend.contracts.scenarios import ScenarioConnectorBindings, ScenarioToolPolicy
from rhetorica_backend.domain.scenarios.errors import (
    WorkflowConflict,
    WorkflowError,
    WorkflowNotFound,
)
from rhetorica_backend.infra.postgres.scenario_activity_repository import ScenarioActivityRepository
from rhetorica_backend.infra.postgres.scenario_authoring_repository import (
    ScenarioAuthoringRepository,
)
from rhetorica_backend.infra.postgres.scenario_repository import ScenarioRepository
from rhetorica_backend.infra.postgres.scenario_run_repository import ScenarioRunRepository
from rhetorica_backend.infra.postgres.scenario_workflow_models import ScenarioOutboxOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from sqlalchemy import event, select


@pytest.fixture
def setup(tmp_path: Path):
    db = DatabaseRuntime(f"sqlite:///{tmp_path}/scenarios.db")

    @event.listens_for(db.engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    db.create_schema()
    user = UserAccountRepository(db).ensure_bootstrap_user("default")
    definition = ScenarioRepository(db).create_definition(
        slug="test",
        workspace_id="default",
        owner_user_id=user.id,
        scope="personal",
        title="Report",
        summary="Report",
        description="",
        input_schema={},
        prompt_template="Original",
        tool_policy=ScenarioToolPolicy(),
        default_connector_bindings=ScenarioConnectorBindings(),
        status="published",
    )
    return db, user, definition


def test_draft_compare_and_swap_and_immutable_publication(setup) -> None:
    db, user, definition = setup
    authoring = ScenarioAuthoringRepository(db)
    draft = authoring.get_draft(definition.id, user)
    assert draft.revision == 0
    saved = authoring.save_draft(
        definition.id,
        user,
        SaveScenarioDraftRequest(
            expected_revision=0,
            workflow=draft.workflow,
        ),
    )
    with pytest.raises(WorkflowConflict):
        authoring.save_draft(
            definition.id,
            user,
            SaveScenarioDraftRequest(expected_revision=0, workflow=draft.workflow),
        )
    published = authoring.publish(definition.id, user, saved.revision)
    saved.workflow.nodes[1].instructions = "Changed"
    authoring.save_draft(
        definition.id, user, SaveScenarioDraftRequest(expected_revision=1, workflow=saved.workflow)
    )
    versions = authoring.list_versions(definition.id, user)
    assert [version.kind for version in versions] == ["workflow", "legacy"]
    assert versions[0].workflow.nodes[1].instructions == "Original"
    assert published.version == 2


def test_personal_definition_and_run_are_not_shared_with_other_user(setup) -> None:
    db, user, definition = setup
    other = UserAccountRepository(db).ensure_bootstrap_user("another")
    with pytest.raises(WorkflowNotFound):
        ScenarioAuthoringRepository(db).get_draft(definition.id, other)


def create_run(setup, key="request"):
    db, user, definition = setup
    repo = ScenarioRunRepository(db)
    request = StartScenarioRunRequest()
    version, kind, snapshot, workflow, title = repo.resolve_definition(definition.id, user, request)
    run = repo.create(
        run_id=str(uuid4()),
        scenario_id=definition.id,
        title=title,
        user=user,
        request=request,
        key=key,
        request_hash="a" * 64,
        version=version,
        kind=kind,
        definition=snapshot,
        workflow=workflow,
        agent_snapshots={},
        conversation_id=None,
        inputs={},
    )
    return repo, run


def test_atomic_admission_and_duplicate_request(setup) -> None:
    db, user, _ = setup
    repo, first = create_run(setup)
    _, repeated = create_run(setup)
    assert first.id == repeated.id
    with db.session() as session:
        assert len(list(session.scalars(select(ScenarioOutboxOrm)))) == 1
    with pytest.raises(WorkflowConflict, match="idempotency_key_reused"):
        repo.find_request(user, "request", "b" * 64)


def test_fenced_completion_and_idempotent_approval(setup) -> None:
    db, user, _ = setup
    repo, run = create_run(setup)
    activities = ScenarioActivityRepository(db)
    activities.begin_step(run.id, "agent", 1, "input-1")
    activities.begin_step(run.id, "agent", 2, "input-2")
    with pytest.raises(WorkflowConflict, match="stale_step_attempt"):
        activities.finish_step(run.id, "agent", 1, output_ref="stale")
    activities.finish_step(run.id, "agent", 2, status="waiting", output_ref="current")
    repo.approve(run.id, "agent", user, True)
    repo.approve(run.id, "agent", user, True)
    with pytest.raises(WorkflowConflict, match="approval_already_decided"):
        repo.approve(run.id, "agent", user, False)
    events = repo.events(run.id, user)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert len([event for event in events if event.type == "step.approved"]) == 1


def test_cancel_is_durable_and_does_not_duplicate_commands(setup) -> None:
    db, user, _ = setup
    repo, run = create_run(setup)
    assert repo.cancel(run.id, user).status == "cancel_requested"
    repo.cancel(run.id, user)
    assert (
        len([item for item in ScenarioActivityRepository(db).outbox() if item.kind == "cancel"])
        == 1
    )


def test_operation_publication_rejects_stale_attempt_and_stopped_run(setup) -> None:
    db, user, _ = setup
    runs, run = create_run(setup)
    activities = ScenarioActivityRepository(db)
    activities.begin_step(run.id, "agent", 2, "inputs")
    data = Mock()
    with pytest.raises(WorkflowConflict, match="stale_step_attempt"):
        activities.commit_operation_result(run.id, "agent", 1, data, "python-op", {"result": 1})
    data.put.assert_not_called()
    activities.commit_operation_result(run.id, "agent", 2, data, "python-op", {"result": 2})
    data.put.assert_called_once_with(
        run_id=run.id, node_id="agent", attempt=0, role="python-op", value={"result": 2}
    )
    data.reset_mock()
    runs.cancel(run.id, user)
    with pytest.raises(WorkflowError, match="scenario_run_stopped"):
        activities.commit_operation_result(run.id, "agent", 2, data, "python-op", {"result": 3})
    data.put.assert_not_called()


@pytest.mark.parametrize("role", ["analyst", "admin"])
def test_official_definition_does_not_share_runs_or_approval_rights(setup, role):
    from rhetorica_backend.infra.postgres.scenario_models import ScenarioDefinitionOrm

    db, owner, definition = setup
    other = UserAccountRepository(db).create_local_user(
        workspace_id="default", login=f"other-{role}", password_hash="test-only", role=role
    )
    repo, run = create_run(setup)
    with db.session() as session:
        session.get(ScenarioDefinitionOrm, definition.id).scope = "official"
        session.commit()
    assert ScenarioAuthoringRepository(db).list_versions(definition.id, other)
    if role == "analyst":
        with pytest.raises(WorkflowNotFound):
            ScenarioAuthoringRepository(db).get_draft(definition.id, other)
    else:
        assert ScenarioAuthoringRepository(db).get_draft(definition.id, other)
    for action in [
        lambda: repo.get(run.id, other),
        lambda: repo.events(run.id, other),
        lambda: repo.cancel(run.id, other),
        lambda: repo.approve(run.id, "agent", other, True),
    ]:
        with pytest.raises(WorkflowNotFound):
            action()
    assert repo.list_runs(other) == []
    assert repo.get(run.id, owner)[0].status == "queued"
