from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from rhetorica_backend.infra.postgres.oauth_repository import OAuthRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.security.oauth import OAuthClientMetadata, pkce_s256_challenge
from rhetorica_backend.oauth import service as service_module
from rhetorica_backend.oauth.service import OAuthProtocolError, OAuthServerConfig, OAuthService

PEPPER = "oauth-service-pepper-000000000000"
RESOURCE = "https://api.example.com/mcp"


def _service(tmp_path: Path, **config_overrides: Any) -> OAuthService:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/oauth_service.db")
    runtime.create_schema()
    config = {
        "issuer": "https://api.example.com",
        "web_url": "https://app.example.com",
        "resource": RESOURCE,
        "pepper": PEPPER,
        "transaction_lifetime": timedelta(minutes=10),
        "code_lifetime": timedelta(minutes=5),
        "access_lifetime": timedelta(hours=1),
        "refresh_lifetime": timedelta(days=30),
        **config_overrides,
    }
    return OAuthService(
        repository=OAuthRepository(runtime, pepper=PEPPER),
        config=OAuthServerConfig(**config),
    )


def _registration_payload() -> dict[str, object]:
    return {
        "client_name": "Public client",
        "redirect_uris": ["https://client.example/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


@pytest.mark.asyncio
async def test_confidential_dcr_client_secret_is_digest_verified(tmp_path: Path) -> None:
    service = _service(tmp_path)
    registered = service.register_client(
        {
            "client_name": "Confidential client",
            "redirect_uris": ["https://client.example/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_basic",
        }
    )
    assert registered.client_secret is not None
    stored = service.repository.get_client(client_id=registered.client.client_id)
    assert stored is not None
    assert stored.client_secret_digest != registered.client_secret
    await service.authenticate_client(
        client_id=registered.client.client_id,
        client_secret=registered.client_secret,
        presented_auth_method="client_secret_basic",
    )
    with pytest.raises(OAuthProtocolError, match="Client authentication failed"):
        await service.authenticate_client(
            client_id=registered.client.client_id,
            client_secret="rh_ocs_00000000000000000000000000000000.invalidsecretvalue00000000000000",
            presented_auth_method="client_secret_basic",
        )


@pytest.mark.asyncio
async def test_cimd_client_is_resolved_and_bound_to_exact_redirect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_id = "https://client.example/.well-known/oauth-client"

    async def fake_resolver(value: str) -> OAuthClientMetadata:
        assert value == client_id
        return OAuthClientMetadata(
            client_id=client_id,
            client_name="Hosted MCP",
            client_uri="https://client.example",
            redirect_uris=("https://client.example/callback",),
            grant_types=("authorization_code", "refresh_token"),
            response_types=("code",),
            token_endpoint_auth_method="none",
            source="cimd",
        )

    monkeypatch.setattr(service_module, "resolve_client_id_metadata", fake_resolver)
    service = _service(tmp_path)
    consent_url = await service.start_authorization(
        response_type="code",
        client_id=client_id,
        redirect_uri="https://client.example/callback",
        scope="mcp:read",
        state="cimd-state",
        code_challenge=pkce_s256_challenge(
            "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        ),
        code_challenge_method="S256",
        resource=RESOURCE,
    )
    transaction = parse_qs(urlsplit(consent_url).query)["transaction"][0]
    assert service.get_consent(transaction_token=transaction).client_name == "Hosted MCP"

    with pytest.raises(OAuthProtocolError, match="redirect_uri is not registered"):
        await service.start_authorization(
            response_type="code",
            client_id=client_id,
            redirect_uri="https://client.example/other",
            scope="mcp:read",
            state=None,
            code_challenge="a" * 43,
            code_challenge_method="S256",
            resource=RESOURCE,
        )


@pytest.mark.asyncio
async def test_disabled_cimd_rejects_https_client_without_outbound_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = pytest.fail
    monkeypatch.setattr(service_module, "resolve_client_id_metadata", resolver)
    service = _service(tmp_path, cimd_enabled=False)

    with pytest.raises(OAuthProtocolError) as captured:
        await service.resolve_client("https://client.example/oauth/client.json")

    assert captured.value.error == "invalid_client"
    assert captured.value.status_code == 401
    assert captured.value.description == "Client ID Metadata Documents are not enabled"


def test_dcr_rate_limit_is_persistent_per_source(tmp_path: Path) -> None:
    service = _service(tmp_path, dcr_registrations_per_source=1)
    now = datetime(2026, 9, 2, tzinfo=UTC)

    service.register_client(_registration_payload(), source="192.0.2.10", now=now)
    with pytest.raises(OAuthProtocolError) as captured:
        service.register_client(
            _registration_payload(),
            source="192.0.2.10",
            now=now + timedelta(seconds=1),
        )

    assert captured.value.status_code == 429
    assert captured.value.error == "temporarily_unavailable"
    assert captured.value.retry_after_seconds == 3600
    service.register_client(
        _registration_payload(),
        source="192.0.2.11",
        now=now + timedelta(seconds=1),
    )


def test_dcr_total_capacity_rejects_new_client(tmp_path: Path) -> None:
    service = _service(tmp_path, dcr_max_clients=1)
    now = datetime(2026, 9, 2, tzinfo=UTC)

    service.register_client(_registration_payload(), source="192.0.2.10", now=now)
    with pytest.raises(OAuthProtocolError) as captured:
        service.register_client(
            _registration_payload(),
            source="192.0.2.11",
            now=now + timedelta(seconds=1),
        )

    assert captured.value.status_code == 429
    assert captured.value.error == "temporarily_unavailable"
    assert captured.value.description == "Client registration capacity reached"
