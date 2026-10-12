from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from rhetorica_backend.infra.sandbox.local_supervisor import (
    LocalSandboxConfig,
    LocalSandboxSupervisor,
    SandboxRejectedError,
)

from rhetorica_sandbox.contracts import ExecuteRequest, InspectRequest
from rhetorica_sandbox.settings import settings

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


supervisor = LocalSandboxSupervisor(
    LocalSandboxConfig(
        sessions_root=settings.sessions_root,
        nsjail_path=settings.nsjail_path,
        python_path=settings.python_path,
        max_parallel_runs=settings.max_parallel_runs,
        process_timeout_seconds=settings.process_timeout_seconds,
        session_ttl_minutes=settings.session_ttl_minutes,
        max_input_bytes=settings.max_input_bytes,
        max_output_bytes=settings.max_output_bytes,
        max_session_output_bytes=settings.max_session_output_bytes,
        max_workspace_bytes=settings.max_workspace_bytes,
        max_tmp_bytes=settings.max_tmp_bytes,
        max_log_bytes=settings.max_log_bytes,
        max_output_files=settings.max_output_files,
        max_session_output_files=settings.max_session_output_files,
        memory_limit_bytes=settings.memory_limit_bytes,
        pids_limit=settings.pids_limit,
        cpu_ms_per_second=settings.cpu_ms_per_second,
        manage_legacy_cgroup_parents=settings.manage_legacy_cgroup_parents,
        cgroup_parent=settings.cgroup_parent,
    )
)
router = APIRouter(prefix="/internal/v1")


def require_internal_token(
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> None:
    if token != settings.internal_token:
        raise HTTPException(status_code=404, detail="Not found")


@router.post("/sessions", dependencies=[])
def create_session(
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> dict[str, str]:
    require_internal_token(token)
    return {"session_id": supervisor.create_session()}


@router.put("/sessions/{session_id}")
def ensure_session(
    session_id: str,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> dict[str, str]:
    require_internal_token(token)
    try:
        return {"session_id": supervisor.create_session(session_id)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid session ID") from exc


@router.post("/sessions/{session_id}/execute")
def execute(
    session_id: str,
    payload: ExecuteRequest,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> dict[str, Any]:
    require_internal_token(token)
    try:
        return dict(
            supervisor.execute(
                session_id=session_id,
                code=payload.code,
                inputs=[item.model_dump() for item in payload.inputs],
                operation_id=payload.operation_id,
            )
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except SandboxRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/inspect")
def inspect(
    session_id: str,
    payload: InspectRequest,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> dict[str, Any]:
    require_internal_token(token)
    try:
        return dict(
            supervisor.inspect(session_id=session_id, input_spec=payload.input.model_dump())
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except SandboxRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/outputs/{output_id}")
def output(
    session_id: str,
    output_id: str,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> FileResponse:
    require_internal_token(token)
    try:
        path, media_type = supervisor.output(session_id=session_id, output_id=output_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Output not found") from exc
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        headers={
            "X-Artifact-Filename": quote(path.name, safe=""),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: str, token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None
) -> None:
    require_internal_token(token)
    supervisor.delete_session(session_id)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    async def cleanup_loop() -> None:
        while True:
            await asyncio.sleep(settings.cleanup_interval_seconds)
            await asyncio.to_thread(supervisor.cleanup_expired)

    cleanup_task = asyncio.create_task(cleanup_loop(), name="sandbox-session-cleanup")
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


def create_app() -> FastAPI:
    app = FastAPI(
        title="rhetorica-sandbox",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    return app


app = create_app()
