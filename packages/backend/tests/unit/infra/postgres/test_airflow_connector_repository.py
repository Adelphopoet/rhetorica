from __future__ import annotations

from pathlib import Path

from rhetorica_backend.contracts.admin_integrations import (
    AirflowConnectionConfig,
    UpsertAirflowConnectorRequest,
)
from rhetorica_backend.infra.postgres.airflow_connector_repository import (
    AirflowConnectorRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.secrets import InMemorySecretStore


def _runtime(tmp_path: Path, db_name: str) -> DatabaseRuntime:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/{db_name}")
    runtime.create_schema()
    return runtime


def _request(
    *,
    name: str = "airflow-main",
    credential: str | None = "secret-password",
    is_active: bool = True,
    use_when: str = "Use for read-only Airflow inspection.",
) -> UpsertAirflowConnectorRequest:
    return UpsertAirflowConnectorRequest(
        name=name,
        workspace_id="ws-1",
        is_active=is_active,
        connection=AirflowConnectionConfig(
            provider="airflow",
            base_url="https://airflow.example.com/",
            username="airflow-user",
            password=credential,
            use_when=use_when,
            verify_tls=False,
            api_path="api/v1/",
            dag_limit=10,
            dag_run_limit=9,
            task_limit=8,
            log_max_chars=7000,
            timeout_seconds=7,
        ),
    )


def test_airflow_connector_upsert_masks_password_and_resolves_secret(tmp_path: Path) -> None:
    repository = AirflowConnectorRepository(
        db=_runtime(tmp_path, "airflow_repo.db"),
        secret_store=InMemorySecretStore(),
    )

    created = repository.upsert(_request())

    assert created.connection.password is None
    assert created.has_password is True
    assert created.connection.base_url == "https://airflow.example.com"
    assert created.connection.api_path == "/api/v1"
    assert created.connection.use_when == "Use for read-only Airflow inspection."

    connection = repository.get_connection(workspace_id="ws-1", name="airflow-main")
    assert connection is not None
    assert connection.password == "secret-password"
    assert connection.verify_tls is False
    assert connection.dag_limit == 10

    item = repository.get(workspace_id="ws-1", name="airflow-main")
    assert item is not None
    assert item.connection.password is None


def test_airflow_connector_update_preserves_secret_and_filters_inactive(
    tmp_path: Path,
) -> None:
    repository = AirflowConnectorRepository(
        db=_runtime(tmp_path, "airflow_repo_update.db"),
        secret_store=InMemorySecretStore(),
    )

    created = repository.upsert(_request())
    updated = repository.update(
        connector_id=created.id,
        workspace_id="ws-1",
        request=_request(
            name="airflow-main",
            credential=None,
            use_when="Use after update.",
        ),
    )
    repository.upsert(_request(name="airflow-disabled", is_active=False))

    assert updated is not None
    assert updated.has_password is True
    connection = repository.get_connection(workspace_id="ws-1", name="airflow-main")
    assert connection is not None
    assert connection.password == "secret-password"
    assert connection.use_when == "Use after update."
    assert repository.get_connection(workspace_id="ws-1", name="airflow-disabled") is None
    assert {item.name for item in repository.list(workspace_id="ws-1", limit=10)} == {
        "airflow-main",
        "airflow-disabled",
    }
