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
from fastapi import HTTPException, Request, WebSocket, status
from fastapi.responses import JSONResponse, Response
from jwt import (
    ExpiredSignatureError,
    InvalidTokenError,
    MissingRequiredClaimError,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import (
    LOCAL_AUTH_PROVIDER,
    UserAccountItem,
    UserAccountRepository,
)
from rhetorica_backend.infra.security.passwords import (
    hash_password,
    password_hash_needs_rehash,
    verify_password,
)

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
)
DEV_ENV_NAMES = {"dev", "local", "test"}
logger = logging.getLogger(__name__)


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
        self.headers = headers or WWW_AUTHENTICATE_HEADER
        super().__init__(detail)


class DefaultAdminCredentialsError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Default admin/admin credentials are allowed only in dev/test.")


@dataclass(frozen=True)
class AuthContext:
    user: UserAccountItem
    issued_at: datetime
    expires_at: datetime

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


_auth_context_var: ContextVar[AuthContext | None] = ContextVar(
    "rhetorica_api_auth_context",
    default=None,
)


@lru_cache(maxsize=1)
def _user_repository() -> UserAccountRepository:
    runtime = DatabaseRuntime(settings.database_url)
    runtime.create_schema()
    return UserAccountRepository(db=runtime)


def _build_auth_context(
    user: UserAccountItem,
    *,
    issued_at: datetime,
    expires_at: datetime,
) -> AuthContext:
    return AuthContext(
        user=user,
        issued_at=issued_at,
        expires_at=expires_at,
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


def decode_access_token(token: str) -> AuthContext:
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


def extract_access_token_from_websocket(websocket: WebSocket) -> str:
    authorization = websocket.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()

    query_token = websocket.query_params.get("token")
    if query_token:
        return query_token

    cookie_token = websocket.cookies.get(settings.auth_cookie_name)
    if cookie_token:
        return cookie_token

    raise AuthError("not_authenticated")


def authenticate_websocket(websocket: WebSocket) -> AuthContext:
    return decode_access_token(extract_access_token_from_websocket(websocket))


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


def install_auth(app) -> None:
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if not settings.auth_enabled or is_public_path(request.url.path):
            return await call_next(request)

        try:
            auth_context = authenticate_http_request(request)
            await validate_http_workspace_binding(request, auth_context)
        except AuthError as exc:
            return JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers=exc.headers,
            )

        request.state.auth_context = auth_context
        token = bind_auth_context(auth_context)
        try:
            return await call_next(request)
        finally:
            reset_auth_context(token)
