from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from rhetorica_api.app.auth import (
    AuthError,
    authenticate_local_user,
    clear_auth_cookie,
    create_access_token_for_user,
    create_bootstrap_access_token,
    ensure_bootstrap_login_secret,
    get_current_auth_context,
    set_auth_cookie,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


def _session_payload(auth_context) -> dict[str, object]:
    return {
        "workspace_id": auth_context.workspace_id,
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


@router.post("/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"status": "logged_out"})
    clear_auth_cookie(response)
    return response
