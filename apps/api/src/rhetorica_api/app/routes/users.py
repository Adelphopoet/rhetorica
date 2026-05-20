from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from rhetorica_backend.infra.postgres.user_repository import (
    LOCAL_AUTH_PROVIDER,
    SUPPORTED_LOCAL_USER_ROLES,
    UserAccountRepository,
)
from rhetorica_backend.infra.security.passwords import hash_password
from sqlalchemy.exc import IntegrityError

from rhetorica_api.app.auth import AuthContext, require_admin_access
from rhetorica_api.app.identity import get_db_runtime

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


@router.delete("/{user_id}", status_code=204)
def delete_local_user(
    user_id: str,
    auth_context: Annotated[AuthContext, Depends(require_admin_access)],
) -> None:
    if auth_context.user.id == user_id:
        raise HTTPException(status_code=400, detail="cannot_delete_current_user")
    deleted = _repository().delete_local_user(
        workspace_id=auth_context.workspace_id,
        user_id=user_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="user_not_found")
