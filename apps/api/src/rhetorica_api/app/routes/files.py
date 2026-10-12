"""Thin streaming HTTP adapter for owned file artifacts."""

from __future__ import annotations

from collections.abc import Iterator
from time import perf_counter
from typing import Annotated

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from rhetorica_backend.contracts.files import FileArtifactDetail
from rhetorica_backend.domain.files.policy import ArtifactRejectedError
from rhetorica_backend.domain.files.service import (
    ArtifactConflictError,
    ArtifactNotFoundError,
)

from rhetorica_api.app.auth import get_current_auth_context
from rhetorica_api.app.file_runtime import get_artifact_service, get_file_telemetry
from rhetorica_api.app.identity import resolve_current_user
from rhetorica_api.app.settings import settings

router = APIRouter(prefix="/v1/files", tags=["files"])
_STREAM_CHUNK_BYTES = 1024 * 1024


def _current_user_id(workspace_id: str | None = None) -> str:
    if settings.auth_enabled:
        context = get_current_auth_context()
        if workspace_id is not None and context.workspace_id != workspace_id:
            raise HTTPException(status_code=403, detail="Workspace access denied")
        return context.user_id
    return resolve_current_user(workspace_id or settings.auth_default_workspace_id).id


def _active_workspace_id() -> str:
    if settings.auth_enabled:
        return get_current_auth_context().workspace_id
    return settings.auth_default_workspace_id


def _storage_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "file_processing_unavailable", "message": "Object storage is unavailable"},
    )


@router.post("", response_model=FileArtifactDetail, status_code=status.HTTP_201_CREATED)
def upload_file(
    workspace_id: Annotated[str, Form()],
    conversation_id: Annotated[str, Form()],
    upload: Annotated[UploadFile, File(alias="file")],
) -> FileArtifactDetail:
    user_id = _current_user_id(workspace_id)
    try:
        artifact = get_artifact_service().stage_upload(
            workspace_id=workspace_id,
            owner_user_id=user_id,
            conversation_id=conversation_id,
            filename=upload.filename or "",
            declared_media_type=upload.content_type,
            fileobj=upload.file,
        )
        return get_artifact_service().to_detail(artifact)
    except ArtifactRejectedError as exc:
        raise HTTPException(
            status_code=422, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    except (BotoCoreError, ClientError, OSError) as exc:
        raise _storage_unavailable(exc) from exc
    finally:
        upload.file.close()


@router.get("/{artifact_id}", response_model=FileArtifactDetail)
def get_file(artifact_id: str) -> FileArtifactDetail:
    try:
        artifact = get_artifact_service().get_owned(
            artifact_id=artifact_id,
            owner_user_id=_current_user_id(),
            workspace_id=_active_workspace_id(),
        )
        return get_artifact_service().to_detail(artifact)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc


@router.get("/{artifact_id}/content")
def download_file(artifact_id: str) -> StreamingResponse:
    try:
        artifact, stored = get_artifact_service().open_content(
            artifact_id=artifact_id,
            owner_user_id=_current_user_id(),
            workspace_id=_active_workspace_id(),
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    except (BotoCoreError, ClientError, OSError) as exc:
        raise _storage_unavailable(exc) from exc

    def chunks() -> Iterator[bytes]:
        started = perf_counter()
        transferred = 0
        outcome = "cancelled"
        try:
            while chunk := stored.body.read(_STREAM_CHUNK_BYTES):
                transferred += len(chunk)
                yield chunk
            outcome = "ok"
        except Exception:
            outcome = "error"
            raise
        finally:
            stored.body.close()
            get_file_telemetry().record(
                operation="download",
                outcome=outcome,
                duration_seconds=perf_counter() - started,
                bytes_count=transferred,
            )

    return StreamingResponse(
        chunks(),
        media_type=artifact.media_type,
        headers={
            "Content-Disposition": get_artifact_service().content_disposition(artifact.filename),
            "Content-Length": str(artifact.size_bytes),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(artifact_id: str) -> None:
    try:
        get_artifact_service().delete_staged(
            artifact_id=artifact_id,
            owner_user_id=_current_user_id(),
            workspace_id=_active_workspace_id(),
        )
    except ArtifactConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (BotoCoreError, ClientError, OSError) as exc:
        raise _storage_unavailable(exc) from exc
