from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from rhetorica_backend.contracts.workspaces import (
    CreateWorkspaceRequest,
    CreateWorkspaceResourceGrantRequest,
    UpsertWorkspaceMembershipRequest,
    WorkspaceItem,
    WorkspaceMembershipItem,
    WorkspacePolicyAuditEventItem,
    WorkspaceResourceCatalogItem,
    WorkspaceResourceGrantItem,
)
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.postgres.workspace_repository import (
    WorkspaceConflictError,
    WorkspaceRepository,
)

from rhetorica_api.app.auth import (
    AuthContext,
    AuthError,
    create_access_token_for_user,
    require_admin_access,
    require_authenticated_access,
)
from rhetorica_api.app.identity import get_db_runtime
from rhetorica_api.app.settings import settings

router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])
admin_router = APIRouter(prefix="/v1/admin/workspaces", tags=["workspaces"])


class WorkspaceAccessItem(BaseModel):
    workspace: WorkspaceItem
    membership: WorkspaceMembershipItem
    is_default: bool


class WorkspaceSelectionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - credential scheme
    workspace_id: str
    home_workspace_id: str
    workspace_role: str
    expires_at: str


class WorkspaceUserItem(BaseModel):
    id: str
    home_workspace_id: str
    login: str
    display_name: str | None
    system_role: str
    is_active: bool


def _repository() -> WorkspaceRepository:
    return WorkspaceRepository(
        db=get_db_runtime(),
        default_workspace_id=settings.auth_default_workspace_id,
    )


def _require_owner_or_admin(auth_context: AuthContext, workspace_id: str) -> None:
    if auth_context.is_admin:
        return
    membership = _repository().membership(
        workspace_id=workspace_id,
        user_id=auth_context.user_id,
    )
    if membership is None or membership.role != "owner":
        raise HTTPException(status_code=403, detail="workspace_owner_access_denied")


@router.get("", response_model=list[WorkspaceAccessItem])
def list_workspaces(
    auth_context: Annotated[AuthContext, Depends(require_authenticated_access)],
) -> list[WorkspaceAccessItem]:
    return [
        WorkspaceAccessItem(
            workspace=workspace,
            membership=membership,
            is_default=workspace.id == settings.auth_default_workspace_id,
        )
        for workspace, membership in _repository().list_for_user(user_id=auth_context.user_id)
    ]


@router.get("/user-directory", response_model=list[WorkspaceUserItem])
def list_workspace_user_directory(
    auth_context: Annotated[AuthContext, Depends(require_authenticated_access)],
) -> list[WorkspaceUserItem]:
    _require_owner_or_admin(auth_context, auth_context.workspace_id)
    return _workspace_users()


@router.post("/{workspace_id}/select", response_model=WorkspaceSelectionResponse)
def select_workspace(
    workspace_id: str,
    auth_context: Annotated[AuthContext, Depends(require_authenticated_access)],
) -> WorkspaceSelectionResponse:
    if auth_context.is_limited_token:
        raise HTTPException(status_code=403, detail="session_credential_required")
    try:
        token, selected = create_access_token_for_user(
            auth_context.user,
            active_workspace_id=workspace_id,
        )
    except AuthError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=str(exc), headers=exc.headers
        ) from exc
    if not _repository().remember_selected_workspace(
        user_id=auth_context.user_id, workspace_id=selected.workspace_id
    ):
        raise HTTPException(status_code=403, detail="workspace_access_denied")
    return WorkspaceSelectionResponse(
        access_token=token,
        workspace_id=selected.workspace_id,
        home_workspace_id=selected.home_workspace_id,
        workspace_role=selected.workspace_role or "viewer",
        expires_at=selected.expires_at.isoformat(),
    )


@admin_router.post("", response_model=WorkspaceItem, status_code=status.HTTP_201_CREATED)
def create_workspace(
    payload: CreateWorkspaceRequest,
    auth_context: Annotated[AuthContext, Depends(require_admin_access)],
) -> WorkspaceItem:
    try:
        return _repository().create(payload, actor_user_id=auth_context.user_id)
    except WorkspaceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@admin_router.get("/users", response_model=list[WorkspaceUserItem])
def list_workspace_users(
    _: Annotated[AuthContext, Depends(require_admin_access)],
) -> list[WorkspaceUserItem]:
    return _workspace_users()


