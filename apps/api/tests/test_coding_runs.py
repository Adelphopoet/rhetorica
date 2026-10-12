from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from rhetorica_api.app.routes import coding_runs
from rhetorica_backend.contracts.coding import CreateCodingRunRequest


def request(**overrides):
    return CreateCodingRunRequest.model_validate({
        "task": "Implement net_revenue",
        "developer_agent_id": "dev",
        "qa_agent_id": "qa",
        "gitlab_connector_id": "gitlab",
        "project_id": "group/project",
        "target_branch": "main",
        "environment_profile_id": "env",
        **overrides,
    })


@pytest.mark.parametrize(
    "credential", ["personal_access_token", "oauth_access_token", "mattermost", "mcp"]
)
@pytest.mark.parametrize("operation", ["create", "read", "cancel", "capabilities", "plan", "confirm"])
def test_external_channels_reject_before_runtime_composition(monkeypatch, credential, operation):
    monkeypatch.setattr(
        coding_runs, "get_current_auth_context", lambda: SimpleNamespace(credential_type=credential)
    )
    compose = Mock(side_effect=AssertionError("must not compose runtime"))
    monkeypatch.setattr(coding_runs, "service", compose)
    monkeypatch.setattr(coding_runs, "planning_service", compose)
    with pytest.raises(HTTPException) as exc:
        if operation == "create":
            coding_runs.create(request(), "key")
        elif operation == "capabilities":
            coding_runs.capabilities()
        elif operation == "plan":
            coding_runs.coding_plan("chat")
        elif operation == "confirm":
            coding_runs.confirm_coding_plan("chat", "plan")
        else:
            getattr(coding_runs, operation)("run")
    assert exc.value.status_code == 403
    compose.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "web_chat"),
        ("workspace_id", "other"),
        ("actor_user_id", "other"),
        ("base_sha", "a" * 40),
        ("max_iterations", 100),
    ],
)
def test_client_cannot_supply_server_owned_admission_context(field, value):
    with pytest.raises(ValidationError):
        request(**{field: value})


def test_active_workspace_comes_from_authenticated_server_context(monkeypatch):
    user = Mock()
    auth = SimpleNamespace(
        credential_type="session", user=user, active_workspace_id="owned-workspace"
    )
    monkeypatch.setattr(coding_runs, "get_current_auth_context", lambda: auth)
    assert coding_runs.actor() == user.for_workspace.return_value
    user.for_workspace.assert_called_once_with("owned-workspace")


def test_read_forwards_owner_scoped_event_cursor(monkeypatch):
    user, runtime = Mock(), Mock()
    monkeypatch.setattr(coding_runs, "actor", lambda: user)
    monkeypatch.setattr(coding_runs, "service", lambda: runtime)
    coding_runs.read("run", after=200)
    runtime.view.assert_called_once_with("run", user, after=200)


def test_event_stream_reports_view_failure_in_band(monkeypatch):
    import asyncio

    from rhetorica_backend.domain.coding.errors import CodingRunError

    user, runtime = Mock(), Mock()
    runtime.view.side_effect = [None, CodingRunError("workspace_access_denied", status_code=403)]
    monkeypatch.setattr(coding_runs, "actor", lambda: user)
    monkeypatch.setattr(coding_runs, "service", lambda: runtime)

    class Connected:
        async def is_disconnected(self):
            return False

    async def collect():
        response = await coding_runs.events("run", Connected(), after=0, last_event_id=None)
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(collect())
    assert chunks == ['event: error\ndata: {"code": "workspace_access_denied"}\n\n']
