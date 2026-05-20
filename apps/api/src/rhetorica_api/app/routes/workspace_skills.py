from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from rhetorica_backend.contracts import (
    DeleteWorkspaceSkillResponse,
    UpsertWorkspaceSkillRequest,
    WorkspaceSkillItem,
)
from rhetorica_backend.domain.workspace_skills.service import (
    WorkspaceSkillService,
    WorkspaceSkillValidationError,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.workspace_skill_repository import (
    WorkspaceSkillRepository,
)
from sqlalchemy.exc import IntegrityError

from rhetorica_api.app.auth import require_admin_access
from rhetorica_api.app.settings import settings

router = APIRouter(
    prefix="/v1/admin/skills",
    tags=["workspace-skills"],
    dependencies=[Depends(require_admin_access)],
)


@lru_cache(maxsize=1)
def _service() -> WorkspaceSkillService:
    db_runtime = DatabaseRuntime(settings.database_url)
    db_runtime.create_schema()
    return WorkspaceSkillService(WorkspaceSkillRepository(db=db_runtime))


@router.get("", response_model=list[WorkspaceSkillItem])
def list_workspace_skills(
    workspace_id: str = Query(default="default", min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[WorkspaceSkillItem]:
    return _service().list(workspace_id=workspace_id, limit=limit)


@router.post("", response_model=WorkspaceSkillItem)
def create_workspace_skill(payload: UpsertWorkspaceSkillRequest) -> WorkspaceSkillItem:
    try:
        return _service().create(payload)
    except WorkspaceSkillValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="workspace_skill_name_conflict") from exc


@router.put("/{skill_id}", response_model=WorkspaceSkillItem)
def update_workspace_skill(
    skill_id: str,
    payload: UpsertWorkspaceSkillRequest,
    workspace_id: str = Query(default="default", min_length=1),
) -> WorkspaceSkillItem:
    try:
        updated = _service().update(
            skill_id=skill_id,
            workspace_id=workspace_id,
            request=payload.model_copy(update={"workspace_id": workspace_id}),
        )
    except WorkspaceSkillValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="workspace_skill_name_conflict") from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="workspace_skill_not_found")
    return updated


@router.delete("/{skill_id}", response_model=DeleteWorkspaceSkillResponse)
def delete_workspace_skill(
    skill_id: str,
    workspace_id: str = Query(default="default", min_length=1),
) -> DeleteWorkspaceSkillResponse:
    deleted = _service().delete(skill_id=skill_id, workspace_id=workspace_id)
    return DeleteWorkspaceSkillResponse(skill_id=skill_id, deleted=deleted)
