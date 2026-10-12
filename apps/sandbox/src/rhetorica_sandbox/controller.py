from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any, cast
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from rhetorica_sandbox.contracts import ExecuteRequest, InspectRequest
from rhetorica_sandbox.kubernetes_jobs import (
    InClusterKubernetesApi,
    KubernetesApiError,
    KubernetesJobConfig,
    KubernetesJobSandboxController,
    RunnerResponseError,
    SandboxCapacityError,
)


class ControllerSettings(BaseSettings):
    internal_token: str = Field(validation_alias="SANDBOX_INTERNAL_TOKEN")
    namespace: str = Field(validation_alias="SANDBOX_K8S_NAMESPACE")
    instance: str = Field(default="rhetorica", validation_alias="SANDBOX_K8S_INSTANCE")
    runner_image: str = Field(validation_alias="SANDBOX_RUNNER_IMAGE")
    runner_image_pull_policy: str = Field(
        default="IfNotPresent", validation_alias="SANDBOX_RUNNER_IMAGE_PULL_POLICY"
    )
    runner_secret_name: str = Field(validation_alias="SANDBOX_RUNNER_SECRET_NAME")
    image_pull_secrets_json: str = Field(
        default="[]", validation_alias="SANDBOX_RUNNER_IMAGE_PULL_SECRETS_JSON"
    )
    max_sessions: int = Field(default=10, ge=1, le=20, validation_alias="SANDBOX_MAX_SESSIONS")
    startup_timeout_seconds: int = Field(
        default=60, ge=10, le=180, validation_alias="SANDBOX_RUNNER_STARTUP_TIMEOUT_SECONDS"
    )
    session_ttl_minutes: int = Field(
        default=20, ge=1, le=120, validation_alias="SANDBOX_SESSION_TTL_MINUTES"
    )
    cleanup_interval_seconds: int = Field(
        default=30, ge=5, le=300, validation_alias="SANDBOX_CLEANUP_INTERVAL_SECONDS"
    )
    process_timeout_seconds: int = Field(
        default=60, ge=1, le=300, validation_alias="SANDBOX_PROCESS_TIMEOUT_SECONDS"
    )
    max_input_bytes: int = Field(
        default=25 * 1024 * 1024, validation_alias="SANDBOX_MAX_INPUT_BYTES"
    )
    max_output_bytes: int = Field(
        default=50 * 1024 * 1024, validation_alias="SANDBOX_MAX_OUTPUT_BYTES"
    )
    max_session_output_bytes: int = Field(
        default=100 * 1024 * 1024,
        validation_alias="SANDBOX_MAX_SESSION_OUTPUT_BYTES",
    )
    max_workspace_bytes: int = Field(
        default=512 * 1024 * 1024, validation_alias="SANDBOX_MAX_WORKSPACE_BYTES"
    )
    max_tmp_bytes: int = Field(default=128 * 1024 * 1024, validation_alias="SANDBOX_MAX_TMP_BYTES")
    max_log_bytes: int = Field(default=64 * 1024, validation_alias="SANDBOX_MAX_LOG_BYTES")
    runner_cpu_request: str = Field(default="400m", validation_alias="SANDBOX_RUNNER_CPU_REQUEST")
    runner_cpu_limit: str = Field(default="425m", validation_alias="SANDBOX_RUNNER_CPU_LIMIT")
    runner_memory_request: str = Field(
        default="1Gi", validation_alias="SANDBOX_RUNNER_MEMORY_REQUEST"
    )
    runner_memory_limit: str = Field(
        default="1152Mi", validation_alias="SANDBOX_RUNNER_MEMORY_LIMIT"
    )
    runner_memory_limit_bytes: int = Field(
        default=768 * 1024 * 1024,
        validation_alias="SANDBOX_RUNNER_PROCESS_MEMORY_LIMIT_BYTES",
    )
    runner_cpu_ms_per_second: int = Field(
        default=400, validation_alias="SANDBOX_RUNNER_CPU_MS_PER_SECOND"
    )
    runner_workspace_size_limit: str = Field(
        default="768Mi", validation_alias="SANDBOX_RUNNER_WORKSPACE_SIZE_LIMIT"
    )
    runner_ephemeral_storage_request: str = Field(
        default="512Mi", validation_alias="SANDBOX_RUNNER_EPHEMERAL_STORAGE_REQUEST"
    )
    runner_ephemeral_storage_limit: str = Field(
        default="1Gi", validation_alias="SANDBOX_RUNNER_EPHEMERAL_STORAGE_LIMIT"
    )

    model_config = SettingsConfigDict(extra="ignore")

    def job_config(self) -> KubernetesJobConfig:
        return KubernetesJobConfig(
            namespace=self.namespace,
            instance=self.instance,
            runner_image=self.runner_image,
            runner_image_pull_policy=self.runner_image_pull_policy,
            runner_secret_name=self.runner_secret_name,
            image_pull_secrets_json=self.image_pull_secrets_json,
            max_sessions=self.max_sessions,
            startup_timeout_seconds=self.startup_timeout_seconds,
            session_ttl_minutes=self.session_ttl_minutes,
            process_timeout_seconds=self.process_timeout_seconds,
            max_input_bytes=self.max_input_bytes,
            max_output_bytes=self.max_output_bytes,
            max_session_output_bytes=self.max_session_output_bytes,
            max_workspace_bytes=self.max_workspace_bytes,
            max_tmp_bytes=self.max_tmp_bytes,
            max_log_bytes=self.max_log_bytes,
            cpu_request=self.runner_cpu_request,
            cpu_limit=self.runner_cpu_limit,
            memory_request=self.runner_memory_request,
            memory_limit=self.runner_memory_limit,
            memory_limit_bytes=self.runner_memory_limit_bytes,
            cpu_ms_per_second=self.runner_cpu_ms_per_second,
            workspace_size_limit=self.runner_workspace_size_limit,
            ephemeral_storage_request=self.runner_ephemeral_storage_request,
            ephemeral_storage_limit=self.runner_ephemeral_storage_limit,
        )


