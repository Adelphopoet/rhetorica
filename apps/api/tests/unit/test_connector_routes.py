from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import confluence_connectors as confluence_connector_routes
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.routes import openmetadata_connectors as openmetadata_connector_routes
from rhetorica_api.app.settings import settings


def _client(tmp_path: Path) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_connectors.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    confluence_connector_routes._repository.cache_clear()
    openmetadata_connector_routes._repository.cache_clear()
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    app = create_app()
    return TestClient(app)


def test_connector_upsert_list_and_delete(tmp_path: Path) -> None:
    client = _client(tmp_path)

    create_response = client.post(
        "/v1/admin/connectors",
        json={
            "name": "clickhouse-prod",
            "workspace_id": "default",
            "connection": {
                "engine": "clickhouse",
                "host": "example.clickhouse.internal",
                "port": 8123,
                "username": "analyst",
                "password": "secret",
                "database": "dm",
                "secure": False,
            },
        },
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["name"] == "clickhouse-prod"
    assert created["has_password"] is True
    assert created["connection"]["password"] is None
    connector_id = created["id"]

    list_response = client.get("/v1/admin/connectors", params={"workspace_id": "default"})
    assert list_response.status_code == 200
    rows = list_response.json()
    assert len(rows) == 1
    assert rows[0]["id"] == connector_id

    update_response = client.put(
        f"/v1/admin/connectors/{connector_id}",
        params={"workspace_id": "default"},
        json={
            "name": "clickhouse-prod-updated",
            "workspace_id": "default",
            "connection": {
                "engine": "clickhouse",
                "host": "new-host.clickhouse.internal",
                "port": 8443,
                "username": "analyst",
                "password": None,
                "database": "dm",
                "secure": True,
            },
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["id"] == connector_id
    assert updated["name"] == "clickhouse-prod-updated"
    assert updated["connection"]["host"] == "new-host.clickhouse.internal"
    assert updated["connection"]["port"] == 8443
    assert updated["has_password"] is True

    delete_response = client.delete(
        f"/v1/admin/connectors/{connector_id}", params={"workspace_id": "default"}
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True

    final_list = client.get("/v1/admin/connectors", params={"workspace_id": "default"})
    assert final_list.status_code == 200
    assert final_list.json() == []


def test_connector_accepts_greenplum_engine(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/admin/connectors",
        json={
            "name": "greenplum-prod",
            "workspace_id": "default",
            "connection": {
                "engine": "greenplum",
                "host": "greenplum.internal",
                "port": 6432,
                "username": "analyst",
                "password": "secret",
                "database": "dwh",
                "schema": "dm",
                "secure": True,
                "verify": False,
                "extra_settings": {
                    "metadata_service": "greenplum-prod",
                    "metadata_database": "analytics",
                    "metadata_schema": "dm",
                    "connect_timeout": 30,
                },
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["connection"]["engine"] == "greenplum"
    assert payload["connection"]["host"] == "greenplum.internal"
    assert payload["connection"]["schema"] == "dm"
    assert payload["has_password"] is True


def test_connector_accepts_impala_ldap_settings(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/admin/connectors",
        json={
            "name": "impala-prod",
            "workspace_id": "default",
            "connection": {
                "engine": "impala",
                "host": "10.234.176.50",
                "port": 21050,
                "username": "analyst_user",
                "password": "secret",
                "schema": "default",
                "secure": True,
                "verify": False,
                "extra_settings": {
                    "auth_type": "LDAP",
                    "use_http_transport": False,
                    "retries": 1,
                    "metadata_service": "impala-prod",
                    "metadata_database": "default",
                },
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["connection"]["engine"] == "impala"
    assert payload["connection"]["host"] == "10.234.176.50"
    assert payload["connection"]["schema"] == "default"
    assert payload["connection"]["extra_settings"]["auth_type"] == "LDAP"
    assert payload["connection"]["extra_settings"]["retries"] == 1
    assert payload["has_password"] is True
