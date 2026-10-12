from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from rhetorica_backend.domain.rate_limits import RateLimitUnavailable
from rhetorica_backend.infra.logging import get_logger
from rhetorica_backend.infra.postgres.user_repository import (
    LOCAL_AUTH_PROVIDER,
    SUPPORTED_LOCAL_USER_ROLES,
    UserAccountRepository,
)
from rhetorica_backend.infra.security.passwords import hash_password
from sqlalchemy.exc import IntegrityError

from rhetorica_api.app.auth import AuthContext, require_admin_access
from rhetorica_api.app.identity import get_db_runtime
from rhetorica_api.app.rate_limits import api_rate_limit_service

logger = get_logger(__name__)

router = APIRouter(
    prefix="/v1/admin/users",
    tags=["users"],
)


class CreateLocalUserRequest(BaseModel):
    login: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="admin", min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=255)


class LocalUserItem(BaseModel):
    id: str
    workspace_id: str
    login: str
    role: str
    is_active: bool
    auth_provider: str
    auth_subject: str
    display_name: str | None
    api_rate_limit_per_hour: int | None
    created_at: str
    updated_at: str


def _repository() -> UserAccountRepository:
    return UserAccountRepository(db=get_db_runtime())


def _to_response(user) -> LocalUserItem:
    return LocalUserItem(
        id=user.id,
        workspace_id=user.workspace_id,
        login=user.login,
        role=user.role,
        is_active=user.is_active,
        auth_provider=user.auth_provider,
        auth_subject=user.auth_subject,
        display_name=user.display_name,
        api_rate_limit_per_hour=user.api_rate_limit_per_hour,
        created_at=user.created_at.isoformat(),
        updated_at=user.updated_at.isoformat(),
    )


@router.get("", response_model=list[LocalUserItem])
def list_local_users(
    auth_context: Annotated[AuthContext, Depends(require_admin_access)],
) -> list[LocalUserItem]:
    users = _repository().list_by_workspace(workspace_id=auth_context.workspace_id)
    return [_to_response(user) for user in users if user.auth_provider == LOCAL_AUTH_PROVIDER]


@router.post("", response_model=LocalUserItem)
def create_local_user(
    payload: CreateLocalUserRequest,
    auth_context: Annotated[AuthContext, Depends(require_admin_access)],
) -> LocalUserItem:
    if payload.role not in SUPPORTED_LOCAL_USER_ROLES:
        raise HTTPException(status_code=400, detail="unsupported_user_role")
    try:
        user = _repository().create_local_user(
            workspace_id=auth_context.workspace_id,
            login=payload.login,
            password_hash=hash_password(payload.password),
            role=payload.role,
            display_name=payload.display_name,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="user_login_conflict") from exc
    return _to_response(user)


class UpdateApiRateLimitRequest(BaseModel):
    requests_per_hour: int = Field(gt=0, strict=True)


def _local_user_or_404(
    *,
    repository: UserAccountRepository,
    workspace_id: str,
    user_id: str,
):
    user = repository.get_by_id(user_id=user_id)
    if (
        user is None
        or user.workspace_id != workspace_id
        or user.auth_provider != LOCAL_AUTH_PROVIDER
    ):
        raise HTTPException(status_code=404, detail="user_not_found")
    return user


@router.put("/{user_id}/rate-limit", response_model=LocalUserItem)
def update_local_user_rate_limit(
    user_id: str,
    payload: UpdateApiRateLimitRequest,
    auth_context: Annotated[AuthContext, Depends(require_admin_access)],
) -> LocalUserItem:
    repository = _repository()
    _local_user_or_404(
        repository=repository,
        workspace_id=auth_context.workspace_id,
        user_id=user_id,
    )
    user = repository.update_api_rate_limit(
        workspace_id=auth_context.workspace_id,
        user_id=user_id,
        requests_per_hour=payload.requests_per_hour,
    )
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return _to_response(user)


@router.delete("/{user_id}/rate-limit", response_model=LocalUserItem)
async def disable_local_user_rate_limit(
    user_id: str,
    auth_context: Annotated[AuthContext, Depends(require_admin_access)],
) -> LocalUserItem:
    repository = _repository()
    user = _local_user_or_404(
        repository=repository,
        workspace_id=auth_context.workspace_id,
        user_id=user_id,
    )
    updated = repository.update_api_rate_limit(
        workspace_id=auth_context.workspace_id,
        user_id=user_id,
        requests_per_hour=None,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    try:
        await api_rate_limit_service().reset(
            workspace_id=user.workspace_id,
            user_id=user.id,
        )
    except RateLimitUnavailable:
        logger.warning(
            "api_rate_limit.disable_cleanup_failed",
            workspace_id=user.workspace_id,
            user_id=user.id,
        )
    return _to_response(updated)


@router.post("/{user_id}/rate-limit/reset", response_model=LocalUserItem)
async def reset_local_user_rate_limit(
    user_id: str,
    auth_context: Annotated[AuthContext, Depends(require_admin_access)],
) -> LocalUserItem:
    repository = _repository()
    user = _local_user_or_404(
        repository=repository,
        workspace_id=auth_context.workspace_id,
        user_id=user_id,
    )
    if user.api_rate_limit_per_hour is None:
        raise HTTPException(status_code=409, detail="api_rate_limit_not_enabled")
    try:
        await api_rate_limit_service().reset(
            workspace_id=user.workspace_id,
            user_id=user.id,
        )
    except RateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail="rate_limit_unavailable") from exc
    return _to_response(user)


@router.delete("/{user_id}", status_code=204)
async def delete_local_user(
    user_id: str,
    auth_context: Annotated[AuthContext, Depends(require_admin_access)],
) -> None:
    if auth_context.user.id == user_id:
        raise HTTPException(status_code=400, detail="cannot_delete_current_user")
    repository = _repository()
    user = _local_user_or_404(
        repository=repository,
        workspace_id=auth_context.workspace_id,
        user_id=user_id,
    )
    deleted = repository.delete_local_user(
        workspace_id=auth_context.workspace_id,
        user_id=user_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="user_not_found")
    if user.api_rate_limit_per_hour is not None:
        try:
            await api_rate_limit_service().reset(
                workspace_id=user.workspace_id,
                user_id=user.id,
            )
        except RateLimitUnavailable:
            logger.warning(
                "api_rate_limit.delete_cleanup_failed",
                workspace_id=user.workspace_id,
                user_id=user.id,
            )
