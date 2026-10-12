from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from urllib.parse import urlsplit

from rhetorica_backend.infra.postgres.oauth_repository import OAuthRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.oauth.service import OAuthServerConfig, OAuthService

from rhetorica_api.app.settings import settings


def oauth_token_pepper() -> str:
    pepper = settings.auth_oauth_token_pepper.strip()
    if not pepper:
        raise RuntimeError("AUTH_OAUTH_TOKEN_PEPPER_required")
    return pepper


def oauth_issuer() -> str:
    return settings.rhetorica_public_api_url.strip().rstrip("/")


def oauth_web_url() -> str:
    return settings.rhetorica_public_url.strip().rstrip("/")


def oauth_resource() -> str:
    return f"{oauth_issuer()}/mcp"


def oauth_resource_metadata_url() -> str:
    return f"{oauth_issuer()}/.well-known/oauth-protected-resource/mcp"


def validate_oauth_public_urls() -> None:
    for name, value in {
        "RHETORICA_PUBLIC_API_URL": oauth_issuer(),
        "RHETORICA_PUBLIC_URL": oauth_web_url(),
    }.items():
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.hostname or parsed.query or parsed.fragment:
            raise RuntimeError(f"{name}_invalid")
        if settings.app_env not in {"dev", "local", "test"} and parsed.scheme != "https":
            raise RuntimeError(f"{name}_https_required")


@lru_cache(maxsize=1)
def oauth_repository() -> OAuthRepository:
    runtime = DatabaseRuntime(settings.database_url)
    runtime.create_schema()
    return OAuthRepository(runtime, pepper=oauth_token_pepper())


@lru_cache(maxsize=1)
def oauth_service() -> OAuthService:
    return OAuthService(
        repository=oauth_repository(),
        config=OAuthServerConfig(
            issuer=oauth_issuer(),
            web_url=oauth_web_url(),
            resource=oauth_resource(),
            pepper=oauth_token_pepper(),
            cimd_enabled=settings.auth_oauth_cimd_enabled,
            transaction_lifetime=timedelta(
                minutes=settings.auth_oauth_transaction_ttl_minutes
            ),
            code_lifetime=timedelta(minutes=settings.auth_oauth_code_ttl_minutes),
            access_lifetime=timedelta(minutes=settings.auth_oauth_access_ttl_minutes),
            refresh_lifetime=timedelta(days=settings.auth_oauth_refresh_ttl_days),
            dcr_registrations_per_source=(
                settings.auth_oauth_dcr_registrations_per_source
            ),
            dcr_max_clients=settings.auth_oauth_dcr_max_clients,
            oauth_record_retention=timedelta(
                hours=settings.auth_oauth_record_retention_hours
            ),
            dcr_inactive_client_retention=timedelta(
                days=settings.auth_oauth_dcr_inactive_client_retention_days
            ),
        ),
    )
