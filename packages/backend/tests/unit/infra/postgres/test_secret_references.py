from __future__ import annotations

import json
from pathlib import Path

import pytest
from rhetorica_backend.contracts.admin_integrations import (
    LlmConnectionConfig,
    UpsertLlmConnectorRequest,
)
from rhetorica_backend.contracts.connectors import UpsertConnectorRequest
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.infra.postgres.chat_models import ChatLlmCredentialBindingOrm
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.llm_credential_binding_repository import (
    LlmCredentialBindingRepository,
)
from rhetorica_backend.infra.postgres.models import (
    ConnectorConfigOrm,
    LlmConnectorConfigOrm,
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


def test_trino_extra_credentials_are_redacted_rotated_and_removed(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "trino_extra_credentials.db")
    secret_store = InMemorySecretStore()
    repository = ConnectorRepository(db=runtime, secret_store=secret_store)

    created = repository.upsert(
        UpsertConnectorRequest(
            name="trino-dg",
            workspace_id="ws-1",
            connection=EngineConnectionConfig(
                engine="trino",
                host="trino.internal",
                port=443,
                username="svc-trino",
                catalog="iceberg_dg",
                schema="iceberg_dg",
                secure=True,
                extra_credentials={
                    "td_user": "svc-teradata",
                    "td_password": "teradata-secret",
                },
            ),
        )
    )

    assert created.has_extra_credentials is True
    assert created.connection.extra_credentials == {
        "td_password": None,
        "td_user": None,
    }
    with runtime.session() as session:
        row = session.get(ConnectorConfigOrm, created.id)
        assert row is not None
        refs = dict(row.extra_credential_secret_refs)
        assert set(refs) == {"td_user", "td_password"}
        assert "teradata-secret" not in str(row.extra_settings)

    resolved = repository.get_connection_for_execution(workspace_id="ws-1", name="trino-dg")
    assert resolved is not None
    assert resolved.extra_credentials == {
        "td_password": "teradata-secret",
        "td_user": "svc-teradata",
    }

    updated = repository.update(
        connector_id=created.id,
        workspace_id="ws-1",
        request=UpsertConnectorRequest(
            name="trino-dg",
            workspace_id="ws-1",
            connection=EngineConnectionConfig(
                engine="trino",
                host="trino.internal",
                port=443,
                username="svc-trino",
                catalog="iceberg_dg",
                schema="iceberg_dg",
                secure=True,
                extra_credentials={
                    "td_user": None,
                    "td_password": "rotated-secret",
                },
            ),
        ),
    )
    assert updated is not None
    assert updated.connection.extra_credentials == {
        "td_password": None,
        "td_user": None,
    }
    resolved = repository.get_connection_for_execution(workspace_id="ws-1", name="trino-dg")
    assert resolved is not None
    assert resolved.extra_credentials == {
        "td_password": "rotated-secret",
        "td_user": "svc-teradata",
    }

    removed = repository.update(
        connector_id=created.id,
        workspace_id="ws-1",
        request=UpsertConnectorRequest(
            name="trino-dg",
            workspace_id="ws-1",
            connection=EngineConnectionConfig(
                engine="trino",
                host="trino.internal",
                port=443,
                username="svc-trino",
                catalog="iceberg_dg",
                schema="iceberg_dg",
                secure=True,
                extra_credentials={"td_user": None},
            ),
        ),
    )
    assert removed is not None
    assert removed.connection.extra_credentials == {"td_user": None}
    assert secret_store.get_secret("ws-1", refs["td_password"]) is None


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
    assert connection.api_keys == [test_api_key]


def test_llm_connector_stores_multiple_api_keys(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "llm_multi_keys.db")
    secret_store = InMemorySecretStore()
    repository = LlmConnectorRepository(db=runtime, secret_store=secret_store)

    created = repository.upsert(
        UpsertLlmConnectorRequest(
            name="llm-main",
            workspace_id="ws-1",
            connection=LlmConnectionConfig(
                base_url="https://llm.example.com/v1",
                model="openai/gpt-4.1-mini",
                api_keys=["llm-secret-1", "llm-secret-2", "llm-secret-1"],
            ),
        )
    )

    with runtime.session() as session:
        row = session.get(LlmConnectorConfigOrm, created.id)
        assert row is not None
        assert row.api_key is None
        assert row.api_key_secret_ref is not None

    item = repository.get(workspace_id="ws-1", name="llm-main")
    connection = repository.get_connection(workspace_id="ws-1", name="llm-main")

    assert item is not None
    assert item.has_api_key is True
    assert item.api_key_count == 2
    assert connection is not None
    assert connection.api_key == "llm-secret-1"
    assert connection.api_keys == ["llm-secret-1", "llm-secret-2"]


def test_public_connector_and_binding_state_do_not_expose_llm_api_keys(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "llm_secret_redaction.db")
    secret_store = InMemorySecretStore()
    repository = LlmConnectorRepository(db=runtime, secret_store=secret_store)
    first_secret = "llm-secret-a-sensitive"
    second_secret = "llm-secret-b-sensitive"
    connector = repository.upsert(
        UpsertLlmConnectorRequest(
            name="llm-main",
            workspace_id="ws-1",
            connection=LlmConnectionConfig(
                base_url="https://llm.example.com/v1",
                model="openai/gpt-4.1-mini",
                api_keys=[first_secret, second_secret],
            ),
        )
    )
    binding_repository = LlmCredentialBindingRepository(runtime)
    binding_repository.resolve(
        conversation_id="conversation-1",
        llm_connector_id=connector.id,
        credential_set_version=1,
        credential_count=2,
    )

    public_item = repository.get(workspace_id="ws-1", name="llm-main")
    assert public_item is not None
    public_payload = json.dumps(public_item.model_dump(mode="json"))
    assert first_secret not in public_payload
    assert second_secret not in public_payload

    binding_columns = ChatLlmCredentialBindingOrm.__table__.columns
    assert "api_key" not in binding_columns
    assert "api_keys" not in binding_columns
    assert "authorization" not in binding_columns
    with runtime.session() as session:
        binding = session.get(
            ChatLlmCredentialBindingOrm,
            {"conversation_id": "conversation-1", "llm_connector_id": connector.id},
        )
        assert binding is not None
        assert first_secret not in str(binding.__dict__)
        assert second_secret not in str(binding.__dict__)


def test_llm_connector_key_set_change_increments_credential_version(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "llm_credential_versions.db")
    repository = LlmConnectorRepository(db=runtime, secret_store=InMemorySecretStore())

    created = repository.upsert(
        UpsertLlmConnectorRequest(
            name="llm-main",
            workspace_id="ws-1",
            connection=LlmConnectionConfig(
                base_url="https://llm.example.com/v1",
                model="openai/gpt-4.1-mini",
                api_keys=["llm-secret-1", "llm-secret-2"],
            ),
        )
    )
    with runtime.session() as session:
        assert session.get(LlmConnectorConfigOrm, created.id).credential_set_version == 1

    repository.update(
        connector_id=created.id,
        workspace_id="ws-1",
        request=UpsertLlmConnectorRequest(
            name="llm-main",
            workspace_id="ws-1",
            connection=LlmConnectionConfig(
                base_url="https://llm.example.com/v1",
                model="openai/gpt-4.1-mini",
                api_keys=["llm-secret-2", "llm-secret-1"],
            ),
        ),
    )

    resolution = repository.get_connection_resolution(workspace_id="ws-1", name="llm-main")
    assert resolution is not None
    assert resolution.credential_set_version == 2
    assert resolution.connection.connector_id == created.id


def test_llm_connector_commit_failure_compensates_secret_write(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, "llm_commit_failure.db")
    secret_store = InMemorySecretStore()
    repository = LlmConnectorRepository(db=runtime, secret_store=secret_store)
    monkeypatch.setattr(
        "rhetorica_backend.infra.postgres.llm_connector_repository.uuid4",
        lambda: "connector-1",
    )

    session_factory = runtime._session_factory

    class _FailingSession:
        def __init__(self) -> None:
            self._session = session_factory()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self._session.close()

        def __getattr__(self, name):
            return getattr(self._session, name)

        def commit(self) -> None:
            raise RuntimeError("commit failed")

    monkeypatch.setattr(runtime, "session", _FailingSession)
    with pytest.raises(RuntimeError, match="commit failed"):
        repository.upsert(
            UpsertLlmConnectorRequest(
                name="llm-main",
                workspace_id="ws-1",
                connection=LlmConnectionConfig(
                    base_url="https://llm.example.com/v1",
                    model="openai/gpt-4.1-mini",
                    api_key="temporary-secret",
                ),
            )
        )

    assert secret_store.get_secret("ws-1", "llm_connector/ws-1/connector-1/api_key") is None


def test_llm_connector_update_retains_existing_keys_and_appends_new(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "llm_append_keys.db")
    repository = LlmConnectorRepository(db=runtime, secret_store=InMemorySecretStore())

    created = repository.upsert(
        UpsertLlmConnectorRequest(
            name="llm-main",
            workspace_id="ws-1",
            connection=LlmConnectionConfig(
                base_url="https://llm.example.com/v1",
                model="openai/gpt-4.1-mini",
                api_keys=["llm-secret-1", "llm-secret-2"],
            ),
        )
    )

    updated = repository.update(
        connector_id=created.id,
        workspace_id="ws-1",
        request=UpsertLlmConnectorRequest(
            name="llm-main",
            workspace_id="ws-1",
            connection=LlmConnectionConfig(
                base_url="https://llm.example.com/v1",
                model="openai/gpt-4.1-mini",
                retained_api_key_indexes=[0],
                api_keys=["llm-secret-3"],
            ),
        ),
    )
    connection = repository.get_connection(workspace_id="ws-1", name="llm-main")

    assert updated is not None
    assert updated.api_key_count == 2
    assert connection is not None
    assert connection.api_keys == ["llm-secret-1", "llm-secret-3"]


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
