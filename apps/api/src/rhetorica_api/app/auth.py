from __future__ import annotations

import json
import logging
import secrets
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import jwt
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from jwt import (
    ExpiredSignatureError,
    InvalidTokenError,
    MissingRequiredClaimError,
    PyJWKClient,
)
from rhetorica_backend.infra.postgres.api_token_repository import (
    PersonalAccessTokenRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import (
    ADMIN_ROLE,
    ANALYST_ROLE,
    LOCAL_AUTH_PROVIDER,
    UserAccountItem,
    UserAccountRepository,
)
from rhetorica_backend.infra.security.api_tokens import (
    api_token_prefix,
    digest_api_token,
)
from rhetorica_backend.infra.security.passwords import (
    hash_password,
    password_hash_needs_rehash,
    verify_password,
)
from starlette.middleware.base import RequestResponseEndpoint

from rhetorica_api.app.settings import settings

WWW_AUTHENTICATE_HEADER = {"WWW-Authenticate": "Bearer"}
PUBLIC_PATH_PREFIXES = (
    "/healthz",
    "/docs",
    "/docs/",
    "/redoc",
    "/redoc/",
    "/openapi.json",
    "/favicon.ico",
    "/v1/auth/login",
    "/v1/auth/bootstrap/session",
    "/v1/auth/keycloak/session",
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-authorization-server",
    "/oauth/authorize",
    "/oauth/token",
    "/oauth/register",
    "/oauth/revoke",
)
DEV_ENV_NAMES = {"dev", "local", "test"}
KEYCLOAK_AUTH_PROVIDER = "keycloak"
logger = logging.getLogger(__name__)


def api_token_pepper() -> str:
    """Return the dedicated PAT digest pepper."""
    pepper = settings.auth_api_token_pepper.strip()
    if not pepper:
        raise RuntimeError("AUTH_API_TOKEN_PEPPER_required")
    return pepper


def validate_auth_token_peppers() -> None:
    """Reject missing or reused digest/signing secrets before serving traffic."""
    secrets_by_name = {
        "AUTH_JWT_SECRET": settings.auth_jwt_secret.strip(),
        "AUTH_API_TOKEN_PEPPER": settings.auth_api_token_pepper.strip(),
        "AUTH_OAUTH_TOKEN_PEPPER": settings.auth_oauth_token_pepper.strip(),
    }
    missing = [name for name, value in secrets_by_name.items() if not value]
    if missing:
        raise RuntimeError(f"{missing[0]}_required")
    if len(set(secrets_by_name.values())) != len(secrets_by_name):
        raise RuntimeError("auth_token_secrets_must_be_distinct")
    if settings.app_env not in DEV_ENV_NAMES and settings.mcp_enabled and not settings.auth_enabled:
        raise RuntimeError("MCP_requires_AUTH_ENABLED")


class AuthError(Exception):
    def __init__(
        self,
        detail: str,
        *,
        status_code: int = status.HTTP_401_UNAUTHORIZED,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.detail = detail
        self.status_code = status_code
        self.headers = headers if headers is not None else WWW_AUTHENTICATE_HEADER
        super().__init__(detail)


class DefaultAdminCredentialsError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Default admin/admin credentials are allowed only in dev/test.")


@dataclass(frozen=True)
class AuthContext:
    user: UserAccountItem
    issued_at: datetime
    expires_at: datetime
    scopes: frozenset[str] = frozenset({"*"})
    credential_type: str = "session"
    token_id: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.user.is_admin

    @property
    def user_id(self) -> str:
        return self.user.id

    @property
    def login(self) -> str:
        return self.user.login

    @property
    def auth_provider(self) -> str:
        return self.user.auth_provider

    @property
    def auth_subject(self) -> str:
        return self.user.auth_subject

    @property
    def workspace_id(self) -> str:
        return self.user.workspace_id

    @property
    def role(self) -> str:
        return self.user.role

    @property
    def is_bootstrap_user(self) -> bool:
        return self.user.is_bootstrap_user

    def has_scope(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes

    @property
    def is_personal_access_token(self) -> bool:
        return self.credential_type == "personal_access_token"

    @property
    def is_oauth_access_token(self) -> bool:
        return self.credential_type == "oauth_access_token"

    @property
    def is_limited_token(self) -> bool:
        return self.is_personal_access_token or self.is_oauth_access_token


_auth_context_var: ContextVar[AuthContext | None] = ContextVar(
    "rhetorica_api_auth_context",
    default=None,
)


@lru_cache(maxsize=1)
def _user_repository() -> UserAccountRepository:
    runtime = DatabaseRuntime(settings.database_url)
    runtime.create_schema()
    return UserAccountRepository(db=runtime)


@lru_cache(maxsize=1)
def _api_token_repository() -> PersonalAccessTokenRepository:
    runtime = DatabaseRuntime(settings.database_url)
    runtime.create_schema()
    return PersonalAccessTokenRepository(db=runtime)


def _build_auth_context(
    user: UserAccountItem,
    *,
    issued_at: datetime,
    expires_at: datetime,
    scopes: frozenset[str] = frozenset({"*"}),
    credential_type: str = "session",
    token_id: str | None = None,
) -> AuthContext:
    return AuthContext(
        user=user,
        issued_at=issued_at,
        expires_at=expires_at,
        scopes=scopes,
        credential_type=credential_type,
        token_id=token_id,
    )


def create_access_token_for_user(user: UserAccountItem) -> tuple[str, AuthContext]:
    now = datetime.now(tz=UTC)
    expires_at = now + timedelta(minutes=settings.auth_jwt_ttl_minutes)
    payload = {
        "sub": user.auth_subject,
        "uid": user.id,
        "login": user.login,
        "auth_provider": user.auth_provider,
        "workspace_id": user.workspace_id,
        "role": user.role,
        "iat": now,
        "exp": expires_at,
        "iss": settings.app_name,
    }
    token = jwt.encode(
        payload,
        settings.auth_jwt_secret,
        algorithm=settings.auth_jwt_algorithm,
    )
    return token, _build_auth_context(
        user,
        issued_at=now,
        expires_at=expires_at,
    )


def create_bootstrap_access_token() -> tuple[str, AuthContext]:
    user = _user_repository().ensure_bootstrap_user(settings.auth_default_workspace_id)
    touched_user = _user_repository().touch_last_seen(user_id=user.id)
    return create_access_token_for_user(touched_user or user)


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _normalized_keycloak_issuer_url() -> str:
    return settings.auth_keycloak_issuer_url.rstrip("/")


@lru_cache(maxsize=8)
def _keycloak_jwks_client(issuer_url: str) -> PyJWKClient:
    return PyJWKClient(f"{issuer_url}/protocol/openid-connect/certs")


def decode_keycloak_id_token(id_token: str) -> dict[str, Any]:
    if not settings.auth_keycloak_enabled:
        raise AuthError(
            "keycloak_auth_disabled",
            status_code=status.HTTP_404_NOT_FOUND,
            headers={},
        )
    issuer_url = _normalized_keycloak_issuer_url()
    if not issuer_url or not settings.auth_keycloak_client_id:
        raise AuthError(
            "keycloak_auth_misconfigured",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={},
        )
    try:
        signing_key = _keycloak_jwks_client(issuer_url).get_signing_key_from_jwt(id_token)
        return jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.auth_keycloak_client_id,
            issuer=issuer_url,
            options={"require": ["sub", "iat", "exp", "iss"]},
        )
    except ExpiredSignatureError as exc:
        raise AuthError("keycloak_token_expired") from exc
    except MissingRequiredClaimError as exc:
        raise AuthError(f"missing_keycloak_claim:{exc.claim}") from exc
    except InvalidTokenError as exc:
        raise AuthError("invalid_keycloak_token") from exc


def _claim_groups(claims: dict[str, Any]) -> set[str]:
    raw_groups = claims.get("groups", [])
    if not isinstance(raw_groups, list):
        return set()
    return {str(group) for group in raw_groups if str(group).strip()}


def _role_from_keycloak_groups(groups: set[str]) -> str:
    admin_groups = set(_split_csv(settings.auth_keycloak_admin_groups))
    analyst_groups = set(_split_csv(settings.auth_keycloak_analyst_groups))
    if groups & admin_groups:
        return ADMIN_ROLE
    if groups & analyst_groups:
        return ANALYST_ROLE
    if settings.auth_keycloak_require_mapped_group:
        raise AuthError(
            "keycloak_group_access_denied",
            status_code=status.HTTP_403_FORBIDDEN,
            headers={},
        )
    return ANALYST_ROLE


def _keycloak_login_from_claims(claims: dict[str, Any]) -> str:
    for claim_name in ("preferred_username", "email", "name", "sub"):
        value = claims.get(claim_name)
        if isinstance(value, str) and value.strip():
            return value
    raise AuthError("missing_keycloak_login_claim")


def create_keycloak_access_token(id_token: str) -> tuple[str, AuthContext]:
    claims = decode_keycloak_id_token(id_token)
    auth_subject = str(claims["sub"])
    groups = _claim_groups(claims)
    role = _role_from_keycloak_groups(groups)
    login = _keycloak_login_from_claims(claims)
    email = claims.get("email")
    display_name = claims.get("name")
    user = _user_repository().ensure_external_user(
        workspace_id=settings.auth_default_workspace_id,
        auth_provider=KEYCLOAK_AUTH_PROVIDER,
        auth_subject=auth_subject,
        login=login,
        role=role,
        email=email if isinstance(email, str) else None,
        display_name=display_name if isinstance(display_name, str) else login,
        is_active=True,
    )
    return create_access_token_for_user(user)


def decode_personal_access_token(token: str) -> AuthContext:
    prefix = api_token_prefix(token)
    if prefix is None:
        raise AuthError("invalid_personal_access_token")
    item = _api_token_repository().find_for_authentication(
        token_prefix=prefix,
        token_digest=digest_api_token(token, pepper=api_token_pepper()),
    )
    if item is None:
        raise AuthError("invalid_or_expired_personal_access_token")
    user = _user_repository().get_by_id(user_id=item.user_id)
    if user is None:
        raise AuthError("unknown_subject")
    if not user.is_active:
        raise AuthError("inactive_user", status_code=status.HTTP_403_FORBIDDEN, headers={})
    used_item = _api_token_repository().record_successful_use(token_id=item.id) or item
    return _build_auth_context(
        user,
        issued_at=used_item.created_at,
        expires_at=used_item.expires_at,
        scopes=frozenset(used_item.scopes),
        credential_type="personal_access_token",
        token_id=used_item.id,
    )


def decode_oauth_access_token(token: str) -> AuthContext:
    from rhetorica_api.app.oauth import oauth_repository, oauth_resource

    item = oauth_repository().find_access_token(
        access_token=token,
        resource=oauth_resource(),
    )
    if item is None:
        raise AuthError("invalid_or_expired_oauth_access_token")
    user = _user_repository().get_by_id(user_id=item.user_id)
    if user is None or user.workspace_id != item.workspace_id:
        raise AuthError("unknown_subject")
    if not user.is_active:
        raise AuthError("inactive_user", status_code=status.HTTP_403_FORBIDDEN, headers={})
    return _build_auth_context(
        user,
        issued_at=item.created_at,
        expires_at=item.access_expires_at,
        scopes=frozenset(item.scopes),
        credential_type="oauth_access_token",
        token_id=item.id,
    )


def decode_access_token(token: str) -> AuthContext:
    if token.startswith("rh_pat_"):
        return decode_personal_access_token(token)
    if token.startswith("rh_oat_"):
        return decode_oauth_access_token(token)
    try:
        payload = jwt.decode(
            token,
            settings.auth_jwt_secret,
            algorithms=[settings.auth_jwt_algorithm],
            issuer=settings.app_name,
            options={
                "require": [
                    "sub",
                    "auth_provider",
                    "workspace_id",
                    "role",
                    "iat",
                    "exp",
                    "iss",
                ]
            },
        )
    except ExpiredSignatureError as exc:
        raise AuthError("access_token_expired") from exc
    except MissingRequiredClaimError as exc:
        raise AuthError(f"missing_claim:{exc.claim}") from exc
    except InvalidTokenError as exc:
        raise AuthError("invalid_access_token") from exc

    auth_subject = str(payload["sub"])
    auth_provider = str(payload["auth_provider"])
    workspace_id = str(payload["workspace_id"])
    user = _user_repository().get_by_auth_identity(
        workspace_id=workspace_id,
        auth_provider=auth_provider,
        auth_subject=auth_subject,
    )
    if user is None:
        raise AuthError("unknown_subject")
    if not user.is_active:
        raise AuthError("inactive_user", status_code=status.HTTP_403_FORBIDDEN, headers={})

    issued_at = datetime.fromtimestamp(int(payload["iat"]), tz=UTC)
    expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
    return _build_auth_context(
        user,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def ensure_bootstrap_login_secret(secret: str | None) -> None:
    if not settings.auth_bootstrap_enabled:
        raise AuthError("bootstrap_auth_disabled", status_code=status.HTTP_404_NOT_FOUND, headers={})
    expected = settings.auth_bootstrap_login_secret
    provided = secret or ""
    if not expected or not secrets.compare_digest(provided, expected):
        raise AuthError(
            "invalid_bootstrap_secret",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


def bind_auth_context(auth_context: AuthContext) -> Token[AuthContext | None]:
    return _auth_context_var.set(auth_context)


def reset_auth_context(token: Token[AuthContext | None]) -> None:
    _auth_context_var.reset(token)


def get_current_auth_context() -> AuthContext:
    auth_context = _auth_context_var.get()
    if auth_context is None:
        raise AuthError("missing_auth_context")
    return auth_context


def ensure_workspace_access(workspace_id: str, auth_context: AuthContext | None = None) -> str:
    current_auth = auth_context or get_current_auth_context()
    if workspace_id != current_auth.workspace_id:
        raise AuthError(
            "workspace_access_denied",
            status_code=status.HTTP_403_FORBIDDEN,
            headers={},
        )
    return workspace_id


def auth_cookie_kwargs() -> dict[str, Any]:
    max_age = settings.auth_jwt_ttl_minutes * 60
    return {
        "httponly": True,
        "secure": settings.auth_cookie_secure,
        "samesite": settings.auth_cookie_samesite,
        "max_age": max_age,
        "path": "/",
    }


def set_auth_cookie(response: Response, access_token: str) -> None:
    response.set_cookie(
        settings.auth_cookie_name,
        access_token,
        **auth_cookie_kwargs(),
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.auth_cookie_name,
        path="/",
    )


def extract_access_token_from_request(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()

    cookie_token = request.cookies.get(settings.auth_cookie_name)
    if cookie_token:
        return cookie_token

    raise AuthError("not_authenticated")


def authenticate_http_request(request: Request) -> AuthContext:
    return decode_access_token(extract_access_token_from_request(request))


def require_scope(auth_context: AuthContext, scope: str) -> None:
    if not auth_context.has_scope(scope):
        headers = {"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{scope}"'}
        if auth_context.is_oauth_access_token:
            from rhetorica_api.app.oauth import oauth_resource_metadata_url

            headers["WWW-Authenticate"] = (
                f'Bearer error="insufficient_scope", scope="{scope}", '
                f'resource_metadata="{oauth_resource_metadata_url()}"'
            )
        raise AuthError(
            "insufficient_token_scope",
            status_code=status.HTTP_403_FORBIDDEN,
            headers=headers,
        )


def _mcp_scope_from_payload(payload: object) -> str:
    if isinstance(payload, list):
        required = {_mcp_scope_from_payload(item) for item in payload}
        return "mcp:execute" if "mcp:execute" in required else "mcp:read"
    if not isinstance(payload, dict):
        return "mcp:read"
    if payload.get("method") != "tools/call":
        return "mcp:read"
    params = payload.get("params")
    tool_name = params.get("name") if isinstance(params, dict) else None
    if tool_name in {"list_agents", "list_connectors", "get_conversation", "list_conversations"}:
        return "mcp:read"
    return "mcp:execute"


async def validate_token_scope(request: Request, auth_context: AuthContext) -> None:
    path = request.url.path.rstrip("/") or "/"
    if path == "/mcp" or path.startswith("/mcp/"):
        if not auth_context.is_limited_token:
            from rhetorica_api.app.oauth import oauth_resource_metadata_url

            raise AuthError(
                "mcp_integration_credential_required",
                headers={
                    "WWW-Authenticate": (
                        'Bearer error="invalid_token", scope="mcp:read mcp:execute", '
                        f'resource_metadata="{oauth_resource_metadata_url()}"'
                    )
                },
            )
        if request.method in {"HEAD", "OPTIONS"}:
            return
        payload: object = None
        body = await request.body()
        if body:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = None
        require_scope(auth_context, _mcp_scope_from_payload(payload))
        return
    if not auth_context.is_limited_token:
        return
    if path.startswith("/v1/"):
        required = "api:read" if request.method in {"GET", "HEAD", "OPTIONS"} else "api:write"
        require_scope(auth_context, required)


def is_public_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES)


async def validate_http_workspace_binding(request: Request, auth_context: AuthContext) -> None:
    query_workspace_id = request.query_params.get("workspace_id")
    if query_workspace_id:
        ensure_workspace_access(query_workspace_id, auth_context)

    if request.method not in {"POST", "PUT", "PATCH"}:
        return

    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type.lower():
        return

    body = await request.body()
    if not body:
        return

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return

    if isinstance(payload, dict):
        workspace_id = payload.get("workspace_id")
        if isinstance(workspace_id, str) and workspace_id:
            ensure_workspace_access(workspace_id, auth_context)


def authenticate_local_user(*, login: str, password: str) -> UserAccountItem:
    workspace_id = settings.auth_default_workspace_id
    user = _user_repository().get_by_login(workspace_id=workspace_id, login=login)
    if user is None or user.auth_provider != LOCAL_AUTH_PROVIDER or user.password_hash is None:
        raise AuthError("invalid_credentials")
    if not user.is_active:
        raise AuthError("inactive_user", status_code=status.HTTP_403_FORBIDDEN, headers={})
    if not verify_password(user.password_hash, password):
        raise AuthError("invalid_credentials")

    if password_hash_needs_rehash(user.password_hash):
        updated = _user_repository().update_password_hash(
            user_id=user.id,
            password_hash=hash_password(password),
        )
        if updated is not None:
            user = updated

    touched_user = _user_repository().touch_last_seen(user_id=user.id)
    return touched_user or user


def seed_local_admin_account() -> None:
    login = settings.auth_local_admin_login.strip()
    password = settings.auth_local_admin_password
    if not login or not password:
        logger.warning("auth.local_admin_seed_skipped_missing_credentials")
        return

    if login == "admin" and password == login:
        if settings.app_env not in DEV_ENV_NAMES:
            raise DefaultAdminCredentialsError()
        logger.warning("auth.local_admin_seed_uses_default_dev_credentials")

    user, created = _user_repository().ensure_local_admin(
        workspace_id=settings.auth_default_workspace_id,
        login=login,
        password_hash=hash_password(password),
        display_name=settings.auth_local_admin_display_name or login,
    )
    if created:
        logger.info(
            "auth.local_admin_seed_created",
            extra={"login": user.login, "workspace_id": user.workspace_id},
        )


def require_admin_access() -> AuthContext:
    if not settings.auth_enabled:
        now = datetime.now(tz=UTC)
        return _build_auth_context(
            _user_repository().ensure_bootstrap_user(settings.auth_default_workspace_id),
            issued_at=now,
            expires_at=now + timedelta(minutes=settings.auth_jwt_ttl_minutes),
        )
    auth_context = get_current_auth_context()
    if not auth_context.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin_access_denied")
    return auth_context


def install_auth(app: FastAPI) -> None:
    @app.middleware("http")
    async def auth_middleware(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if not settings.auth_enabled or is_public_path(request.url.path):
            return await call_next(request)

        try:
            auth_context = authenticate_http_request(request)
            await validate_token_scope(request, auth_context)
            await validate_http_workspace_binding(request, auth_context)
        except AuthError as exc:
            headers = exc.headers
            path = request.url.path.rstrip("/") or "/"
            if exc.status_code == status.HTTP_401_UNAUTHORIZED and (
                path == "/mcp" or path.startswith("/mcp/")
            ):
                from rhetorica_api.app.oauth import oauth_resource_metadata_url

                headers = {
                    "WWW-Authenticate": (
                        'Bearer error="invalid_token", scope="mcp:read mcp:execute", '
                        f'resource_metadata="{oauth_resource_metadata_url()}"'
                    )
                }
            return JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers=headers,
            )

        request.state.auth_context = auth_context
        token = bind_auth_context(auth_context)
        try:
            return await call_next(request)
        finally:
            reset_auth_context(token)
