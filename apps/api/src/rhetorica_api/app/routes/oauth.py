from __future__ import annotations

import json
import logging
from typing import Any, NoReturn, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from rhetorica_backend.oauth.service import OAuthProtocolError, OAuthTokenResult

from rhetorica_api.app.auth import get_current_auth_context
from rhetorica_api.app.oauth import (
    oauth_issuer,
    oauth_resource,
    oauth_service,
)
from rhetorica_api.app.settings import settings

router = APIRouter(tags=["oauth"])
logger = logging.getLogger(__name__)


class ConsentResponse(BaseModel):
    redirect_uri: str


def _oauth_error(exc: OAuthProtocolError) -> JSONResponse:
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    if exc.authenticate:
        headers["WWW-Authenticate"] = 'Basic realm="oauth/token"'
    if exc.retry_after_seconds is not None:
        headers["Retry-After"] = str(exc.retry_after_seconds)
    return JSONResponse(
        {"error": exc.error, "error_description": exc.description},
        status_code=exc.status_code,
        headers=headers,
    )


def _token_payload(result: OAuthTokenResult) -> dict[str, object]:
    return {
        "access_token": result.access_token,
        "token_type": "Bearer",
        "expires_in": result.expires_in,
        "refresh_token": result.refresh_token,
        "scope": " ".join(result.scopes),
    }


async def _registration_payload(request: Request) -> dict[str, object]:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OAuthProtocolError(
            "invalid_client_metadata",
            "Registration request must contain valid JSON",
        ) from exc
    if not isinstance(payload, dict):
        raise OAuthProtocolError("invalid_client_metadata", "JSON object required")
    return payload


def _required(form: dict[str, str], field: str, description: str) -> str:
    value = form.get(field)
    if not value:
        raise OAuthProtocolError("invalid_request", description)
    return value


def _unsupported_grant_type() -> NoReturn:
    raise OAuthProtocolError("unsupported_grant_type", "Unsupported grant_type")


