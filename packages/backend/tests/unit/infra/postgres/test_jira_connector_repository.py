from __future__ import annotations

from pathlib import Path

from rhetorica_backend.contracts.admin_integrations import (
    JiraConnectionConfig,
    UpsertJiraConnectorRequest,
)
from rhetorica_backend.infra.postgres.jira_connector_repository import (
    JiraConnectorRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


def _runtime(tmp_path: Path, db_name: str) -> DatabaseRuntime:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/{db_name}")
    runtime.create_schema()
    return runtime


def test_jira_connector_upsert_and_get_connection(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "jira_repo.db")
    repository = JiraConnectorRepository(db=runtime)

    created = repository.upsert(
        UpsertJiraConnectorRequest(
            name="jira-main",
            workspace_id="ws-1",
            connection=JiraConnectionConfig(
                provider="jira",
                base_url="https://jira.example.com",
                username="analyst",
                api_token="secret-token",
                auth_type="basic",
                verify_tls=False,
                project_keys=["dm", "ops", "DM"],
                issue_limit=15,
            ),
        )
    )

    assert created.connection.api_token is None
    assert created.has_api_token is True
    assert created.connection.auth_type == "basic"
    assert created.connection.project_keys == ["DM", "OPS"]
    assert created.connection.issue_limit == 15

    connection = repository.get_connection(workspace_id="ws-1", name="jira-main")
    assert connection is not None
    assert connection.base_url == "https://jira.example.com"
    assert connection.username == "analyst"
    assert connection.api_token == "secret-token"
    assert connection.auth_type == "basic"
    assert connection.project_keys == ["DM", "OPS"]

    deleted = repository.delete(connector_id=created.id, workspace_id="ws-1")
    assert deleted is True
    assert repository.get_connection(workspace_id="ws-1", name="jira-main") is None
