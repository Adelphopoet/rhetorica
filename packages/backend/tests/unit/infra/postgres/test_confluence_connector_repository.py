from __future__ import annotations

from pathlib import Path

from rhetorica_backend.contracts.admin_integrations import (
    ConfluenceConnectionConfig,
    UpsertConfluenceConnectorRequest,
)
from rhetorica_backend.infra.postgres.confluence_connector_repository import (
    ConfluenceConnectorRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


def _runtime(tmp_path: Path, db_name: str) -> DatabaseRuntime:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/{db_name}")
    runtime.create_schema()
    return runtime


def test_confluence_connector_upsert_and_get_connection(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "confluence_repo.db")
    repository = ConfluenceConnectorRepository(db=runtime)

    created = repository.upsert(
        UpsertConfluenceConnectorRequest(
            name="confluence-main",
            workspace_id="ws-1",
            connection=ConfluenceConnectionConfig(
                provider="confluence",
                base_url="https://confluence.example.com",
                username="reader@example.com",
                api_token="secret-token",
                auth_type="basic",
                verify_tls=False,
                space_key="DOCS",
                page_limit=15,
            ),
        )
    )

    assert created.connection.api_token is None
    assert created.has_api_token is True
    assert created.connection.auth_type == "basic"
    assert created.connection.page_limit == 15

    connection = repository.get_connection(workspace_id="ws-1", name="confluence-main")
    assert connection is not None
    assert connection.base_url == "https://confluence.example.com"
    assert connection.username == "reader@example.com"
    assert connection.api_token == "secret-token"
    assert connection.auth_type == "basic"

    deleted = repository.delete(connector_id=created.id, workspace_id="ws-1")
    assert deleted is True
    assert repository.get_connection(workspace_id="ws-1", name="confluence-main") is None