def _controller(request: Request) -> KubernetesJobSandboxController:
    return cast(KubernetesJobSandboxController, request.app.state.controller)


def _require_token(request: Request, token: str | None) -> None:
    if token != request.app.state.settings.internal_token:
        raise HTTPException(status_code=404, detail="Not found")


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SandboxCapacityError):
        return HTTPException(status_code=429, detail="capacity_exhausted")
    if isinstance(exc, RunnerResponseError):
        return HTTPException(status_code=exc.status_code, detail=exc.detail)
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="Session not found")
    if isinstance(exc, KubernetesApiError):
        return HTTPException(status_code=503, detail="runner_unavailable")
    if isinstance(exc, httpx.RequestError):
        return HTTPException(status_code=503, detail="runner_unavailable")
    return HTTPException(status_code=500, detail="controller_error")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = ControllerSettings()  # type: ignore[call-arg]
    controller = KubernetesJobSandboxController(
        api=InClusterKubernetesApi(namespace=settings.namespace),
        config=settings.job_config(),
        internal_token=settings.internal_token,
    )
    await asyncio.to_thread(controller.cleanup_orphan_jobs)
    app.state.settings = settings
    app.state.controller = controller

    async def cleanup_loop() -> None:
        while True:
            await asyncio.sleep(settings.cleanup_interval_seconds)
            await asyncio.to_thread(controller.cleanup_expired)

    cleanup_task = asyncio.create_task(cleanup_loop(), name="sandbox-job-cleanup")
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


def create_app() -> FastAPI:
    app = FastAPI(
        title="rhetorica-sandbox-controller",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/internal/v1/sessions")
    def create_session(
        request: Request,
        token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
    ) -> dict[str, str]:
        _require_token(request, token)
        try:
            return {"session_id": _controller(request).create_session()}
        except Exception as exc:
            raise _translate_error(exc) from exc

    @app.put("/internal/v1/sessions/{session_id}")
    def ensure_session(
        session_id: str,
        request: Request,
        token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
    ) -> dict[str, str]:
        _require_token(request, token)
        try:
            return {"session_id": _controller(request).create_session(session_id)}
        except Exception as exc:
            raise _translate_error(exc) from exc

    @app.post("/internal/v1/sessions/{session_id}/execute")
    def execute(
        session_id: str,
        payload: ExecuteRequest,
        request: Request,
        token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
    ) -> dict[str, Any]:
        _require_token(request, token)
        try:
            return _controller(request).execute(session_id, payload.model_dump())
        except Exception as exc:
            raise _translate_error(exc) from exc

    @app.post("/internal/v1/sessions/{session_id}/inspect")
    def inspect(
        session_id: str,
        payload: InspectRequest,
        request: Request,
        token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
    ) -> dict[str, Any]:
        _require_token(request, token)
        try:
            return _controller(request).inspect(session_id, payload.model_dump())
        except Exception as exc:
            raise _translate_error(exc) from exc

    @app.get("/internal/v1/sessions/{session_id}/outputs/{output_id}")
    def output(
        session_id: str,
        output_id: str,
        request: Request,
        token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
    ) -> StreamingResponse:
        _require_token(request, token)
        try:
            response, stream = _controller(request).open_output(
                session_id=session_id,
                output_id=output_id,
            )
        except Exception as exc:
            raise _translate_error(exc) from exc

        def body() -> Iterator[bytes]:
            try:
                yield from response.iter_bytes(chunk_size=1024 * 1024)
            finally:
                stream.__exit__(None, None, None)

        headers = {
            "X-Artifact-Filename": quote(
                response.headers.get("X-Artifact-Filename", "output.bin"), safe="%"
            ),
            "X-Content-Type-Options": "nosniff",
        }
        return StreamingResponse(
            body(),
            media_type=response.headers.get("Content-Type", "application/octet-stream"),
            headers=headers,
        )

    @app.delete("/internal/v1/sessions/{session_id}", status_code=204)
    def delete_session(
        session_id: str,
        request: Request,
        token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
    ) -> None:
        _require_token(request, token)
        try:
            _controller(request).close_session(session_id)
        except Exception as exc:
            raise _translate_error(exc) from exc

    return app


app = create_app()
