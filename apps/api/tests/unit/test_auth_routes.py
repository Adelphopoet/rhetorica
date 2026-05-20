from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.auth import _user_repository, seed_local_admin_account
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import confluence_connectors as confluence_connector_routes
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import jira_connectors as jira_connector_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.routes import openmetadata_connectors as openmetadata_connector_routes
from rhetorica_api.app.settings import settings
from rhetorica_backend.domain.execution import build_execution_service
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.security.passwords import hash_password

ADMIN_LOGIN = "admin"
ADMIN_PASSWORD = "adminadmin"
MEMBER_PASSWORD = "member-pass-123"


def _client(
    tmp_path: Path,
    *,
    bootstrap_enabled: bool = False,
) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_auth.db"
    settings.auth_enabled = True
    settings.auth_default_workspace_id = "default"
    settings.auth_bootstrap_enabled = bootstrap_enabled
    settings.auth_bootstrap_login_secret = "test-bootstrap-secret-000000000000"
    settings.auth_jwt_secret = "test-jwt-secret-0000000000000000"
    settings.auth_local_admin_login = ADMIN_LOGIN
    settings.auth_local_admin_password = ADMIN_PASSWORD
    settings.auth_local_admin_display_name = "Admin User"

    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    confluence_connector_routes._repository.cache_clear()
    jira_connector_routes._repository.cache_clear()
    openmetadata_connector_routes._repository.cache_clear()
    _user_repository.cache_clear()

    _, db_runtime = build_execution_service(settings.database_url)
    db_runtime.create_schema()
    seed_local_admin_account()
    return TestClient(create_app())


def _login(client: TestClient, *, login: str = ADMIN_LOGIN, password: str | None = None) -> str:
    response = client.post(
        "/v1/auth/login",
        json={"login": login, "password": password or ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_login_succeeds_with_valid_admin_credentials(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/auth/login",
        json={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["workspace_id"] == "default"
    assert payload["role"] == "admin"
    assert payload["user"]["login"] == "admin"
    assert "access_token" in payload
    assert settings.auth_cookie_name in response.cookies


def test_login_fails_with_wrong_password(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/auth/login",
        json={"login": ADMIN_LOGIN, "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_credentials"


def test_protected_route_rejects_anonymous_request(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/v1/auth/session")

    assert response.status_code == 401
    assert response.json()["detail"] == "not_authenticated"


def test_protected_route_accepts_jwt_from_local_account(tmp_path: Path) -> None:
    client = _client(tmp_path)
    access_token = _login(client)

    response = client.get(
        "/v1/auth/session",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["login"] == "admin"
    assert response.json()["user"]["auth_provider"] == "local"


def test_admin_can_create_local_user(tmp_path: Path) -> None:
    client = _client(tmp_path)
    access_token = _login(client)

    response = client.post(
        "/v1/admin/users",
        json={"login": "new-user", "password": "strong-pass-123", "display_name": "New User"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["login"] == "new-user"
    assert payload["workspace_id"] == "default"
    assert payload["role"] == "admin"
    assert payload["auth_provider"] == "local"


def test_admin_can_delete_local_user(tmp_path: Path) -> None:
    client = _client(tmp_path)
    access_token = _login(client)

    create_response = client.post(
        "/v1/admin/users",
        json={
            "login": "delete-me",
            "password": "strong-pass-123",
            "display_name": "Delete Me",
            "role": "admin",
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert create_response.status_code == 200
    user_id = create_response.json()["id"]

    response = client.delete(
        f"/v1/admin/users/{user_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 204

    list_response = client.get(
        "/v1/admin/users",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert [user["login"] for user in list_response.json()] == ["admin"]


def test_admin_can_list_workspace_users(tmp_path: Path) -> None:
    client = _client(tmp_path)
    access_token = _login(client)

    create_response = client.post(
        "/v1/admin/users",
        json={"login": "listed-user", "password": "strong-pass-123", "display_name": "Listed User"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert create_response.status_code == 200

    response = client.get(
        "/v1/admin/users",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert [user["login"] for user in payload] == ["admin", "listed-user"]


def test_current_admin_cannot_delete_self(tmp_path: Path) -> None:
    client = _client(tmp_path)
    access_token = _login(client)
    admin_id = client.get(
        "/v1/admin/users",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()[0]["id"]

    response = client.delete(
        f"/v1/admin/users/{admin_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "cannot_delete_current_user"


def test_create_local_user_rejects_unknown_role(tmp_path: Path) -> None:
    client = _client(tmp_path)
    access_token = _login(client)

    response = client.post(
        "/v1/admin/users",
        json={"login": "bad-role", "password": "strong-pass-123", "role": "viewer"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unsupported_user_role"


def test_non_admin_cannot_create_local_user(tmp_path: Path) -> None:
    client = _client(tmp_path)
    repository = UserAccountRepository(db=build_execution_service(settings.database_url)[1])
    repository.create_local_user(
        workspace_id="default",
        login="analyst",
        password_hash=hash_password(MEMBER_PASSWORD),
        role="analyst",
        display_name="Analyst User",
    )
    access_token = _login(client, login="analyst", password=MEMBER_PASSWORD)

    response = client.post(
        "/v1/admin/users",
        json={"login": "blocked", "password": "strong-pass-123"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "admin_access_denied"


def test_analyst_cannot_list_admin_users(tmp_path: Path) -> None:
    client = _client(tmp_path)
    repository = UserAccountRepository(db=build_execution_service(settings.database_url)[1])
    repository.create_local_user(
        workspace_id="default",
        login="analyst",
        password_hash=hash_password(MEMBER_PASSWORD),
        role="analyst",
        display_name="Analyst User",
    )
    access_token = _login(client, login="analyst", password=MEMBER_PASSWORD)

    response = client.get(
        "/v1/admin/users",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "admin_access_denied"


def test_workspace_mismatch_still_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    access_token = _login(client)

    response = client.get(
        "/v1/scenarios",
        params={"workspace_id": "other-workspace"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "workspace_access_denied"


def test_bootstrap_route_is_disabled_by_default(tmp_path: Path) -> None:
    client = _client(tmp_path, bootstrap_enabled=False)

    response = client.post(
        "/v1/auth/bootstrap/session",
        headers={"X-Rhetorica-Bootstrap-Secret": "test-bootstrap-secret-000000000000"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "bootstrap_auth_disabled"


def test_bootstrap_route_works_only_when_explicitly_enabled(tmp_path: Path) -> None:
    client = _client(tmp_path, bootstrap_enabled=True)

    response = client.post(
        "/v1/auth/bootstrap/session",
        headers={"X-Rhetorica-Bootstrap-Secret": "test-bootstrap-secret-000000000000"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["auth_provider"] == "bootstrap"
