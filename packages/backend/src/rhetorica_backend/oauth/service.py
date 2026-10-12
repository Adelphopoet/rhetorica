from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from rhetorica_backend.infra.postgres.oauth_repository import (
    OAuthClientItem,
    OAuthConsentItem,
    OAuthRepository,
    OAuthTokenGrantItem,
)
from rhetorica_backend.infra.security.oauth import (
    PUBLIC_CLIENT_AUTH_METHOD,
    OAuthClientMetadata,
    issue_oauth_secret,
    normalize_oauth_scopes,
    oauth_registration_source_digest,
    pkce_s256_challenge,
    resolve_client_id_metadata,
    validate_client_registration,
    validate_pkce_challenge,
)


class OAuthProtocolError(Exception):
    def __init__(
        self,
        error: str,
        description: str,
        *,
        status_code: int = 400,
        authenticate: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.error = error
        self.description = description
        self.status_code = status_code
        self.authenticate = authenticate
        self.retry_after_seconds = retry_after_seconds
        super().__init__(description)


@dataclass(frozen=True)
class OAuthServerConfig:
    issuer: str
    web_url: str
    resource: str
    pepper: str
    transaction_lifetime: timedelta
    code_lifetime: timedelta
    access_lifetime: timedelta
    refresh_lifetime: timedelta
    cimd_enabled: bool = True
    dcr_registrations_per_source: int = 30
    dcr_registration_window: timedelta = timedelta(hours=1)
    dcr_max_clients: int = 10_000
    oauth_record_retention: timedelta = timedelta(hours=24)
    dcr_inactive_client_retention: timedelta = timedelta(days=90)


@dataclass(frozen=True)
class OAuthRegistrationResult:
    client: OAuthClientItem
    client_secret: str | None


@dataclass(frozen=True)
class OAuthTokenResult:
    access_token: str
    refresh_token: str
    expires_in: int
    scopes: tuple[str, ...]


def add_query(url: str, values: dict[str, str | None]) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend((key, value) for key, value in values.items() if value is not None)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


class OAuthService:
    def __init__(self, *, repository: OAuthRepository, config: OAuthServerConfig):
        self.repository = repository
        self.config = config

    async def resolve_client(self, client_id: str) -> OAuthClientMetadata:
        if client_id.startswith("https://"):
            if not self.config.cimd_enabled:
                raise OAuthProtocolError(
                    "invalid_client",
                    "Client ID Metadata Documents are not enabled",
                    status_code=401,
                )
            try:
                return await resolve_client_id_metadata(client_id)
            except ValueError as exc:
                raise OAuthProtocolError("invalid_client", str(exc), status_code=401) from exc
        item = self.repository.get_client(client_id=client_id)
        if item is None:
            raise OAuthProtocolError("invalid_client", "Unknown client", status_code=401)
        return self._metadata_from_item(item)

    def register_client(
        self,
        payload: dict[str, object],
        *,
        source: str = "unknown",
        now: datetime | None = None,
    ) -> OAuthRegistrationResult:
        try:
            metadata = validate_client_registration(payload)
        except ValueError as exc:
            raise OAuthProtocolError("invalid_client_metadata", str(exc)) from exc
        registered_at = now or datetime.now(tz=UTC)
        source_digest = oauth_registration_source_digest(
            source,
            pepper=self.config.pepper,
        )
        recent_registrations = self.repository.count_recent_registrations(
            registration_source_digest=source_digest,
            created_since=registered_at - self.config.dcr_registration_window,
        )
        retry_after = max(int(self.config.dcr_registration_window.total_seconds()), 1)
        if recent_registrations >= self.config.dcr_registrations_per_source:
            raise OAuthProtocolError(
                "temporarily_unavailable",
                "Client registration rate limit exceeded",
                status_code=429,
                retry_after_seconds=retry_after,
            )
        self.repository.cleanup_expired(
            invalid_before=registered_at - self.config.oauth_record_retention,
            inactive_client_before=(
                registered_at - self.config.dcr_inactive_client_retention
            ),
        )
        if self.repository.count_clients() >= self.config.dcr_max_clients:
            raise OAuthProtocolError(
                "temporarily_unavailable",
                "Client registration capacity reached",
                status_code=429,
                retry_after_seconds=retry_after,
            )
        client_id = f"rh_oc_{uuid4().hex}"
        secret = None
        if metadata.token_endpoint_auth_method != PUBLIC_CLIENT_AUTH_METHOD:
            secret = issue_oauth_secret(kind="client_secret", pepper=self.config.pepper)
        item = self.repository.register_client(
            metadata=metadata,
            client_id=client_id,
            client_secret=secret,
            registration_source_digest=source_digest,
            now=registered_at,
        )
        return OAuthRegistrationResult(client=item, client_secret=secret.value if secret else None)

    async def start_authorization(
        self,
        *,
        response_type: str,
        client_id: str,
        redirect_uri: str,
        scope: str | None,
        state: str | None,
        code_challenge: str,
        code_challenge_method: str,
        resource: str,
    ) -> str:
        if response_type != "code":
            raise OAuthProtocolError("unsupported_response_type", "Only code is supported")
        if resource != self.config.resource:
            raise OAuthProtocolError("invalid_target", "Unknown resource")
        try:
            challenge = validate_pkce_challenge(code_challenge, code_challenge_method)
            scopes = normalize_oauth_scopes(scope)
        except ValueError as exc:
            raise OAuthProtocolError(str(exc), str(exc)) from exc
        client = await self.resolve_client(client_id)
        if redirect_uri not in client.redirect_uris:
            raise OAuthProtocolError("invalid_request", "redirect_uri is not registered")
        if client.source == "dcr":
            self.repository.touch_client(client_id=client.client_id)
        transaction = issue_oauth_secret(kind="transaction", pepper=self.config.pepper)
        self.repository.create_consent(
            transaction=transaction,
            client=client,
            redirect_uri=redirect_uri,
            scopes=scopes,
            state=state,
            code_challenge=challenge,
            resource=resource,
            lifetime=self.config.transaction_lifetime,
        )
        return add_query(
            f"{self.config.web_url}/oauth/consent",
            {"transaction": transaction.value},
        )

    def get_consent(self, *, transaction_token: str) -> OAuthConsentItem:
        item = self.repository.get_active_consent(transaction_token=transaction_token)
        if item is None:
            raise OAuthProtocolError("invalid_request", "Consent transaction expired", status_code=404)
        return item

    def approve_consent(
        self, *, transaction_token: str, user_id: str, workspace_id: str
    ) -> str:
        code = issue_oauth_secret(kind="code", pepper=self.config.pepper)
        approved = self.repository.approve_consent(
            transaction_token=transaction_token,
            code=code,
            user_id=user_id,
            workspace_id=workspace_id,
            code_lifetime=self.config.code_lifetime,
        )
        if approved is None:
            raise OAuthProtocolError("invalid_request", "Consent transaction expired", status_code=404)
        consent, _ = approved
        return add_query(consent.redirect_uri, {"code": code.value, "state": consent.state})

    def deny_consent(self, *, transaction_token: str) -> str:
        consent = self.repository.deny_consent(transaction_token=transaction_token)
        if consent is None:
            raise OAuthProtocolError("invalid_request", "Consent transaction expired", status_code=404)
        return add_query(
            consent.redirect_uri,
            {"error": "access_denied", "state": consent.state},
        )

    async def exchange_code(
        self,
        *,
        client_id: str,
        client_secret: str | None,
        client_auth_method: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        resource: str,
    ) -> OAuthTokenResult:
        await self.authenticate_client(
            client_id=client_id,
            client_secret=client_secret,
            presented_auth_method=client_auth_method,
        )
        if resource != self.config.resource:
            raise OAuthProtocolError("invalid_target", "Unknown resource")
        try:
            challenge = pkce_s256_challenge(code_verifier)
        except ValueError as exc:
            raise OAuthProtocolError("invalid_grant", str(exc)) from exc
        code_item = self.repository.consume_code(
            code_token=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            resource=resource,
            code_challenge=challenge,
        )
        if code_item is None:
            raise OAuthProtocolError("invalid_grant", "Code is invalid, expired, or already used")
        pair = self.repository.issue_token_pair(
            code=code_item,
            access_token=issue_oauth_secret(kind="access", pepper=self.config.pepper),
            refresh_token=issue_oauth_secret(kind="refresh", pepper=self.config.pepper),
            access_lifetime=self.config.access_lifetime,
            refresh_lifetime=self.config.refresh_lifetime,
        )
        return self._token_result(pair.access_token, pair.refresh_token, pair.grant)

    async def refresh(
        self,
        *,
        client_id: str,
        client_secret: str | None,
        client_auth_method: str,
        refresh_token: str,
        resource: str,
        scope: str | None,
    ) -> OAuthTokenResult:
        await self.authenticate_client(
            client_id=client_id,
            client_secret=client_secret,
            presented_auth_method=client_auth_method,
        )
        if resource != self.config.resource:
            raise OAuthProtocolError("invalid_target", "Unknown resource")
        try:
            scopes = normalize_oauth_scopes(scope) if scope is not None else None
        except ValueError as exc:
            raise OAuthProtocolError("invalid_scope", str(exc)) from exc
        result, pair = self.repository.rotate_refresh_token(
            refresh_token_value=refresh_token,
            client_id=client_id,
            resource=resource,
            requested_scopes=scopes,
            access_token=issue_oauth_secret(kind="access", pepper=self.config.pepper),
            refresh_token=issue_oauth_secret(kind="refresh", pepper=self.config.pepper),
            access_lifetime=self.config.access_lifetime,
        )
        if result == "invalid_scope":
            raise OAuthProtocolError("invalid_scope", "Refresh cannot expand scopes")
        if pair is None:
            raise OAuthProtocolError("invalid_grant", "Refresh token is invalid or replayed")
        return self._token_result(pair.access_token, pair.refresh_token, pair.grant)

    async def revoke(
        self,
        *,
        client_id: str,
        client_secret: str | None,
        client_auth_method: str,
        token: str,
    ) -> None:
        await self.authenticate_client(
            client_id=client_id,
            client_secret=client_secret,
            presented_auth_method=client_auth_method,
        )
        self.repository.revoke_token_family(token_value=token, client_id=client_id)

    async def authenticate_client(
        self,
        *,
        client_id: str,
        client_secret: str | None,
        presented_auth_method: str,
    ) -> OAuthClientMetadata:
        if client_id.startswith("https://"):
            if client_secret or presented_auth_method != PUBLIC_CLIENT_AUTH_METHOD:
                raise OAuthProtocolError(
                    "invalid_client", "CIMD client is public", status_code=401, authenticate=True
                )
            return await self.resolve_client(client_id)
        item = self.repository.get_client(client_id=client_id)
        if item is None:
            raise OAuthProtocolError(
                "invalid_client", "Unknown client", status_code=401, authenticate=True
            )
        if item.token_endpoint_auth_method != presented_auth_method:
            raise OAuthProtocolError(
                "invalid_client",
                "Client authentication method does not match registration",
                status_code=401,
                authenticate=True,
            )
        if item.token_endpoint_auth_method == PUBLIC_CLIENT_AUTH_METHOD:
            if client_secret:
                raise OAuthProtocolError(
                    "invalid_client", "Public client cannot use a secret", status_code=401
                )
            metadata = self._metadata_from_item(item)
            self.repository.touch_client(client_id=item.client_id)
            return metadata
        if not client_secret or self.repository.authenticate_client_secret(
            client_id=client_id,
            client_secret=client_secret,
        ) is None:
            raise OAuthProtocolError(
                "invalid_client", "Client authentication failed", status_code=401, authenticate=True
            )
        metadata = self._metadata_from_item(item)
        self.repository.touch_client(client_id=item.client_id)
        return metadata

    @staticmethod
    def parse_basic_client(authorization: str | None) -> tuple[str | None, str | None]:
        if not authorization or not authorization.lower().startswith("basic "):
            return None, None
        try:
            decoded = base64.b64decode(authorization.split(" ", 1)[1], validate=True).decode()
            client_id, client_secret = decoded.split(":", 1)
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise OAuthProtocolError(
                "invalid_client", "Malformed client authentication", status_code=401
            ) from exc
        return client_id, client_secret

    @staticmethod
    def _metadata_from_item(item: OAuthClientItem) -> OAuthClientMetadata:
        return OAuthClientMetadata(
            client_id=item.client_id,
            client_name=item.client_name,
            client_uri=item.client_uri,
            redirect_uris=item.redirect_uris,
            grant_types=item.grant_types,
            response_types=item.response_types,
            token_endpoint_auth_method=item.token_endpoint_auth_method,
            source="dcr",
        )

    def _token_result(
        self, access_token: str, refresh_token: str, grant: OAuthTokenGrantItem
    ) -> OAuthTokenResult:
        seconds = int((grant.access_expires_at - datetime.now(tz=UTC)).total_seconds())
        return OAuthTokenResult(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=max(seconds, 1),
            scopes=grant.scopes,
        )
