from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from rhetorica_api.app.auth import _api_token_repository, _user_repository, seed_local_admin_account
from rhetorica_api.app.main import create_app
from rhetorica_api.app.oauth import oauth_repository, oauth_service
from rhetorica_api.app.routes import oauth as oauth_routes
from rhetorica_api.app.settings import settings
from rhetorica_backend.infra.security.oauth import pkce_s256_challenge

ADMIN_LOGIN = "admin"
ADMIN_PASSWORD = "adminadmin"
RESOURCE = "http://localhost:8000/mcp"
REDIRECT_URI = "http://127.0.0.1:49152/callback"
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"


def _client(tmp_path: Path) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/oauth_api.db"
    settings.auth_enabled = True
    settings.auth_default_workspace_id = "default"
    settings.auth_jwt_secret = "test-jwt-secret-0000000000000000"
    settings.auth_oauth_token_pepper = "test-oauth-secret-0000000000000000"
    settings.auth_oauth_cimd_enabled = True
    settings.auth_local_admin_login = ADMIN_LOGIN
    settings.auth_local_admin_password = ADMIN_PASSWORD
    settings.auth_local_admin_display_name = "Admin"
    settings.rhetorica_public_url = "http://localhost:3000"
    settings.rhetorica_public_api_url = "http://localhost:8000"
    settings.auth_oauth_dcr_registrations_per_source = 30
    settings.auth_oauth_dcr_max_clients = 10_000
    settings.auth_oauth_record_retention_hours = 24
    settings.auth_oauth_dcr_inactive_client_retention_days = 90
    _user_repository.cache_clear()
    _api_token_repository.cache_clear()
    oauth_repository.cache_clear()
    oauth_service.cache_clear()
    seed_local_admin_account()
    return TestClient(create_app())


def _login(client: TestClient) -> None:
    response = client.post(
        "/v1/auth/login",
        json={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200


def _register(client: TestClient) -> str:
    response = client.post(
        "/oauth/register",
        json={
            "client_name": "Pytest MCP client",
            "redirect_uris": [REDIRECT_URI],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert "client_secret" not in response.json()
    return response.json()["client_id"]


def _authorize_and_approve(
    client: TestClient,
    *,
    client_id: str,
    scope: str = "mcp:read mcp:execute",
) -> str:
    response = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": scope,
            "state": "state-123",
            "code_challenge": pkce_s256_challenge(VERIFIER),
            "code_challenge_method": "S256",
            "resource": RESOURCE,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    consent_url = urlsplit(response.headers["location"])
    transaction = parse_qs(consent_url.query)["transaction"][0]
    details = client.get(f"/v1/oauth/consent/{transaction}")
    assert details.status_code == 200
    assert details.json()["client"]["name"] == "Pytest MCP client"
    assert details.json()["redirect_host"] == "127.0.0.1"
    expected_scopes = scope.split()
    if "mcp:execute" in expected_scopes and "mcp:read" not in expected_scopes:
        expected_scopes.insert(0, "mcp:read")
    assert details.json()["scopes"] == expected_scopes
    approved = client.post(f"/v1/oauth/consent/{transaction}/approve")
    assert approved.status_code == 200
    redirect = urlsplit(approved.json()["redirect_uri"])
    query = parse_qs(redirect.query)
    assert query["state"] == ["state-123"]
    return query["code"][0]


def _exchange(client: TestClient, *, client_id: str, code: str) -> dict[str, object]:
    response = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": VERIFIER,
            "resource": RESOURCE,
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def _mcp_initialize(client: TestClient, token: str):
    return client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pytest-oauth", "version": "1"},
            },
        },
    )


def test_metadata_and_mcp_challenge_are_discoverable(tmp_path: Path) -> None:
    client = _client(tmp_path)
    protected = client.get("/.well-known/oauth-protected-resource/mcp")
    assert protected.status_code == 200
    assert protected.json()["resource"] == RESOURCE
    assert protected.json()["authorization_servers"] == ["http://localhost:8000"]
    metadata = client.get("/.well-known/oauth-authorization-server")
    assert metadata.status_code == 200
    assert metadata.json()["client_id_metadata_document_supported"] is True
    assert metadata.json()["code_challenge_methods_supported"] == ["S256"]

    challenge = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert challenge.status_code == 401
    assert 'resource_metadata="http://localhost:8000/.well-known/oauth-protected-resource/mcp"' in challenge.headers["www-authenticate"]


def test_metadata_falls_back_to_dcr_when_cimd_is_disabled(tmp_path: Path) -> None:
    client = _client(tmp_path)
    settings.auth_oauth_cimd_enabled = False
    oauth_service.cache_clear()

    metadata = client.get("/.well-known/oauth-authorization-server")

    assert metadata.status_code == 200
    assert "client_id_metadata_document_supported" not in metadata.json()
    assert metadata.json()["registration_endpoint"] == "http://localhost:8000/oauth/register"
    assert _register(client).startswith("rh_oc_")


def test_full_oauth_flow_rotates_and_revokes_tokens(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _login(client)
        client_id = _register(client)
        code = _authorize_and_approve(client, client_id=client_id)

        wrong_resource = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": VERIFIER,
                "resource": "https://wrong.example/mcp",
            },
        )
        assert wrong_resource.status_code == 400
        assert wrong_resource.json()["error"] == "invalid_target"

        tokens = _exchange(client, client_id=client_id, code=code)
        access_token = str(tokens["access_token"])
        refresh_token = str(tokens["refresh_token"])
        assert access_token.startswith("rh_oat_")
        assert refresh_token.startswith("rh_ort_")
        assert _mcp_initialize(client, access_token).status_code == 200

        refreshed = client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
                "resource": RESOURCE,
            },
        )
        assert refreshed.status_code == 200
        new_access = refreshed.json()["access_token"]
        assert new_access != access_token
        assert _mcp_initialize(client, access_token).status_code == 401
        assert _mcp_initialize(client, new_access).status_code == 200

        revoked = client.post(
            "/oauth/revoke",
            data={"client_id": client_id, "token": new_access},
        )
        assert revoked.status_code == 200
        assert _mcp_initialize(client, new_access).status_code == 401