@router.get("/.well-known/oauth-protected-resource/mcp")
@router.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata() -> JSONResponse:
    return JSONResponse(
        {
            "resource": oauth_resource(),
            "authorization_servers": [oauth_issuer()],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["mcp:read", "mcp:execute"],
            "resource_documentation": f"{oauth_issuer()}/docs#/mcp",
        },
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata() -> JSONResponse:
    issuer = oauth_issuer()
    metadata: dict[str, object] = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "registration_endpoint": f"{issuer}/oauth/register",
        "revocation_endpoint": f"{issuer}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "none",
            "client_secret_basic",
            "client_secret_post",
        ],
        "revocation_endpoint_auth_methods_supported": [
            "none",
            "client_secret_basic",
            "client_secret_post",
        ],
        "scopes_supported": ["mcp:read", "mcp:execute"],
    }
    if settings.auth_oauth_cimd_enabled:
        metadata["client_id_metadata_document_supported"] = True
    return JSONResponse(
        metadata,
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/oauth/authorize")
async def authorize(
    response_type: str | None = Query(default=None),
    client_id: str | None = Query(default=None),
    redirect_uri: str | None = Query(default=None),
    scope: str | None = Query(default=None),
    state: str | None = Query(default=None),
    code_challenge: str | None = Query(default=None),
    code_challenge_method: str | None = Query(default=None),
    resource: str | None = Query(default=None),
) -> Response:
    if not all(
        [
            response_type,
            client_id,
            redirect_uri,
            code_challenge,
            code_challenge_method,
            resource,
        ]
    ):
        return _oauth_error(OAuthProtocolError("invalid_request", "Required parameter missing"))
    try:
        consent_url = await oauth_service().start_authorization(
            response_type=cast(str, response_type),
            client_id=cast(str, client_id),
            redirect_uri=cast(str, redirect_uri),
            scope=scope,
            state=state,
            code_challenge=cast(str, code_challenge),
            code_challenge_method=cast(str, code_challenge_method),
            resource=cast(str, resource),
        )
    except OAuthProtocolError as exc:
        return _oauth_error(exc)
    return RedirectResponse(consent_url, status_code=302, headers={"Cache-Control": "no-store"})


@router.post("/oauth/register", status_code=201)
async def register(request: Request) -> JSONResponse:
    try:
        payload = await _registration_payload(request)
        source = request.client.host if request.client is not None else "unknown"
        result = oauth_service().register_client(payload, source=source)
    except OAuthProtocolError as exc:
        return _oauth_error(exc)
    except Exception:
        logger.exception("oauth_client_registration_failed")
        return _oauth_error(
            OAuthProtocolError(
                "server_error",
                "Client registration failed",
                status_code=500,
            )
        )
    client = result.client
    body: dict[str, Any] = {
        "client_id": client.client_id,
        "client_id_issued_at": int(client.created_at.timestamp()),
        "client_name": client.client_name,
        "client_uri": client.client_uri,
        "redirect_uris": list(client.redirect_uris),
        "grant_types": list(client.grant_types),
        "response_types": list(client.response_types),
        "token_endpoint_auth_method": client.token_endpoint_auth_method,
    }
    if result.client_secret:
        body["client_secret"] = result.client_secret
        body["client_secret_expires_at"] = 0
    return JSONResponse(
        body,
        status_code=201,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


async def _form_values(request: Request) -> dict[str, str]:
    form = await request.form()
    return {key: str(value) for key, value in form.multi_items()}


def _client_credentials(
    request: Request, form: dict[str, str]
) -> tuple[str, str | None, str]:
    basic_id, basic_secret = oauth_service().parse_basic_client(
        request.headers.get("Authorization")
    )
    body_id = form.get("client_id")
    body_secret = form.get("client_secret")
    if basic_id and body_id and basic_id != body_id:
        raise OAuthProtocolError("invalid_client", "Conflicting client identity", status_code=401)
    client_id = basic_id or body_id
    if not client_id:
        raise OAuthProtocolError("invalid_client", "client_id is required", status_code=401)
    if basic_id:
        return client_id, basic_secret, "client_secret_basic"
    if body_secret:
        return client_id, body_secret, "client_secret_post"
    return client_id, None, "none"


@router.post("/oauth/token")
async def token(request: Request) -> JSONResponse:
    try:
        form = await _form_values(request)
        client_id, client_secret, client_auth_method = _client_credentials(request, form)
        grant_type = form.get("grant_type")
        resource = _required(form, "resource", "resource is required")
        if grant_type == "authorization_code":
            result = await oauth_service().exchange_code(
                client_id=client_id,
                client_secret=client_secret,
                client_auth_method=client_auth_method,
                code=_required(form, "code", "Code parameters are required"),
                redirect_uri=_required(form, "redirect_uri", "Code parameters are required"),
                code_verifier=_required(form, "code_verifier", "Code parameters are required"),
                resource=resource,
            )
        elif grant_type == "refresh_token":
            result = await oauth_service().refresh(
                client_id=client_id,
                client_secret=client_secret,
                client_auth_method=client_auth_method,
                refresh_token=_required(form, "refresh_token", "refresh_token is required"),
                resource=resource,
                scope=form.get("scope"),
            )
        else:
            _unsupported_grant_type()
    except OAuthProtocolError as exc:
        return _oauth_error(exc)
    return JSONResponse(
        _token_payload(result),
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/oauth/revoke")
async def revoke(request: Request) -> JSONResponse:
    try:
        form = await _form_values(request)
        client_id, client_secret, client_auth_method = _client_credentials(request, form)
        await oauth_service().revoke(
            client_id=client_id,
            client_secret=client_secret,
            client_auth_method=client_auth_method,
            token=_required(form, "token", "token is required"),
        )
    except OAuthProtocolError as exc:
        return _oauth_error(exc)
    return JSONResponse({}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/v1/oauth/consent/{transaction_token}")
def consent_details(transaction_token: str) -> JSONResponse:
    try:
        item = oauth_service().get_consent(transaction_token=transaction_token)
    except OAuthProtocolError as exc:
        return _oauth_error(exc)
    redirect = urlsplit(item.redirect_uri)
    return JSONResponse(
        {
            "client": {
                "id": item.client_id,
                "name": item.client_name,
                "uri": item.client_uri,
            },
            "redirect_host": redirect.hostname,
            "scopes": list(item.scopes),
            "resource": item.resource,
            "expires_at": item.expires_at.isoformat(),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/v1/oauth/consent/{transaction_token}/approve",
    response_model=ConsentResponse,
)
def approve_consent(transaction_token: str) -> dict[str, str] | JSONResponse:
    auth_context = get_current_auth_context()
    if auth_context.credential_type != "session":
        return JSONResponse({"detail": "session_credential_required"}, status_code=403)
    try:
        redirect_uri = oauth_service().approve_consent(
            transaction_token=transaction_token,
            user_id=auth_context.user_id,
            workspace_id=auth_context.workspace_id,
        )
    except OAuthProtocolError as exc:
        return _oauth_error(exc)
    return {"redirect_uri": redirect_uri}


@router.post(
    "/v1/oauth/consent/{transaction_token}/deny",
    response_model=ConsentResponse,
)
def deny_consent(transaction_token: str) -> dict[str, str] | JSONResponse:
    auth_context = get_current_auth_context()
    if auth_context.credential_type != "session":
        return JSONResponse({"detail": "session_credential_required"}, status_code=403)
    try:
        redirect_uri = oauth_service().deny_consent(transaction_token=transaction_token)
    except OAuthProtocolError as exc:
        return _oauth_error(exc)
    return {"redirect_uri": redirect_uri}
