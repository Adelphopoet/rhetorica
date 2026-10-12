from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.settings import settings


def _client(tmp_path: Path) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_llm_connectors.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    return TestClient(create_app())


def test_llm_connector_upsert_list_delete(tmp_path: Path) -> None:
    client = _client(tmp_path)
    create_response = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "openrouter-example",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://openrouter.ai/api/v1",
                "model": "openai/gpt-4.1",
                "api_key": "secret-key",
                "temperature": 0.2,
                "max_tokens": 900,
                "max_tokens_parameter": "max_completion_tokens",
                "extra_settings": {"context_window_tokens": 190000},
            },
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["name"] == "openrouter-example"
    assert payload["has_api_key"] is True
    assert payload["api_key_count"] == 1
    assert payload["connection"]["api_key"] is None
    assert payload["connection"]["api_keys"] is None
    assert payload["connection"]["max_tokens_parameter"] == "max_completion_tokens"
    assert payload["connection"]["extra_settings"]["context_window_tokens"] == 190000
    connector_id = payload["id"]

    list_response = client.get("/v1/admin/llm-connectors", params={"workspace_id": "default"})
    assert list_response.status_code == 200
    rows = list_response.json()
    assert len(rows) == 1
    assert rows[0]["id"] == connector_id

    update_response = client.put(
        f"/v1/admin/llm-connectors/{connector_id}",
        params={"workspace_id": "default"},
        json={
            "name": "openrouter-main-updated",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://openrouter.ai/api/v1",
                "model": "openai/gpt-4.1-mini",
                "api_key": None,
                "verify_tls": False,
                "temperature": 0.1,
                "max_tokens": 1200,
            },
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "openrouter-main-updated"
    assert updated["has_api_key"] is True
    assert updated["api_key_count"] == 1
    assert updated["connection"]["verify_tls"] is False
    assert updated["connection"]["max_tokens_parameter"] == "max_tokens"

    delete_response = client.delete(
        f"/v1/admin/llm-connectors/{connector_id}",
        params={"workspace_id": "default"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True


def test_llm_connector_rejects_unknown_max_tokens_parameter(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "invalid-limit-field",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://llm.example/v1",
                "model": "model",
                "max_tokens": 1200,
                "max_tokens_parameter": "max_output_tokens",
            },
        },
    )

    assert response.status_code == 422


def test_llm_connector_reasoning_effort_round_trip_and_validation(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {
        "name": "any-provider",
        "workspace_id": "default",
        "connection": {
            "base_url": "https://provider.example/v1",
            "model": "reasoning-model",
            "reasoning_effort": "high",
        },
    }
    created = client.post("/v1/admin/llm-connectors", json=body)
    assert created.status_code == 200
    connector_id = created.json()["id"]
    assert created.json()["connection"]["reasoning_effort"] == "high"
    assert client.get("/v1/admin/llm-connectors?workspace_id=default").json()[0][
        "connection"
    ]["reasoning_effort"] == "high"
    assert llm_connector_routes._repository().get_connection("default", "any-provider").reasoning_effort == "high"

    body["connection"]["reasoning_effort"] = "unsupported"
    invalid = client.put(f"/v1/admin/llm-connectors/{connector_id}?workspace_id=default", json=body)
    assert invalid.status_code == 422
    assert llm_connector_routes._repository().get_connection("default", "any-provider").reasoning_effort == "high"

    body["connection"]["reasoning_effort"] = None
    cleared = client.put(f"/v1/admin/llm-connectors/{connector_id}?workspace_id=default", json=body)
    assert cleared.status_code == 200
    assert cleared.json()["connection"]["reasoning_effort"] is None


def test_llm_connector_accepts_runtime_context_role(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "responses-user-context",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://llm.example/v1",
                "model": "model",
                "endpoint_path": "/responses",
                "extra_settings": {"runtime_context_role": "user"},
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["connection"]["extra_settings"]["runtime_context_role"] == "user"


def test_llm_connector_rejects_unknown_runtime_context_role(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "invalid-runtime-context-role",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://llm.example/v1",
                "model": "model",
                "extra_settings": {"runtime_context_role": "system"},
            },
        },
    )

    assert response.status_code == 422
    assert "runtime_context_role_must_be_developer_or_user" in response.text


def test_llm_connector_accepts_multiple_api_keys(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "openrouter-example",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://openrouter.ai/api/v1",
                "model": "openai/gpt-4.1",
                "api_keys": ["secret-key-1", "secret-key-2", "secret-key-1"],
                "temperature": 0.2,
                "max_tokens": 900,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_api_key"] is True
    assert payload["api_key_count"] == 2
    assert payload["connection"]["api_key"] is None
    assert payload["connection"]["api_keys"] is None

    update_response = client.put(
        f"/v1/admin/llm-connectors/{payload['id']}",
        params={"workspace_id": "default"},
        json={
            "name": "openrouter-example",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://openrouter.ai/api/v1",
                "model": "openai/gpt-4.1",
                "retained_api_key_indexes": [0],
                "api_keys": ["secret-key-3"],
                "temperature": 0.2,
                "max_tokens": 900,
            },
        },
    )

    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["has_api_key"] is True
    assert updated["api_key_count"] == 2
    assert updated["connection"]["api_keys"] is None


def test_llm_connector_default_is_explicit_and_unique(tmp_path: Path) -> None:
    client = _client(tmp_path)

    def payload(name: str, model: str, *, is_default: bool | None = None) -> dict:
        result = {
            "name": name,
            "workspace_id": "default",
            "is_active": True,
            "connection": {
                "base_url": "https://llm.example/v1",
                "model": model,
                "api_key": f"{name}-key",
            },
        }
        if is_default is not None:
            result["is_default"] = is_default
        return result

    first = client.post(
        "/v1/admin/llm-connectors",
        json=payload("first", "model-first", is_default=True),
    )
    assert first.status_code == 200
    assert first.json()["is_default"] is True

    second = client.post(
        "/v1/admin/llm-connectors",
        json=payload("second", "model-second", is_default=True),
    )
    assert second.status_code == 200
    assert second.json()["is_default"] is True

    rows = client.get(
        "/v1/admin/llm-connectors",
        params={"workspace_id": "default"},
    ).json()
    assert [row["name"] for row in rows] == ["second", "first"]
    assert [row["is_default"] for row in rows] == [True, False]
    assert llm_connector_routes._repository().get_connection("default").model == "model-second"
    assert (
        llm_connector_routes._repository().get_connection("default", "first").model == "model-first"
    )

    preserve = client.put(
        f"/v1/admin/llm-connectors/{first.json()['id']}",
        params={"workspace_id": "default"},
        json=payload("first", "model-first-updated"),
    )
    assert preserve.status_code == 200
    rows = client.get(
        "/v1/admin/llm-connectors",
        params={"workspace_id": "default"},
    ).json()
    assert next(row for row in rows if row["name"] == "second")["is_default"] is True

    deactivate = client.put(
        f"/v1/admin/llm-connectors/{second.json()['id']}",
        params={"workspace_id": "default"},
        json={**payload("second", "model-second"), "is_active": False},
    )
    assert deactivate.status_code == 200
    assert deactivate.json()["is_default"] is False
    assert llm_connector_routes._repository().get_connection("default") is None

    reactivate = client.put(
        f"/v1/admin/llm-connectors/{second.json()['id']}",
        params={"workspace_id": "default"},
        json=payload("second", "model-second", is_default=True),
    )
    assert reactivate.status_code == 200

    clear = client.put(
        f"/v1/admin/llm-connectors/{second.json()['id']}",
        params={"workspace_id": "default"},
        json=payload("second", "model-second", is_default=False),
    )
    assert clear.status_code == 200
    assert clear.json()["is_default"] is False
    assert llm_connector_routes._repository().get_connection("default") is None


def test_llm_connector_rejects_inactive_default(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "inactive-default",
            "workspace_id": "default",
            "is_active": False,
            "is_default": True,
            "connection": {
                "base_url": "https://llm.example/v1",
                "model": "model-inactive",
            },
        },
    )

    assert response.status_code == 422
    assert "default_llm_connector_must_be_active" in response.text


