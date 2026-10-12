from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from rhetorica_backend.infra.postgres.api_token_repository import (
    PersonalAccessTokenItem,
)
from rhetorica_backend.infra.security.api_tokens import (
    issue_api_token,
    validate_api_token_request,
)

from rhetorica_api.app.auth import (
    AuthError,
    _api_token_repository,
    api_token_pepper,
    authenticate_local_user,
    clear_auth_cookie,
    create_access_token_for_user,
    create_bootstrap_access_token,
    create_keycloak_access_token,
    ensure_bootstrap_login_secret,
    get_current_auth_context,
    set_auth_cookie,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class KeycloakSessionRequest(BaseModel):
    id_token: str = Field(min_length=1)


ApiTokenScope = Literal["api:read", "api:write", "mcp:read", "mcp:execute"]


class CreateApiTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scopes: list[ApiTokenScope] = Field(default_factory=lambda: ["mcp:read", "mcp:execute"])
    lifetime_days: Literal[90, 180, 365] = 90
    auto_renew: bool = False


class ApiTokenItemResponse(BaseModel):
    id: str
    name: str
    token_hint: str
    scopes: list[str]
    lifetime_days: int
    auto_renew: bool
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    is_active: bool


class CreatedApiTokenResponse(ApiTokenItemResponse):
    token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - credential scheme, not a secret


def _session_payload(auth_context) -> dict[str, object]:
    return {
        "workspace_id": auth_context.workspace_id,
        "home_workspace_id": auth_context.home_workspace_id,
        "workspace_role": auth_context.workspace_role,
        "role": auth_context.role,
        "expires_at": auth_context.expires_at.isoformat(),
        "user": {
            "id": auth_context.user_id,
            "login": auth_context.login,
            "display_name": auth_context.user.display_name,
            "auth_provider": auth_context.auth_provider,
            "auth_subject": auth_context.auth_subject,
            "is_active": auth_context.user.is_active,
            "is_bootstrap_user": auth_context.is_bootstrap_user,
        },
    }


def _require_session_credential(auth_context) -> None:
    if auth_context.is_personal_access_token:
        raise HTTPException(status_code=403, detail="session_credential_required")


def _api_token_payload(item: PersonalAccessTokenItem) -> dict[str, object]:
    return {
        "id": item.id,
        "name": item.name,
        "token_hint": item.token_prefix[-8:],
        "scopes": list(item.scopes),
        "lifetime_days": item.lifetime_days,
        "auto_renew": item.auto_renew,
        "expires_at": item.expires_at,
        "last_used_at": item.last_used_at,
        "revoked_at": item.revoked_at,
        "created_at": item.created_at,
        "is_active": item.is_active,
    }


@router.post("/login")
def login(payload: LoginRequest) -> JSONResponse:
    try:
        user = authenticate_local_user(login=payload.login, password=payload.password)
    except AuthError as exc:
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    access_token, auth_context = create_access_token_for_user(user)
    response = JSONResponse(
        {
            "access_token": access_token,
            "token_type": "bearer",
            **_session_payload(auth_context),
        }
    )
    set_auth_cookie(response, access_token)
    return response


@router.post("/keycloak/session")
def keycloak_session(payload: KeycloakSessionRequest) -> JSONResponse:
    try:
        access_token, auth_context = create_keycloak_access_token(payload.id_token)
    except AuthError as exc:
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    response = JSONResponse(
        {
            "access_token": access_token,
            "token_type": "bearer",
            **_session_payload(auth_context),
        }
    )
    set_auth_cookie(response, access_token)
    return response


@router.post("/bootstrap/session")
def bootstrap_session(
    x_rhetorica_bootstrap_secret: str | None = Header(
        default=None,
        alias="X-Rhetorica-Bootstrap-Secret",
    ),
) -> JSONResponse:
    try:
        ensure_bootstrap_login_secret(x_rhetorica_bootstrap_secret)
    except AuthError as exc:
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    access_token, auth_context = create_bootstrap_access_token()
    response = JSONResponse(
        {
            "access_token": access_token,
            "token_type": "bearer",
            **_session_payload(auth_context),
        }
    )
    set_auth_cookie(response, access_token)
    return response


@router.get("/session")
def current_session() -> dict[str, object]:
    auth_context = get_current_auth_context()
    return _session_payload(auth_context)


@router.get("/tokens", response_model=list[ApiTokenItemResponse])
def list_api_tokens(response: Response) -> list[dict[str, object]]:
    auth_context = get_current_auth_context()
    _require_session_credential(auth_context)
    response.headers["Cache-Control"] = "no-store"
    return [
        _api_token_payload(item)
        for item in _api_token_repository().list_for_user(user_id=auth_context.user_id)
    ]


@router.post(
    "/tokens",
    response_model=CreatedApiTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_api_token(payload: CreateApiTokenRequest, response: Response) -> dict[str, object]:
    auth_context = get_current_auth_context()
    _require_session_credential(auth_context)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    try:
        scopes = validate_api_token_request(
            scopes=list(payload.scopes),
            lifetime_days=payload.lifetime_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    issued = issue_api_token(pepper=api_token_pepper())
    item = _api_token_repository().create(
        user_id=auth_context.user_id,
        workspace_id=auth_context.workspace_id,
        name=payload.name,
        token_prefix=issued.prefix,
        token_digest=issued.digest,
        scopes=scopes,
        lifetime_days=payload.lifetime_days,
        auto_renew=payload.auto_renew,
    )
    return {**_api_token_payload(item), "token": issued.token, "token_type": "bearer"}


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_token(token_id: str) -> Response:
    auth_context = get_current_auth_context()
    _require_session_credential(auth_context)
    if not _api_token_repository().revoke(token_id=token_id, user_id=auth_context.user_id):
        raise HTTPException(status_code=404, detail="api_token_not_found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"status": "logged_out"})
    clear_auth_cookie(response)
    return response
