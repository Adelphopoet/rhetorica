from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from rhetorica_backend.contracts.coding import PreparePythonEnvironmentRequest
from rhetorica_backend.infra.execution import ProcessExecutor, ProcessLimits
from rhetorica_backend.infra.execution.file_sessions import (
    ExecutionRejectedError,
    FileSessionConfig,
    FileSessionRuntime,
)

from rhetorica_coding_runner.contracts import ExecuteRequest, FileExecuteRequest, FileInspectRequest
from rhetorica_coding_runner.runtime import ExecutionRuntime, ExecutionRuntimeError
from rhetorica_coding_runner.sessions import CodingSessionRegistry
from rhetorica_coding_runner.settings import CodingRunnerSettings

settings = CodingRunnerSettings()  # type: ignore[call-arg]
runtime = ExecutionRuntime(settings)
file_sessions = FileSessionRuntime(
    FileSessionConfig(
        sessions_root=settings.sessions_root,
        process_timeout_seconds=settings.process_timeout_seconds,
        session_ttl_minutes=settings.session_ttl_minutes,
        max_input_bytes=settings.max_input_bytes,
        max_output_bytes=settings.max_output_bytes,
        max_session_output_bytes=settings.max_session_output_bytes,
        max_workspace_bytes=settings.max_workspace_bytes,
        max_output_files=settings.max_output_files,
        max_session_output_files=settings.max_session_output_files,
    ),
    ProcessExecutor(
        ProcessLimits(
            max_log_bytes=settings.max_log_bytes,
            pid_limit=settings.pid_limit,
            memory_limit_bytes=settings.process_memory_limit_bytes,
            file_size_limit_bytes=settings.max_output_bytes,
            cpu_time_seconds=settings.process_timeout_seconds,
        )
    ),
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async def cleanup_loop() -> None:
        while True:
            await asyncio.sleep(settings.cleanup_interval_seconds)
            await asyncio.to_thread(file_sessions.cleanup_expired)

    task = asyncio.create_task(cleanup_loop(), name="execution-runtime-session-cleanup")
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="rhetorica-execution-runtime",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


def require_token(token: str | None) -> None:
    if token != settings.task_token:
        raise HTTPException(status_code=404, detail="Not found")


@app.get("/healthz")
def health() -> dict[str, object]:
    payload = settings.software_manifest_path.read_bytes()
    return {
        "status": "ok",
        "software_manifest_digest": hashlib.sha256(payload).hexdigest(),
        "tool_versions": json.loads(payload),
    }


@app.put("/internal/v1/snapshot")
async def stage_snapshot(
    request: Request,
    digest: Annotated[str, Header(alias="X-Rhetorica-Snapshot-Sha256")],
    token: Annotated[str | None, Header(alias="X-Rhetorica-Task-Token")] = None,
) -> dict[str, Any]:
    require_token(token)
    payload = await request.body()
    if len(payload) > settings.max_snapshot_bytes:
        raise HTTPException(status_code=413, detail="snapshot_too_large")
    try:
        return runtime.stage(payload, expected_digest=digest)
    except (ExecutionRuntimeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/internal/v1/execute")
def execute(
    payload: ExecuteRequest,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Task-Token")] = None,
) -> dict[str, Any]:
    require_token(token)
    try:
        return runtime.execute(payload)
    except (ExecutionRuntimeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/internal/v1/candidate")
def collect_candidate(
    token: Annotated[str | None, Header(alias="X-Rhetorica-Task-Token")] = None,
) -> Response:
    require_token(token)
    try:
        payload, manifest = runtime.collect()
    except (ExecutionRuntimeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        payload,
        media_type="application/gzip",
        headers={
            "X-Rhetorica-Artifact-Sha256": str(manifest["digest"]),
            "X-Rhetorica-Artifact-Manifest": json_dumps(manifest),
        },
    )


@app.post("/internal/v1/cancel", status_code=204)
def cancel(
    token: Annotated[str | None, Header(alias="X-Rhetorica-Task-Token")] = None,
) -> None:
    require_token(token)
    runtime.cancel()


def json_dumps(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


@app.post("/internal/v1/files/sessions")
def create_file_session(
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> dict[str, str]:
    require_token(token)
    return {"session_id": file_sessions.create_session()}


@app.put("/internal/v1/files/sessions/{session_id}")
def ensure_file_session(
    session_id: str,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> dict[str, str]:
    require_token(token)
    try:
        return {"session_id": file_sessions.create_session(session_id)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid session ID") from exc


@app.post("/internal/v1/files/sessions/{session_id}/execute")
def execute_file_python(
    session_id: str,
    payload: FileExecuteRequest,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> dict[str, Any]:
    require_token(token)
    try:
        return file_sessions.execute(
            session_id=session_id,
            code=payload.code,
            inputs=[item.model_dump() for item in payload.inputs],
            operation_id=payload.operation_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except ExecutionRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/internal/v1/files/sessions/{session_id}/inspect")
def inspect_file(
    session_id: str,
    payload: FileInspectRequest,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> dict[str, Any]:
    require_token(token)
    try:
        return file_sessions.inspect(session_id=session_id, input_spec=payload.input.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except ExecutionRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/internal/v1/files/sessions/{session_id}/outputs/{output_id}")
def download_file_output(
    session_id: str,
    output_id: str,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> FileResponse:
    require_token(token)
    try:
        path, media_type = file_sessions.output(session_id=session_id, output_id=output_id)
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


@app.delete("/internal/v1/files/sessions/{session_id}", status_code=204)
def delete_file_session(
    session_id: str,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> None:
    require_token(token)
    file_sessions.delete_session(session_id)


# Session endpoints are used only by the explicitly selected local Docker adapter.

coding_sessions = CodingSessionRegistry(settings)


@app.put("/internal/v1/coding/sessions/{session_id}/snapshot")
async def coding_snapshot(
    session_id: str,
    request: Request,
    digest: Annotated[str, Header(alias="X-Rhetorica-Snapshot-Sha256")],
    token: Annotated[str | None, Header(alias="X-Rhetorica-Task-Token")] = None,
):
    require_token(token)
    try:
        session = coding_sessions.get(session_id)
        if coding_sessions.staged(session_id):
            return {"restored": True}
        payload = await request.body()
        if len(payload) > settings.max_snapshot_bytes:
            raise HTTPException(413, "snapshot_too_large")
        return await asyncio.to_thread(session.stage, payload, expected_digest=digest)
    except (ValueError, ExecutionRuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/internal/v1/coding/sessions/{session_id}/execute")
def coding_execute(
    session_id: str,
    payload: ExecuteRequest,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Task-Token")] = None,
):
    require_token(token)
    try:
        return coding_sessions.get(session_id).execute(payload)
    except (ValueError, ExecutionRuntimeError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/internal/v1/coding/sessions/{session_id}/candidate")
def coding_candidate(
    session_id: str, token: Annotated[str | None, Header(alias="X-Rhetorica-Task-Token")] = None
):
    require_token(token)
    try:
        payload, manifest = coding_sessions.get(session_id).collect()
    except (ExecutionRuntimeError, ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(
        payload,
        media_type="application/gzip",
        headers={
            "X-Rhetorica-Artifact-Sha256": manifest["digest"],
        },
    )


@app.delete("/internal/v1/coding/sessions/{session_id}", status_code=204)
def coding_close(
    session_id: str, token: Annotated[str | None, Header(alias="X-Rhetorica-Task-Token")] = None
):
    require_token(token)
    try:
        coding_sessions.close(session_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.put("/internal/v1/coding/sessions/{session_id}/environment")
def prepare_coding_environment(
    session_id: str,
    payload: PreparePythonEnvironmentRequest,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Task-Token")] = None,
):
    require_token(token)
    try:
        if not coding_sessions.staged(session_id):
            raise HTTPException(422, "coding_snapshot_required")
        return coding_sessions.get(session_id).python_environment.prepare(payload)
    except (ValueError, ExecutionRuntimeError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
