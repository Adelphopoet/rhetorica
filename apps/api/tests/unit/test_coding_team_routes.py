from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import coding_runs
from rhetorica_backend.domain.coding.errors import CodingTeamValidationError
from rhetorica_backend.domain.coding.team_service import CodingAgentTeamService
from rhetorica_backend.infra.postgres.coding_team_repository import CodingAgentTeamRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.workspace_models import WorkspaceOrm

PAYLOAD = {
    "expected_revision": 0,
    "name": "dbt team",
    "coordinator_agent_profile_id": "coordinator",
    "coordinator_instruction": "Prefer small changes.",
    "members": [
        {
            "id": "engineer",
            "agent_profile_id": "developer",
            "label": "Data engineer",
            "instruction": "Check dbt changes.",
            "capability": "candidate",
            "skill_version_bindings": [{"skill_id": "dbt-core", "version_digest": "v1"}],
        },
        {"id": "reviewer", "agent_profile_id": "qa", "label": "Reviewer", "capability": "review"},
    ],
}


def _client(tmp_path: Path, monkeypatch, *, admin: bool = True):
    db = DatabaseRuntime(f"sqlite:///{tmp_path}/coding_teams.db")
    db.create_schema()
    with db.session() as session:
        now = datetime.now(UTC)
        session.add(
            WorkspaceOrm(
                id="analytics", name="Analytics", is_active=True, created_at=now, updated_at=now
            )
        )
        session.commit()
    validator = Mock()
    service = CodingAgentTeamService(
        coding_service=SimpleNamespace(
            require_actor=Mock(return_value=SimpleNamespace(is_admin=admin))
        ),
        repository=CodingAgentTeamRepository(db),
        validator=validator,
    )
    monkeypatch.setattr(coding_runs, "teams_service", lambda: service)
    monkeypatch.setattr(
        coding_runs, "actor", lambda: SimpleNamespace(id="admin", workspace_id="analytics")
    )
    return TestClient(create_app()), validator


def test_admin_creates_activates_and_lists_team_revisions(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path, monkeypatch)

    created = client.post("/v1/coding-agent-teams", json=PAYLOAD)
    assert created.status_code == 201
    revision = created.json()
    assert revision["revision"] == 1 and revision["is_active"] is False

    activated = client.post(
        f"/v1/coding-agent-teams/{revision['team_id']}/revisions/{revision['id']}/activate"
    )
    assert activated.status_code == 200
    assert activated.json()["active_revision_id"] == revision["id"]

    second = client.post(
        "/v1/coding-agent-teams",
        json={**PAYLOAD, "team_id": revision["team_id"], "expected_revision": 1, "name": "dbt"},
    )
    assert second.status_code == 201

    listed = client.get("/v1/coding-agent-teams").json()
    assert listed[0]["team"]["name"] == "dbt"
    assert [item["revision"] for item in listed[0]["revisions"]] == [2, 1]
    assert listed[0]["team"]["active_revision_id"] == revision["id"]
    detail = client.get(f"/v1/coding-agent-teams/{revision['team_id']}").json()
    assert detail["revisions"][1]["members"][0]["label"] == "Data engineer"


def test_stale_revision_and_duplicate_name_conflict(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    first = client.post("/v1/coding-agent-teams", json=PAYLOAD).json()

    stale = client.post(
        "/v1/coding-agent-teams",
        json={**PAYLOAD, "team_id": first["team_id"], "expected_revision": 0},
    )
    duplicate = client.post("/v1/coding-agent-teams", json=PAYLOAD)

    assert (stale.status_code, stale.json()["detail"]) == (409, "coding_team_revision_conflict")
    assert (duplicate.status_code, duplicate.json()["detail"]) == (409, "coding_team_name_conflict")


def test_validation_error_names_the_member_and_saves_nothing(tmp_path: Path, monkeypatch) -> None:
    client, validator = _client(tmp_path, monkeypatch)
    validator.validate.side_effect = CodingTeamValidationError(
        "coding_team_member_gitlab_grant_missing", member_id="reviewer", member_label="Reviewer"
    )

    response = client.post("/v1/coding-agent-teams", json=PAYLOAD)

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "coding_team_member_gitlab_grant_missing",
        "member_id": "reviewer",
        "member_label": "Reviewer",
    }
    assert client.get("/v1/coding-agent-teams").json() == []


def test_duplicate_member_is_rejected_by_contract(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    members = [PAYLOAD["members"][0], {**PAYLOAD["members"][1], "id": "engineer"}]

    response = client.post("/v1/coding-agent-teams", json={**PAYLOAD, "members": members})

    assert response.status_code == 422


def test_non_admin_cannot_read_or_change_teams(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path, monkeypatch, admin=False)

    created = client.post("/v1/coding-agent-teams", json=PAYLOAD)
    listed = client.get("/v1/coding-agent-teams")
    detail = client.get("/v1/coding-agent-teams/team-1")

    for response in (created, listed, detail):
        assert (response.status_code, response.json()["detail"]) == (403, "admin_access_required")