def test_read_scope_gets_resource_metadata_on_execute_denial(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _login(client)
        client_id = _register(client)
        code = _authorize_and_approve(client, client_id=client_id, scope="mcp:read")
        access_token = str(_exchange(client, client_id=client_id, code=code)["access_token"])

        denied = client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "run_sql", "arguments": {"query": "SELECT 1"}},
            },
        )
        assert denied.status_code == 403
        challenge = denied.headers["www-authenticate"]
        assert 'error="insufficient_scope"' in challenge
        assert 'scope="mcp:execute"' in challenge
        assert "resource_metadata=" in challenge


def test_execute_only_oauth_request_gets_usable_effective_scopes(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _login(client)
        client_id = _register(client)
        code = _authorize_and_approve(client, client_id=client_id, scope="mcp:execute")
        tokens = _exchange(client, client_id=client_id, code=code)

        assert tokens["scope"] == "mcp:read mcp:execute"
        assert _mcp_initialize(client, str(tokens["access_token"])).status_code == 200


def test_dcr_rate_limit_returns_retry_after_without_creating_client(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    settings.auth_oauth_dcr_registrations_per_source = 1
    oauth_service.cache_clear()
    with client:
        _register(client)
        limited = client.post(
            "/oauth/register",
            json={
                "client_name": "Second MCP client",
                "redirect_uris": [REDIRECT_URI],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )

    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "3600"
    assert limited.json() == {
        "error": "temporarily_unavailable",
        "error_description": "Client registration rate limit exceeded",
    }


def test_dcr_unexpected_error_is_logged_but_not_exposed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class BrokenOAuthService:
        def register_client(self, *_args, **_kwargs):
            raise RuntimeError("postgresql://user:database-password@internal/db")

    monkeypatch.setattr(oauth_routes, "oauth_service", lambda: BrokenOAuthService())
    log_exception = Mock()
    monkeypatch.setattr(oauth_routes.logger, "exception", log_exception)
    with _client(tmp_path) as client:
        response = client.post(
            "/oauth/register",
            json={
                "client_name": "Broken client",
                "redirect_uris": [REDIRECT_URI],
            },
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": "server_error",
        "error_description": "Client registration failed",
    }
    assert "database-password" not in response.text
    log_exception.assert_called_once_with("oauth_client_registration_failed")


def test_denied_consent_and_redirect_mismatch_never_issue_code(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _login(client)
    client_id = _register(client)
    mismatch = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://127.0.0.1:49152/other",
            "code_challenge": pkce_s256_challenge(VERIFIER),
            "code_challenge_method": "S256",
            "resource": RESOURCE,
        },
        follow_redirects=False,
    )
    assert mismatch.status_code == 400

    authorization = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "state": "deny-state",
            "code_challenge": pkce_s256_challenge(VERIFIER),
            "code_challenge_method": "S256",
            "resource": RESOURCE,
        },
        follow_redirects=False,
    )
    transaction = parse_qs(urlsplit(authorization.headers["location"]).query)["transaction"][0]
    denied = client.post(f"/v1/oauth/consent/{transaction}/deny")
    query = parse_qs(urlsplit(denied.json()["redirect_uri"]).query)
    assert query == {"error": ["access_denied"], "state": ["deny-state"]}
    assert client.post(f"/v1/oauth/consent/{transaction}/approve").status_code == 404
