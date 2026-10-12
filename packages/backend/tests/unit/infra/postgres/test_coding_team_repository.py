from datetime import UTC, datetime

import pytest
from rhetorica_backend.contracts.coding import CreateCodingAgentTeamRevision
from rhetorica_backend.domain.coding.errors import CodingRunError
from rhetorica_backend.infra.postgres.coding_team_repository import CodingAgentTeamRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.workspace_models import WorkspaceOrm


def _request(expected_revision: int) -> CreateCodingAgentTeamRevision:
    return CreateCodingAgentTeamRevision(
        expected_revision=expected_revision,
        coordinator_agent_profile_id="coordinator",
        members=[
            {
                "id": "member-1",
                "agent_profile_id": "developer",
                "label": "Developer",
                "capability": "candidate",
            }
        ],
    )


def test_team_revisions_are_workspace_scoped_and_optimistically_locked(tmp_path) -> None:
    db = DatabaseRuntime(f"sqlite:///{tmp_path}/teams.db")
    db.create_schema()
    with db.session() as session:
        session.add(
            WorkspaceOrm(
                id="analytics",
                name="Analytics",
                is_active=True,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()
    repository = CodingAgentTeamRepository(db)

    first = repository.append_revision(
        workspace_id="analytics",
        team_id=None,
        name="dbt team",
        actor_user_id="admin",
        request=_request(0),
    )
    assert first.revision == 1
    assert first.members[0].label == "Developer"

    with pytest.raises(CodingRunError, match="coding_team_revision_conflict"):
        repository.append_revision(
            workspace_id="analytics",
            team_id=first.team_id,
            name="ignored",
            actor_user_id="admin",
            request=_request(0),
        )

    active = repository.activate(
        workspace_id="analytics", team_id=first.team_id, revision_id=first.id
    )
    assert active.active_revision_id == first.id
    assert (
        repository.get_revision(workspace_id="analytics", revision_id=first.id).team_id
        == first.team_id
    )

    second = repository.append_revision(
        workspace_id="analytics",
        team_id=first.team_id,
        name="ignored",
        actor_user_id="admin",
        request=_request(1),
    )
    repository.activate(workspace_id="analytics", team_id=first.team_id, revision_id=second.id)
    assert (
        repository.get_revision(workspace_id="analytics", revision_id=first.id).is_active is False
    )
    assert (
        repository.get_revision(workspace_id="analytics", revision_id=second.id).is_active is True
    )


def test_team_rename_history_and_active_revision_resolution(tmp_path) -> None:
    db = DatabaseRuntime(f"sqlite:///{tmp_path}/teams.db")
    db.create_schema()
    with db.session() as session:
        session.add(
            WorkspaceOrm(
                id="analytics",
                name="Analytics",
                is_active=True,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()
    repository = CodingAgentTeamRepository(db)
    first = repository.append_revision(
        workspace_id="analytics",
        team_id=None,
        name="dbt team",
        actor_user_id="admin",
        request=_request(0),
    )
    repository.append_revision(
        workspace_id="analytics",
        team_id=None,
        name="review team",
        actor_user_id="admin",
        request=_request(0),
    )

    with pytest.raises(CodingRunError, match="coding_team_inactive"):
        repository.active_revision(workspace_id="analytics", team_id=first.team_id)
    with pytest.raises(CodingRunError, match="coding_team_name_conflict"):
        repository.append_revision(
            workspace_id="analytics",
            team_id=first.team_id,
            name="review team",
            actor_user_id="admin",
            request=_request(1),
        )

    second = repository.append_revision(
        workspace_id="analytics",
        team_id=first.team_id,
        name="dbt engineers",
        actor_user_id="admin",
        request=_request(1),
    )
    repository.activate(workspace_id="analytics", team_id=first.team_id, revision_id=first.id)
    team, active = repository.active_revision(workspace_id="analytics", team_id=first.team_id)
    detail = repository.detail(workspace_id="analytics", team_id=first.team_id)

    assert team.name == "dbt engineers"
    # A newer draft revision does not replace the activated one for new runs.
    assert active.id == first.id
    assert [revision.id for revision in detail.revisions] == [second.id, first.id]
    with pytest.raises(CodingRunError, match="coding_team_not_found"):
        repository.detail(workspace_id="other", team_id=first.team_id)
