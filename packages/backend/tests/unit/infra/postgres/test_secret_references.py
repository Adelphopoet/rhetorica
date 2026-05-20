from __future__ import annotations

from pathlib import Path

from rhetorica_backend.contracts.admin_integrations import (
    ConfluenceConnectionConfig,
    JiraConnectionConfig,
    LlmConnectionConfig,
    OpenMetadataConnectionConfig,
    UpsertConfluenceConnectorRequest,
    UpsertJiraConnectorRequest,
    UpsertLlmConnectorRequest,
    UpsertOpenMetadataConnectorRequest,
)
from rhetorica_backend.contracts.connectors import UpsertConnectorRequest
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.infra.postgres.confluence_connector_repository import (
    ConfluenceConnectorRepository,
)
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.jira_connector_repository import JiraConnectorRepository
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.models import (
    ConfluenceConnectorConfigOrm,
    ConnectorConfigOrm,
    JiraConnectorConfigOrm,
    LlmConnectorConfigOrm,
    OpenMetadataConnectorConfigOrm,
)
from rhetorica_backend.infra.postgres.openmetadata_connector_repository import (
    OpenMetadataConnectorRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.secrets import InMemorySecretStore


def _runtime(tmp_path: Path, db_name: str) -> DatabaseRuntime:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/{db_name}")
    runtime.create_schema()
    return runtime


def test_database_connector_credentials_moved_to_secret_store(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "db_connectors.db")
    secret_store = InMemorySecretStore()
    repository = ConnectorRepository(db=runtime, secret_store=secret_store)
    test_password = "super-secret"

    created = repository.upsert(
        UpsertConnectorRequest(
            name="clickhouse-main",
            workspace_id="ws-1",
            connection=EngineConnectionConfig(
                engine="clickhouse",
                host="clickhouse.internal",
                port=8123,
                username="analyst",
                password=test_password,
                database="dm",
            ),
        )
    )

    with runtime.session() as session:
        row = session.get(ConnectorConfigOrm, created.id)
        assert row is not None
        assert row.password is None
        assert row.password_secret_ref is not None

    connection = repository.get_connection_for_execution(
        workspace_id="ws-1", name="clickhouse-main"
    )
    assert connection is not None
    assert connection.password == test_password


def test_inactive_database_connector_is_not_resolved_for_execution(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "inactive_db_connectors.db")
    repository = ConnectorRepository(db=runtime, secret_store=InMemorySecretStore())

    repository.upsert(
        UpsertConnectorRequest(
            name="clickhouse-main",
            workspace_id="ws-1",
            is_active=False,
            connection=EngineConnectionConfig(
                engine="clickhouse",
                host="clickhouse.internal",
                port=8123,
                username="analyst",
                password="secret",
                database="dm",
            ),
        )
    )

    assert (
        repository.get_connection_for_execution(workspace_id="ws-1", name="clickhouse-main") is None
    )


def test_llm_connector_credentials_moved_to_secret_store(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "llm_connectors.db")
    secret_store = InMemorySecretStore()
    repository = LlmConnectorRepository(db=runtime, secret_store=secret_store)
    test_api_key = "llm-secret"

    created = repository.upsert(
        UpsertLlmConnectorRequest(
            name="llm-main",
            workspace_id="ws-1",
            connection=LlmConnectionConfig(
                base_url="https://llm.example.com/v1",
                model="openai/gpt-4.1-mini",
                api_key=test_api_key,
            ),
        )
    )

    with runtime.session() as session:
        row = session.get(LlmConnectorConfigOrm, created.id)
        assert row is not None
        assert row.api_key is None
        assert row.api_key_secret_ref is not None

    connection = repository.get_connection(workspace_id="ws-1", name="llm-main")
    assert connection is not None
    assert connection.api_key == test_api_key


def test_inactive_llm_connector_is_not_resolved(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "inactive_llm_connectors.db")
    repository = LlmConnectorRepository(db=runtime, secret_store=InMemorySecretStore())

    repository.upsert(
        UpsertLlmConnectorRequest(
            name="llm-main",
            workspace_id="ws-1",
            is_active=False,
            connection=LlmConnectionConfig(
                base_url="https://llm.example.com/v1",
                model="openai/gpt-4.1-mini",
                api_key="llm-secret",
            ),
        )
    )

    assert repository.get_connection(workspace_id="ws-1", name="llm-main") is None


def test_llm_connector_reports_missing_secret_as_absent_key(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "llm_missing_secret.db")
    repository = LlmConnectorRepository(db=runtime, secret_store=InMemorySecretStore())

    repository.upsert(
        UpsertLlmConnectorRequest(
            name="llm-main",
            workspace_id="ws-1",
            connection=LlmConnectionConfig(
                base_url="https://llm.example.com/v1",
                model="openai/gpt-4.1-mini",
                api_key="llm-secret",
            ),
        )
    )

    repository_with_empty_store = LlmConnectorRepository(
        db=runtime,
        secret_store=InMemorySecretStore(),
    )

    item = repository_with_empty_store.get(workspace_id="ws-1", name="llm-main")
    connection = repository_with_empty_store.get_connection(
        workspace_id="ws-1",
        name="llm-main",
    )

    assert item is not None
    assert item.has_api_key is False
    assert connection is not None
    assert connection.api_key is None


def test_openmetadata_connector_credentials_moved_to_secret_store(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "openmetadata_connectors.db")
    secret_store = InMemorySecretStore()
    repository = OpenMetadataConnectorRepository(db=runtime, secret_store=secret_store)
    test_api_token = "om-secret"

    created = repository.upsert(
        UpsertOpenMetadataConnectorRequest(
            name="om-main",
            workspace_id="ws-1",
            connection=OpenMetadataConnectionConfig(
                provider="openmetadata",
                base_url="https://openmetadata.local",
                api_token=test_api_token,
            ),
        )
    )

    with runtime.session() as session:
        row = session.get(OpenMetadataConnectorConfigOrm, created.id)
        assert row is not None
        assert row.api_token is None
        assert row.api_token_secret_ref is not None

    connection = repository.get_connection(workspace_id="ws-1", name="om-main")
    assert connection is not None
    assert connection.api_token == test_api_token


def test_confluence_connector_credentials_moved_to_secret_store(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "confluence_connectors.db")
    secret_store = InMemorySecretStore()
    repository = ConfluenceConnectorRepository(db=runtime, secret_store=secret_store)
    test_api_token = "confluence-secret"

    created = repository.upsert(
        UpsertConfluenceConnectorRequest(
            name="confluence-main",
            workspace_id="ws-1",
            connection=ConfluenceConnectionConfig(
                provider="confluence",
                base_url="https://confluence.example.com",
                username="reader@example.com",
                api_token=test_api_token,
                auth_type="bearer",
                verify_tls=True,
            ),
        )
    )

    with runtime.session() as session:
        row = session.get(ConfluenceConnectorConfigOrm, created.id)
        assert row is not None
        assert row.api_token is None
        assert row.api_token_secret_ref is not None

    connection = repository.get_connection(workspace_id="ws-1", name="confluence-main")
    assert connection is not None
    assert connection.api_token == test_api_token


def test_confluence_connector_reports_missing_secret_as_absent_token(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "confluence_missing_secret.db")
    repository = ConfluenceConnectorRepository(db=runtime, secret_store=InMemorySecretStore())

    repository.upsert(
        UpsertConfluenceConnectorRequest(
            name="confluence-main",
            workspace_id="ws-1",
            connection=ConfluenceConnectionConfig(
                provider="confluence",
                base_url="https://confluence.example.com",
                username="reader@example.com",
                api_token="confluence-secret",
                auth_type="bearer",
                verify_tls=True,
            ),
        )
    )

    repository_with_empty_store = ConfluenceConnectorRepository(
        db=runtime,
        secret_store=InMemorySecretStore(),
    )

    item = repository_with_empty_store.get(workspace_id="ws-1", name="confluence-main")
    connection = repository_with_empty_store.get_connection(
        workspace_id="ws-1",
        name="confluence-main",
    )

    assert item is not None
    assert item.has_api_token is False
    assert connection is not None
    assert connection.api_token is None


def test_jira_connector_credentials_moved_to_secret_store(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "jira_connectors.db")
    secret_store = InMemorySecretStore()
    repository = JiraConnectorRepository(db=runtime, secret_store=secret_store)
    test_api_token = "jira-secret"

    created = repository.upsert(
        UpsertJiraConnectorRequest(
            name="jira-main",
            workspace_id="ws-1",
            connection=JiraConnectionConfig(
                provider="jira",
                base_url="https://jira.example.com",
                username="analyst",
                api_token=test_api_token,
                auth_type="basic",
                verify_tls=True,
                project_keys=["DM"],
                issue_limit=12,
            ),
        )
    )

    with runtime.session() as session:
        row = session.get(JiraConnectorConfigOrm, created.id)
        assert row is not None
        assert row.api_token is None
        assert row.api_token_secret_ref is not None

    connection = repository.get_connection(workspace_id="ws-1", name="jira-main")
    assert connection is not None
    assert connection.api_token == test_api_token
