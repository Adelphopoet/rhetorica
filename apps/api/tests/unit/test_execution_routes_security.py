"""Security tests for execution routes.

These tests verify that the public API endpoints properly reject raw connection
config and enforce governed connector resolution.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.identity import resolve_current_user
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import (
    connectors as connector_routes,
)
from rhetorica_api.app.routes import (
    execution as execution_routes,
)
from rhetorica_api.app.routes import (
    llm_connectors as llm_connector_routes,
)
from rhetorica_api.app.settings import settings


class _FakeAsyncResult:
    def __init__(self, *, ready: bool = False, result: object = None) -> None:
        self._ready = ready
        self.result = result
        self.revoked = False

    def ready(self) -> bool:
        return self._ready

    def revoke(self, terminate: bool = False) -> None:
        _ = terminate
        self.revoked = True


class _FakeCeleryApp:
    def __init__(self) -> None:
        self.sent_tasks: list[dict[str, object]] = []
        self.results: dict[str, _FakeAsyncResult] = {}

    def send_task(
        self,
        name: str,
        kwargs: dict[str, object],
        task_id: str,
    ) -> _FakeAsyncResult:
        self.sent_tasks.append({"name": name, "kwargs": kwargs, "task_id": task_id})
        result = self.results.setdefault(task_id, _FakeAsyncResult())
        return result

    def AsyncResult(self, task_id: str) -> _FakeAsyncResult:
        return self.results.setdefault(task_id, _FakeAsyncResult())


def _client(tmp_path: Path, fake_celery: _FakeCeleryApp) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_security_tests.db"
    execution_routes._runtime.cache_clear()
    clear_celery_cache = getattr(execution_routes._celery_app, "cache_clear", None)
    if callable(clear_celery_cache):
        clear_celery_cache()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    execution_routes._celery_app = lambda: fake_celery  # type: ignore[assignment]
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    app = create_app()
    return TestClient(app)


class TestExecuteSqlSecurityValidation:
    """Test that ExternalSqlExecutionRequest rejects raw connection config."""

    def test_external_contract_rejects_unknown_field_title(self, tmp_path: Path) -> None:
        """POST with 'title' in body returns 422 (model validation rejects unknown fields)."""
        client = _client(tmp_path, _FakeCeleryApp())

        response = client.post(
            "/v1/sql/execute",
            json={
                "sql": "SELECT 1",
                "title": "malicious request",
            },
        )

        assert response.status_code == 422

    def test_external_contract_does_not_accept_connection_field(self, tmp_path: Path) -> None:
        """POST with 'connection' in body returns 422.

        The ExternalSqlExecutionRequest contract does not have a connection field,
        so it should reject requests attempting to submit raw connection config.
        """
        client = _client(tmp_path, _FakeCeleryApp())

        response = client.post(
            "/v1/sql/execute",
            json={
                "sql": "SELECT 1",
                "connection": {
                    "engine": "clickhouse",
                    "host": "malicious-host.example.com",
                    "port": 8123,
                    "username": "attacker",
                    "password": "secret",
                },
            },
        )

        assert response.status_code == 422
        assert "connection" in response.text.lower()

    def test_external_contract_does_not_accept_host_field(self, tmp_path: Path) -> None:
        """POST with 'host' in body returns 422.

        Even direct host field should be rejected as it could be used to
        bypass the governed connector policy.
        """
        client = _client(tmp_path, _FakeCeleryApp())

        response = client.post(
            "/v1/sql/execute",
            json={
                "sql": "SELECT 1",
                "host": "malicious-host.example.com",
            },
        )

        assert response.status_code == 422


class TestConnectorResolution:
    """Test connector resolution for external requests."""

    def test_execute_sql_with_no_connector_and_no_connector_name_returns_404(
        self, tmp_path: Path
    ) -> None:
        """When no connector exists, returns 404 with clear message."""
        client = _client(tmp_path, _FakeCeleryApp())

        response = client.post(
            "/v1/sql/execute",
            json={
                "sql": "SELECT 1",
            },
        )

        assert response.status_code == 404
        assert "No database connector configured" in response.json()["detail"]

    def test_execute_sql_with_specific_connector_name_not_found_returns_404(
        self, tmp_path: Path
    ) -> None:
        """When requested connector doesn't exist, returns 404 with clear message."""
        client = _client(tmp_path, _FakeCeleryApp())

        response = client.post(
            "/v1/sql/execute",
            json={
                "sql": "SELECT 1",
                "connector_name": "nonexistent-connector",
            },
        )

        assert response.status_code == 404
        assert "nonexistent-connector" in response.json()["detail"]
        assert "not found" in response.json()["detail"]

    def test_execute_sql_with_named_connector_works(self, tmp_path: Path) -> None:
        """When a connector exists, named connector resolution works."""
        client = _client(tmp_path, _FakeCeleryApp())

        # First, create a connector via the API
        client.post(
            "/v1/admin/connectors",
            json={
                "name": "test-connector",
                "workspace_id": "default",
                "connection": {
                    "engine": "clickhouse",
                    "host": "localhost",
                    "port": 8123,
                    "username": "default",
                    "database": "default",
                },
            },
        )

        # Now the execute should work (will be blocked by guard, but not 404)
        response = client.post(
            "/v1/sql/execute",
            json={
                "sql": "INSERT INTO t SELECT 1",  # Write SQL will be blocked
                "connector_name": "test-connector",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "blocked"

    def test_execute_sql_without_connector_name_uses_first_available(self, tmp_path: Path) -> None:
        """When no connector_name specified, uses first available connector."""
        client = _client(tmp_path, _FakeCeleryApp())

        # Create a connector
        client.post(
            "/v1/admin/connectors",
            json={
                "name": "primary-connector",
                "workspace_id": "default",
                "connection": {
                    "engine": "clickhouse",
                    "host": "localhost",
                    "port": 8123,
                    "username": "default",
                    "database": "default",
                },
            },
        )

        # Execute without specifying connector_name - should use first available
        response = client.post(
            "/v1/sql/execute",
            json={
                "sql": "INSERT INTO t SELECT 1",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "blocked"


class TestSubmitExecutionSecurity:
    """Test /v1/sql/executions endpoint security."""

    def test_submit_execution_rejects_raw_connection_config(self, tmp_path: Path) -> None:
        """POST to /executions with 'connection' returns 422."""
        client = _client(tmp_path, _FakeCeleryApp())

        response = client.post(
            "/v1/sql/executions",
            json={
                "sql": "SELECT 1",
                "connection": {
                    "engine": "clickhouse",
                    "host": "malicious-host.example.com",
                    "port": 8123,
                    "username": "attacker",
                },
            },
        )

        assert response.status_code == 422

    def test_submit_execution_with_no_connector_returns_404(self, tmp_path: Path) -> None:
        """When no connector exists, /executions returns 404."""
        client = _client(tmp_path, _FakeCeleryApp())

        response = client.post(
            "/v1/sql/executions",
            json={
                "sql": "SELECT 1",
            },
        )

        assert response.status_code == 404
        assert "No database connector configured" in response.json()["detail"]

    def test_submit_execution_with_valid_connector_queues_successfully(
        self, tmp_path: Path
    ) -> None:
        """When connector exists, queuing succeeds."""
        fake_celery = _FakeCeleryApp()
        client = _client(tmp_path, fake_celery)

        # Create a connector
        client.post(
            "/v1/admin/connectors",
            json={
                "name": "queue-test-connector",
                "workspace_id": "default",
                "connection": {
                    "engine": "clickhouse",
                    "host": "localhost",
                    "port": 8123,
                    "username": "default",
                    "database": "default",
                },
            },
        )

        response = client.post(
            "/v1/sql/executions",
            json={
                "sql": "SELECT 1",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "queued"
        assert len(fake_celery.sent_tasks) == 1


class TestActorIdBinding:
    """Test actor_id binding through the execution pipeline."""

    def test_actor_id_persisted_on_queued_creation(self, tmp_path: Path) -> None:
        """Actor ID flows through submit -> service -> repository."""
        fake_celery = _FakeCeleryApp()
        client = _client(tmp_path, fake_celery)
        owner_id = resolve_current_user("default").id

        # Create a connector
        client.post(
            "/v1/admin/connectors",
            json={
                "name": "actor-test-connector",
                "workspace_id": "default",
                "connection": {
                    "engine": "clickhouse",
                    "host": "localhost",
                    "port": 8123,
                    "username": "default",
                    "database": "default",
                },
            },
        )

        response = client.post(
            "/v1/sql/executions",
            json={
                "sql": "SELECT 1",
                "actor_id": owner_id,
            },
        )

        assert response.status_code == 200
        execution_id = response.json()["execution_id"]

        # Verify actor_id is persisted
        history = client.get(
            f"/v1/sql/executions/{execution_id}",
        )
        assert history.status_code == 200
        assert history.json()["history"]["actor_id"] == owner_id

    def test_execution_detail_uses_authenticated_owner(self, tmp_path: Path) -> None:
        """Owner can read their execution without a client-supplied actor id."""
        fake_celery = _FakeCeleryApp()
        client = _client(tmp_path, fake_celery)
        owner_id = resolve_current_user("default").id

        # Create a connector
        client.post(
            "/v1/admin/connectors",
            json={
                "name": "acl-test-connector",
                "workspace_id": "default",
                "connection": {
                    "engine": "clickhouse",
                    "host": "localhost",
                    "port": 8123,
                    "username": "default",
                    "database": "default",
                },
            },
        )

        response = client.post(
            "/v1/sql/executions",
            json={
                "sql": "SELECT 1",
                "actor_id": owner_id,
            },
        )
        execution_id = response.json()["execution_id"]

        detail = client.get(f"/v1/sql/executions/{execution_id}")
        assert detail.status_code == 200
        assert detail.json()["history"]["actor_id"] == owner_id

    def test_execution_detail_rejected_for_foreign_actor(self, tmp_path: Path) -> None:
        """Actor cannot see another user's execution record."""
        fake_celery = _FakeCeleryApp()
        client = _client(tmp_path, fake_celery)

        # Create a connector
        client.post(
            "/v1/admin/connectors",
            json={
                "name": "acl-test-connector-2",
                "workspace_id": "default",
                "connection": {
                    "engine": "clickhouse",
                    "host": "localhost",
                    "port": 8123,
                    "username": "default",
                    "database": "default",
                },
            },
        )

        response = client.post(
            "/v1/sql/executions",
            json={
                "sql": "SELECT 1",
                "actor_id": "owner-user-2",
            },
        )
        execution_id = response.json()["execution_id"]

        # Supplying the stored actor id cannot spoof authenticated ownership.
        detail = client.get(
            f"/v1/sql/executions/{execution_id}",
            params={"actor_id": "owner-user-2"},
        )
        assert detail.status_code == 403
        assert "Access denied" in detail.json()["detail"]

    def test_execution_detail_requires_auth(self, tmp_path: Path) -> None:
        """Authenticated identity cannot read a foreign protected execution."""
        fake_celery = _FakeCeleryApp()
        client = _client(tmp_path, fake_celery)

        # Create a connector
        client.post(
            "/v1/admin/connectors",
            json={
                "name": "acl-test-connector-3",
                "workspace_id": "default",
                "connection": {
                    "engine": "clickhouse",
                    "host": "localhost",
                    "port": 8123,
                    "username": "default",
                    "database": "default",
                },
            },
        )

        response = client.post(
            "/v1/sql/executions",
            json={
                "sql": "SELECT 1",
                "actor_id": "auth-required-user",
            },
        )
        execution_id = response.json()["execution_id"]

        detail = client.get(f"/v1/sql/executions/{execution_id}")
        assert detail.status_code == 403
        assert "Access denied" in detail.json()["detail"]

    def test_legacy_execution_without_actor_remains_readable(self, tmp_path: Path) -> None:
        fake_celery = _FakeCeleryApp()
        client = _client(tmp_path, fake_celery)
        client.post(
            "/v1/admin/connectors",
            json={
                "name": "legacy-execution-connector",
                "workspace_id": "default",
                "connection": {
                    "engine": "clickhouse",
                    "host": "localhost",
                    "port": 8123,
                    "username": "default",
                    "database": "default",
                },
            },
        )
        response = client.post("/v1/sql/executions", json={"sql": "SELECT 1"})

        detail = client.get(f"/v1/sql/executions/{response.json()['execution_id']}")

        assert detail.status_code == 200
        assert detail.json()["history"]["actor_id"] is None

    def test_list_filtered_by_actor_id(self, tmp_path: Path) -> None:
        """List returns only actor's records when actor_id is specified."""
        fake_celery = _FakeCeleryApp()
        client = _client(tmp_path, fake_celery)

        # Create a connector
        client.post(
            "/v1/admin/connectors",
            json={
                "name": "list-test-connector",
                "workspace_id": "default",
                "connection": {
                    "engine": "clickhouse",
                    "host": "localhost",
                    "port": 8123,
                    "username": "default",
                    "database": "default",
                },
            },
        )

        # Create executions from different actors
        for actor in ["actor-X", "actor-Y", "actor-X"]:
            client.post(
                "/v1/sql/executions",
                json={
                    "sql": "SELECT 1",
                    "actor_id": actor,
                },
            )

        # List all
        all_history = client.get("/v1/sql/executions")
        assert all_history.status_code == 200
        assert len(all_history.json()) == 3

        # Filter by actor-X
        actor_x_history = client.get("/v1/sql/executions", params={"actor_id": "actor-X"})
        assert actor_x_history.status_code == 200
        rows = actor_x_history.json()
        assert len(rows) == 2
        assert all(r["actor_id"] == "actor-X" for r in rows)

        # Filter by actor-Y
        actor_y_history = client.get("/v1/sql/executions", params={"actor_id": "actor-Y"})
        assert actor_y_history.status_code == 200
        assert len(actor_y_history.json()) == 1

    def test_cancel_requires_actor_id_match(self, tmp_path: Path) -> None:
        """Only owner can cancel their execution."""
        fake_celery = _FakeCeleryApp()
        client = _client(tmp_path, fake_celery)

        # Create a connector
        client.post(
            "/v1/admin/connectors",
            json={
                "name": "cancel-test-connector",
                "workspace_id": "default",
                "connection": {
                    "engine": "clickhouse",
                    "host": "localhost",
                    "port": 8123,
                    "username": "default",
                    "database": "default",
                },
            },
        )

        response = client.post(
            "/v1/sql/executions",
            json={
                "sql": "SELECT 1",
                "actor_id": "cancel-owner",
            },
        )
        execution_id = response.json()["execution_id"]

        # Owner can cancel
        cancel = client.post(
            f"/v1/sql/executions/{execution_id}/cancel",
            params={"actor_id": "cancel-owner"},
        )
        assert cancel.status_code == 200

        # Other user cannot cancel
        cancel_other = client.post(
            f"/v1/sql/executions/{execution_id}/cancel",
            params={"actor_id": "other-user"},
        )
        assert cancel_other.status_code == 403
        assert "Access denied" in cancel_other.json()["detail"]

    def test_cancel_requires_auth(self, tmp_path: Path) -> None:
        """Cancel without actor_id returns 403 when execution has actor_id set."""
        fake_celery = _FakeCeleryApp()
        client = _client(tmp_path, fake_celery)

        # Create a connector
        client.post(
            "/v1/admin/connectors",
            json={
                "name": "cancel-auth-connector",
                "workspace_id": "default",
                "connection": {
                    "engine": "clickhouse",
                    "host": "localhost",
                    "port": 8123,
                    "username": "default",
                    "database": "default",
                },
            },
        )

        response = client.post(
            "/v1/sql/executions",
            json={
                "sql": "SELECT 1",
                "actor_id": "cancel-auth-user",
            },
        )
        execution_id = response.json()["execution_id"]

        # Cancel without actor_id should fail (access denied since execution has actor_id)
        cancel = client.post(f"/v1/sql/executions/{execution_id}/cancel")
        assert cancel.status_code == 403
        assert "Access denied" in cancel.json()["detail"]
