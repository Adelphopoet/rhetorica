from __future__ import annotations

from pathlib import Path

from rhetorica_backend.contracts.admin_integrations import (
    GitLabConnectionConfig,
    UpsertGitLabConnectorRequest,
)
from rhetorica_backend.infra.postgres.gitlab_connector_repository import (
    GitLabConnectorRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


def _runtime(tmp_path: Path, db_name: str) -> DatabaseRuntime:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/{db_name}")
    runtime.create_schema()
    return runtime


def test_gitlab_connector_upsert_masks_token_and_gets_connection(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "gitlab_repo.db")
    repository = GitLabConnectorRepository(db=runtime)

    created = repository.upsert(
        UpsertGitLabConnectorRequest(
            name="gitlab-main",
            workspace_id="ws-1",
            connection=GitLabConnectionConfig(
                provider="gitlab",
                base_url="https://gitlab.example.com/",
                api_token="secret-token",
                use_when="Use for read-only source lookup.",
                verify_tls=False,
                default_project_paths=["/group/app/", "group/app"],
                default_group_paths=["platform"],
                search_limit=10,
                read_max_tokens=900,
            ),
        )
    )

    assert created.connection.api_token is None
    assert created.has_api_token is True
    assert created.connection.base_url == "https://gitlab.example.com"
    assert created.connection.default_project_paths == ["group/app"]
    assert created.connection.search_limit == 10

    connection = repository.get_connection(workspace_id="ws-1", name="gitlab-main")
    assert connection is not None
    assert connection.api_token == "secret-token"
    assert connection.use_when == "Use for read-only source lookup."
    assert connection.verify_tls is False
    assert connection.default_group_paths == ["platform"]

    deleted = repository.delete(connector_id=created.id, workspace_id="ws-1")
    assert deleted is True
    assert repository.get_connection(workspace_id="ws-1", name="gitlab-main") is None