def test_llm_sampling_round_trip_and_clear_preserves_keys(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {
        "name": "sampling-test",
        "workspace_id": "default",
        "connection": {
            "base_url": "https://llm.example/v1", "model": "test",
            "api_key": "sampling-secret", "temperature": None, "max_tokens": None,
            "top_p": 0.95, "headers": {"X-Client": "analytics"},
            "extra_settings": {"chat_template_enable_thinking": False, "llm_retry_attempts": 2},
        },
    }
    created = client.post("/v1/admin/llm-connectors", json=body)
    assert created.status_code == 200
    item = created.json()
    connector_id = item["id"]
    for result in [item, client.get("/v1/admin/llm-connectors?workspace_id=default").json()[0]]:
        assert result["connection"]["top_p"] == 0.95
        assert result["connection"]["temperature"] is None
        assert result["connection"]["max_tokens"] is None
        assert result["connection"]["headers"] == {"X-Client": "analytics"}
        assert result["connection"]["extra_settings"]["chat_template_enable_thinking"] is False
        assert "sampling-secret" not in str(result)
    body["connection"]["api_key"] = None
    body["connection"]["top_p"] = 0.0
    updated = client.put(f"/v1/admin/llm-connectors/{connector_id}?workspace_id=default", json=body)
    assert updated.status_code == 200
    assert updated.json()["connection"]["top_p"] == 0.0
    assert updated.json()["api_key_count"] == 1
    resolved = llm_connector_routes._repository().get_connection("default", "sampling-test")
    assert resolved is not None and resolved.top_p == 0.0
    body["connection"]["top_p"] = None
    cleared = client.put(f"/v1/admin/llm-connectors/{connector_id}?workspace_id=default", json=body)
    assert cleared.status_code == 200
    assert cleared.json()["connection"]["top_p"] is None


def test_llm_rejects_invalid_sampling_and_unsupported_overrides(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for invalid in [{"top_p": -0.1}, {"top_p": 1.01}, {"phase_capable": {"tool_loop": False}}]:
        response = client.post("/v1/admin/llm-connectors", json={
            "name": "invalid-sampling", "connection": {
                "base_url": "https://llm.example/v1", "model": "test", **invalid,
            },
        })
        assert response.status_code == 422