def _workspace_users() -> list[WorkspaceUserItem]:
    return [
        WorkspaceUserItem(
            id=user.id,
            home_workspace_id=user.home_workspace_id,
            login=user.login,
            display_name=user.display_name,
            system_role=user.role,
            is_active=user.is_active,
        )
        for user in UserAccountRepository(db=get_db_runtime()).list_all()
    ]


@admin_router.get("/{workspace_id}/memberships", response_model=list[WorkspaceMembershipItem])
def list_memberships(
    workspace_id: str,
    auth_context: Annotated[AuthContext, Depends(require_authenticated_access)],
) -> list[WorkspaceMembershipItem]:
    _require_owner_or_admin(auth_context, workspace_id)
    return _repository().list_memberships(workspace_id=workspace_id)


@admin_router.put(
    "/{workspace_id}/memberships/{user_id}",
    response_model=WorkspaceMembershipItem,
)
def upsert_membership(
    workspace_id: str,
    user_id: str,
    payload: UpsertWorkspaceMembershipRequest,
    auth_context: Annotated[AuthContext, Depends(require_authenticated_access)],
) -> WorkspaceMembershipItem:
    _require_owner_or_admin(auth_context, workspace_id)
    if payload.user_id != user_id:
        raise HTTPException(status_code=400, detail="membership_user_id_mismatch")
    try:
        return _repository().upsert_membership(
            workspace_id=workspace_id,
            request=payload,
            actor_user_id=auth_context.user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkspaceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@admin_router.delete("/{workspace_id}/memberships/{user_id}", status_code=204)
def revoke_membership(
    workspace_id: str,
    user_id: str,
    auth_context: Annotated[AuthContext, Depends(require_authenticated_access)],
) -> Response:
    _require_owner_or_admin(auth_context, workspace_id)
    try:
        revoked = _repository().revoke_membership(
            workspace_id=workspace_id,
            user_id=user_id,
            actor_user_id=auth_context.user_id,
        )
    except WorkspaceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not revoked:
        raise HTTPException(status_code=404, detail="workspace_membership_not_found")
    return Response(status_code=204)


@admin_router.get(
    "/{workspace_id}/resource-grants", response_model=list[WorkspaceResourceGrantItem]
)
def list_resource_grants(
    workspace_id: str,
    auth_context: Annotated[AuthContext, Depends(require_authenticated_access)],
) -> list[WorkspaceResourceGrantItem]:
    _require_owner_or_admin(auth_context, workspace_id)
    return _repository().list_resource_grants(workspace_id=workspace_id)


@admin_router.get(
    "/{workspace_id}/local-resources",
    response_model=list[WorkspaceResourceCatalogItem],
)
def list_local_resources(
    workspace_id: str,
    auth_context: Annotated[AuthContext, Depends(require_authenticated_access)],
) -> list[WorkspaceResourceCatalogItem]:
    _require_owner_or_admin(auth_context, workspace_id)
    return _repository().list_local_resources(workspace_id=workspace_id)


@admin_router.post(
    "/{workspace_id}/resource-grants",
    response_model=WorkspaceResourceGrantItem,
    status_code=status.HTTP_201_CREATED,
)
def create_resource_grant(
    workspace_id: str,
    payload: CreateWorkspaceResourceGrantRequest,
    auth_context: Annotated[AuthContext, Depends(require_admin_access)],
) -> WorkspaceResourceGrantItem:
    if payload.recipient_workspace_id != workspace_id:
        raise HTTPException(status_code=400, detail="resource_grant_workspace_mismatch")
    try:
        return _repository().create_resource_grant(payload, actor_user_id=auth_context.user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkspaceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@admin_router.delete("/{workspace_id}/resource-grants/{grant_id}", status_code=204)
def revoke_resource_grant(
    workspace_id: str,
    grant_id: str,
    auth_context: Annotated[AuthContext, Depends(require_admin_access)],
) -> Response:
    if not _repository().revoke_resource_grant(
        grant_id=grant_id,
        workspace_id=workspace_id,
        actor_user_id=auth_context.user_id,
    ):
        raise HTTPException(status_code=404, detail="workspace_resource_grant_not_found")
    return Response(status_code=204)


@admin_router.get("/{workspace_id}/audit", response_model=list[WorkspacePolicyAuditEventItem])
def list_policy_audit(
    workspace_id: str,
    auth_context: Annotated[AuthContext, Depends(require_authenticated_access)],
    limit: int = 200,
) -> list[WorkspacePolicyAuditEventItem]:
    _require_owner_or_admin(auth_context, workspace_id)
    return _repository().list_audit(workspace_id=workspace_id, limit=min(max(limit, 1), 500))
