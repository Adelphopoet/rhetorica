from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from rhetorica_backend.contracts.coding import (
    EnvironmentProfile,
    UpsertEnvironmentProfileRequest,
)
from rhetorica_backend.infra.coding.environment_identity import CodingEnvironmentIdentity
from rhetorica_backend.infra.postgres.coding_environment_repository import (
    CodingEnvironmentConflictError,
    CodingEnvironmentRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime

from rhetorica_api.app.auth import AuthContext, require_admin_access
from rhetorica_api.app.settings import settings

router = APIRouter(
    prefix="/v1/admin/coding-environments",
    tags=["admin-coding-environments"],
    dependencies=[Depends(require_admin_access)],
)


@lru_cache(maxsize=1)
def _repository() -> CodingEnvironmentRepository:
    return CodingEnvironmentRepository(DatabaseRuntime(settings.database_url))


def _tool_versions(manifest_digest):
    return CodingEnvironmentIdentity(
        manifest_digest=manifest_digest,
        runtime_url=settings.execution_runtime_url
        if os.environ.get("CODING_RUNTIME_MODE") == "local_docker"
        else None,
        manifest_path=os.environ.get("CODING_RUNNER_SOFTWARE_MANIFEST_PATH"),
    ).tool_versions()


@router.get("", response_model=list[EnvironmentProfile])
def list_coding_environments() -> list[EnvironmentProfile]:
    return _repository().list_latest()


@router.put("/{profile_id}", response_model=EnvironmentProfile)
def replace_coding_environment(
    profile_id: str,
    payload: UpsertEnvironmentProfileRequest,
    auth_context: Annotated[AuthContext, Depends(require_admin_access)],
) -> EnvironmentProfile:
    if profile_id != payload.id:
        raise HTTPException(status_code=422, detail="coding_environment_id_mismatch")
    image_digest = settings.coding_runner_image_digest
    manifest_digest = settings.coding_runner_software_manifest_digest
    if "@sha256:" not in image_digest or len(manifest_digest) != 64:
        raise HTTPException(
            status_code=503, detail="coding_environment_runtime_identity_not_configured"
        )
    try:
        return _repository().replace(
            request=payload,
            actor_user_id=auth_context.user_id,
            image_digest=image_digest,
            runtime_class_name=settings.coding_runner_runtime_class_name,
            tool_versions=_tool_versions(manifest_digest),
            software_manifest_digest=manifest_digest,
        )
    except (ValueError, OSError, httpx.HTTPError) as exc:
        raise HTTPException(503, detail="coding_environment_runtime_identity_unavailable") from exc
    except CodingEnvironmentConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "reason_code": "coding_environment_revision_conflict",
                "current_revision": exc.current_revision,
            },
        ) from exc
