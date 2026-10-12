from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any, cast

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from rhetorica_backend.contracts.coding import (
    CodingRun,
    EnvironmentProfile,
    ExecutionPrincipal,
    SandboxExecutionRequest,
    SandboxPhase,
)
from rhetorica_backend.infra.coding.artifact_relay import SignedS3CodingArtifactRelay
from rhetorica_backend.infra.coding.kubernetes_runner import KubernetesCodingSandbox
from rhetorica_backend.infra.coding.repository_snapshot import TrustedRepositorySnapshotService
from rhetorica_backend.infra.execution.kubernetes import (
    ExecutionCapacityError,
    InClusterExecutionApi,
    KubernetesExecutionError,
    KubernetesJobCapacity,
)
from rhetorica_backend.infra.execution.kubernetes_file_sessions import (
    KubernetesFileSessionConfig,
    KubernetesFileSessionController,
    RunnerResponseError,
)
from rhetorica_backend.infra.object_storage import S3ObjectStorage, S3StorageConfig
from rhetorica_backend.infra.postgres.coding_sandbox_repository import (
    CodingSandboxLeaseRepository,
)
from rhetorica_backend.infra.postgres.gitlab_connector_repository import (
    GitLabConnectorRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from starlette.background import BackgroundTask

from rhetorica_coding_runner.contracts import FileExecuteRequest, FileInspectRequest


class ControllerSettings(BaseSettings):
    namespace: str = Field(
        validation_alias=AliasChoices(
            "EXECUTION_CONTROLLER_NAMESPACE", "CODING_CONTROLLER_NAMESPACE"
        )
    )
    instance: str = Field(
        default="rhetorica",
        validation_alias=AliasChoices(
            "EXECUTION_CONTROLLER_INSTANCE", "CODING_CONTROLLER_INSTANCE"
        ),
    )
    max_active_jobs: int = Field(
        default=10,
        ge=1,
        le=1000,
        validation_alias="EXECUTION_CONTROLLER_MAX_ACTIVE_JOBS",
    )
    reconcile_interval_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
        validation_alias=AliasChoices(
            "EXECUTION_CONTROLLER_RECONCILE_INTERVAL_SECONDS",
            "CODING_CONTROLLER_RECONCILE_INTERVAL_SECONDS",
        ),
    )
    execution_runtime_internal_token: str = Field(
        validation_alias="EXECUTION_RUNTIME_INTERNAL_TOKEN"
    )
    file_enabled: bool = Field(
        default=False,
        validation_alias="EXECUTION_RUNTIME_ENABLED",
    )
    file_runner_image: str = Field(validation_alias="EXECUTION_CONTROLLER_FILE_RUNNER_IMAGE")
    file_runner_secret_name: str = Field(
        validation_alias="EXECUTION_CONTROLLER_FILE_RUNNER_SECRET_NAME"
    )
    file_runner_image_pull_policy: str = Field(
        default="IfNotPresent",
        validation_alias="EXECUTION_CONTROLLER_FILE_RUNNER_IMAGE_PULL_POLICY",
    )
    file_runner_runtime_class_name: str = Field(
        validation_alias="EXECUTION_CONTROLLER_FILE_RUNTIME_CLASS_NAME"
    )
    file_node_selector_json: str = Field(
        default='{"rhetorica.io/coding-runtime":"gvisor"}',
        validation_alias="EXECUTION_CONTROLLER_FILE_NODE_SELECTOR_JSON",
    )
    file_tolerations_json: str = Field(
        default="[]",
        validation_alias="EXECUTION_CONTROLLER_FILE_TOLERATIONS_JSON",
    )
    coding_node_selector_json: str = Field(
        default='{"rhetorica.io/coding-runtime":"gvisor"}',
        validation_alias="CODING_CONTROLLER_NODE_SELECTOR_JSON",
    )
    coding_tolerations_json: str = Field(
        default="[]",
        validation_alias="CODING_CONTROLLER_TOLERATIONS_JSON",
    )
    image_pull_secrets_json: str = Field(
        default="[]",
        validation_alias=AliasChoices(
            "EXECUTION_CONTROLLER_IMAGE_PULL_SECRETS_JSON",
            "CODING_CONTROLLER_IMAGE_PULL_SECRETS_JSON",
        ),
    )
    file_startup_timeout_seconds: int = Field(
        default=60,
        ge=5,
        le=300,
        validation_alias="EXECUTION_CONTROLLER_FILE_STARTUP_TIMEOUT_SECONDS",
    )
    file_session_ttl_minutes: int = Field(
        default=20,
        ge=1,
        le=120,
        validation_alias="EXECUTION_RUNTIME_SESSION_TTL_MINUTES",
    )
    file_cleanup_interval_seconds: int = Field(
        default=60,
        ge=5,
        le=300,
        validation_alias="EXECUTION_RUNTIME_CLEANUP_INTERVAL_SECONDS",
    )
    file_process_timeout_seconds: int = Field(
        default=60,
        ge=1,
        le=300,
        validation_alias="EXECUTION_RUNTIME_PROCESS_TIMEOUT_SECONDS",
    )
    file_max_input_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=1,
        validation_alias="FILE_MAX_INPUT_BYTES",
    )
    file_max_output_bytes: int = Field(
        default=50 * 1024 * 1024,
        ge=1,
        validation_alias="FILE_MAX_OUTPUT_BYTES",
    )
    file_max_session_output_bytes: int = Field(
        default=100 * 1024 * 1024,
        ge=1,
        validation_alias="FILE_MAX_SESSION_OUTPUT_BYTES",
    )
    file_max_workspace_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1,
        validation_alias="EXECUTION_RUNTIME_MAX_WORKSPACE_BYTES",
    )
    file_max_log_bytes: int = Field(
        default=64 * 1024,
        ge=1024,
        validation_alias="EXECUTION_RUNTIME_MAX_LOG_BYTES",
    )
    file_max_output_files: int = Field(
        default=100,
        ge=1,
        validation_alias="EXECUTION_RUNTIME_MAX_OUTPUT_FILES",
    )
    file_max_session_output_files: int = Field(
        default=200,
        ge=1,
        validation_alias="EXECUTION_RUNTIME_MAX_SESSION_OUTPUT_FILES",
    )
    file_process_memory_limit_bytes: int = Field(
        default=768 * 1024 * 1024,
        ge=128 * 1024 * 1024,
        validation_alias="EXECUTION_RUNTIME_MEMORY_LIMIT_BYTES",
    )
    file_pid_limit: int = Field(
        default=64,
        ge=16,
        validation_alias="EXECUTION_RUNTIME_PID_LIMIT",
    )
    file_workspace_size_limit: str = Field(
        default="1Gi",
        validation_alias="EXECUTION_CONTROLLER_FILE_WORKSPACE_SIZE_LIMIT",
    )
    file_cpu_request: str = Field(
        default="400m",
        validation_alias="EXECUTION_CONTROLLER_FILE_CPU_REQUEST",
    )
    file_cpu_limit: str = Field(
        default="1",
        validation_alias="EXECUTION_CONTROLLER_FILE_CPU_LIMIT",
    )
    file_memory_request: str = Field(
        default="1Gi",
        validation_alias="EXECUTION_CONTROLLER_FILE_MEMORY_REQUEST",
    )
    file_memory_limit: str = Field(
        default="2304Mi",
        validation_alias="EXECUTION_CONTROLLER_FILE_MEMORY_LIMIT",
    )
    file_ephemeral_storage_request: str = Field(
        default="512Mi",
        validation_alias="EXECUTION_CONTROLLER_FILE_EPHEMERAL_STORAGE_REQUEST",
    )
    file_ephemeral_storage_limit: str = Field(
        default="2Gi",
        validation_alias="EXECUTION_CONTROLLER_FILE_EPHEMERAL_STORAGE_LIMIT",
    )
    coding_enabled: bool = Field(default=False, validation_alias="CODING_ENABLED")
    coding_internal_token: str | None = Field(
        default=None,
        validation_alias="CODING_CONTROLLER_INTERNAL_TOKEN",
    )
    coding_controller_secret: str | None = Field(
        default=None,
        validation_alias="CODING_CONTROLLER_CONTROLLER_SECRET",
    )
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    coding_runner_image_digest: str | None = Field(
        default=None,
        validation_alias="CODING_CONTROLLER_RUNNER_IMAGE_DIGEST",
    )
    coding_runner_runtime_class_name: str | None = Field(
        default=None,
        validation_alias="CODING_CONTROLLER_RUNNER_RUNTIME_CLASS_NAME",
    )
    s3_endpoint: str = Field(default="http://minio:9000", validation_alias="S3_ENDPOINT")
    s3_access_key: str = Field(default="minio", validation_alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(default="minio123", validation_alias="S3_SECRET_KEY")
    s3_bucket: str = Field(default="rhetorica", validation_alias="S3_BUCKET")
    s3_region: str = Field(default="us-east-1", validation_alias="S3_REGION")
    s3_secure: bool = Field(default=False, validation_alias="S3_SECURE")

    @model_validator(mode="after")
    def validate_coding_settings(self) -> ControllerSettings:
        required = (
            self.coding_internal_token,
            self.coding_controller_secret,
            self.database_url,
            self.coding_runner_image_digest,
            self.coding_runner_runtime_class_name,
        )
        if self.coding_enabled and not all(required):
            raise ValueError("coding controller settings are incomplete")
        return self

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    task_id: str
    profile: EnvironmentProfile
    principal: ExecutionPrincipal
    phase: SandboxPhase = SandboxPhase.RUNTIME


class ScopedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal: ExecutionPrincipal


class ExecuteControllerRequest(ScopedRequest):
    request: SandboxExecutionRequest


class StageControllerRequest(ScopedRequest):
    artifact_id: str


class PrepareRepositorySnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: CodingRun
    principal: ExecutionPrincipal


def _require_coding_token(request: Request, token: str | None) -> None:
    expected = request.app.state.settings.coding_internal_token
    if expected is None or token != expected:
        raise HTTPException(status_code=404, detail="Not found")


def _require_file_token(request: Request, token: str | None) -> None:
    if token != request.app.state.settings.execution_runtime_internal_token:
        raise HTTPException(status_code=404, detail="Not found")


def _sandbox(request: Request) -> KubernetesCodingSandbox:
    sandbox = request.app.state.sandbox
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Not found")
    return cast(KubernetesCodingSandbox, sandbox)


def _file_sessions(request: Request) -> KubernetesFileSessionController:
    file_sessions = request.app.state.file_sessions
    if file_sessions is None:
        raise HTTPException(status_code=404, detail="Not found")
    return cast(KubernetesFileSessionController, file_sessions)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = ControllerSettings()  # type: ignore[call-arg]
    api = InClusterExecutionApi(namespace=settings.namespace)
    capacity = KubernetesJobCapacity(
        api=api,
        instance=settings.instance,
        max_active_jobs=settings.max_active_jobs,
    )
    file_sessions: KubernetesFileSessionController | None = None
    if settings.file_enabled:
        file_sessions = KubernetesFileSessionController(
            api=api,
            capacity=capacity,
            internal_token=settings.execution_runtime_internal_token,
            config=KubernetesFileSessionConfig(
                instance=settings.instance,
                runner_image=settings.file_runner_image,
                runner_image_pull_policy=settings.file_runner_image_pull_policy,
                runner_secret_name=settings.file_runner_secret_name,
                runtime_class_name=settings.file_runner_runtime_class_name,
                image_pull_secrets_json=settings.image_pull_secrets_json,
                node_selector_json=settings.file_node_selector_json,
                tolerations_json=settings.file_tolerations_json,
                startup_timeout_seconds=settings.file_startup_timeout_seconds,
                session_ttl_minutes=settings.file_session_ttl_minutes,
                cleanup_interval_seconds=settings.file_cleanup_interval_seconds,
                process_timeout_seconds=settings.file_process_timeout_seconds,
                max_input_bytes=settings.file_max_input_bytes,
                max_output_bytes=settings.file_max_output_bytes,
                max_session_output_bytes=settings.file_max_session_output_bytes,
                max_workspace_bytes=settings.file_max_workspace_bytes,
                max_log_bytes=settings.file_max_log_bytes,
                max_output_files=settings.file_max_output_files,
                max_session_output_files=settings.file_max_session_output_files,
                process_memory_limit_bytes=settings.file_process_memory_limit_bytes,
                pid_limit=settings.file_pid_limit,
                workspace_size_limit=settings.file_workspace_size_limit,
                cpu_request=settings.file_cpu_request,
                cpu_limit=settings.file_cpu_limit,
                memory_request=settings.file_memory_request,
                memory_limit=settings.file_memory_limit,
                ephemeral_storage_request=settings.file_ephemeral_storage_request,
                ephemeral_storage_limit=settings.file_ephemeral_storage_limit,
            ),
        )
    sandbox: KubernetesCodingSandbox | None = None
    if settings.coding_enabled:
        db = DatabaseRuntime(cast(str, settings.database_url))
        storage = S3ObjectStorage(
            S3StorageConfig(
                endpoint_url=settings.s3_endpoint,
                access_key=settings.s3_access_key,
                secret_key=settings.s3_secret_key,
                bucket=settings.s3_bucket,
                region=settings.s3_region,
                secure=settings.s3_secure,
            )
        )
        artifacts = SignedS3CodingArtifactRelay(
            storage=storage,
            signing_secret=cast(str, settings.coding_controller_secret),
        )
        sandbox = KubernetesCodingSandbox(
            api=api,
            capacity=capacity,
            leases=CodingSandboxLeaseRepository(db),
            artifacts=artifacts,
            instance=settings.instance,
            controller_secret=cast(str, settings.coding_controller_secret),
            node_selector=json.loads(settings.coding_node_selector_json),
            tolerations=json.loads(settings.coding_tolerations_json),
            image_pull_secrets=json.loads(settings.image_pull_secrets_json),
        )
        app.state.artifacts = artifacts
        app.state.gitlab_connectors = GitLabConnectorRepository(db)
        app.state.snapshotter = TrustedRepositorySnapshotService()
        await asyncio.to_thread(sandbox.reconcile)
    app.state.settings = settings
    app.state.sandbox = sandbox
    app.state.file_sessions = file_sessions
    if file_sessions is not None:
        await asyncio.to_thread(file_sessions.reconcile)

    async def reconcile_loop() -> None:
        while True:
            await asyncio.sleep(settings.reconcile_interval_seconds)
            if file_sessions is not None:
                await asyncio.to_thread(file_sessions.reconcile)
            if sandbox is not None:
                await asyncio.to_thread(sandbox.reconcile)

    task = asyncio.create_task(reconcile_loop(), name="execution-job-reconcile")
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="rhetorica-execution-controller",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/internal/v1/files/sessions")
def create_file_session(
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> dict[str, str]:
    _require_file_token(request, token)
    try:
        return {"session_id": _file_sessions(request).create_session()}
    except ExecutionCapacityError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except (KubernetesExecutionError, httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="execution_runtime_unavailable") from exc


@app.put("/internal/v1/files/sessions/{session_id}")
def ensure_file_session(
    session_id: str,
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> dict[str, str]:
    _require_file_token(request, token)
    try:
        return {"session_id": _file_sessions(request).create_session(session_id)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid session ID") from exc
    except ExecutionCapacityError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except (KubernetesExecutionError, httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="execution_runtime_unavailable") from exc


@app.post("/internal/v1/files/sessions/{session_id}/execute")
def execute_file_python(
    session_id: str,
    payload: FileExecuteRequest,
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> dict[str, Any]:
    _require_file_token(request, token)
    try:
        return _file_sessions(request).execute(session_id, payload.model_dump(mode="json"))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except RunnerResponseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.post("/internal/v1/files/sessions/{session_id}/inspect")
def inspect_file(
    session_id: str,
    payload: FileInspectRequest,
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> dict[str, Any]:
    _require_file_token(request, token)
    try:
        return _file_sessions(request).inspect(session_id, payload.model_dump(mode="json"))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except RunnerResponseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.get("/internal/v1/files/sessions/{session_id}/outputs/{output_id}")
def download_file_output(
    session_id: str,
    output_id: str,
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> StreamingResponse:
    _require_file_token(request, token)
    try:
        response, close_stream = _file_sessions(request).open_output(
            session_id=session_id,
            output_id=output_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Output not found") from exc
    except RunnerResponseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return StreamingResponse(
        response.iter_bytes(chunk_size=1024 * 1024),
        media_type=response.headers.get("Content-Type", "application/octet-stream"),
        headers={
            "X-Artifact-Filename": response.headers.get("X-Artifact-Filename", "output.bin"),
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(close_stream),
    )


@app.delete("/internal/v1/files/sessions/{session_id}", status_code=204)
def delete_file_session(
    session_id: str,
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Sandbox-Token")] = None,
) -> None:
    _require_file_token(request, token)
    _file_sessions(request).close_session(session_id)


@app.post("/internal/v1/sessions")
def create_session(
    payload: CreateSessionRequest,
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Coding-Token")] = None,
) -> dict[str, Any]:
    _require_coding_token(request, token)
    settings = cast(ControllerSettings, request.app.state.settings)
    if (
        payload.profile.image_digest != settings.coding_runner_image_digest
        or payload.profile.runtime_class_name != settings.coding_runner_runtime_class_name
    ):
        raise HTTPException(status_code=422, detail="coding_environment_profile_not_deployed")
    try:
        lease = _sandbox(request).create(
            run_id=payload.run_id,
            task_id=payload.task_id,
            profile=payload.profile,
            principal=payload.principal,
            phase=payload.phase,
        )
        return lease.model_dump(mode="json")
    except ExecutionCapacityError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/internal/v1/repository-snapshots")
def prepare_repository_snapshot(
    payload: PrepareRepositorySnapshotRequest,
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Coding-Token")] = None,
) -> dict[str, Any]:
    _require_coding_token(request, token)
    connection = request.app.state.gitlab_connectors.get_connection_by_id(
        workspace_id=payload.principal.workspace_id,
        connector_id=payload.run.repository.gitlab_connector_id,
    )
    if connection is None:
        raise HTTPException(status_code=403, detail="gitlab_connector_not_available")
    prepared = request.app.state.snapshotter.prepare(
        connection=connection,
        run=payload.run,
        principal=payload.principal,
    )
    artifact_id = request.app.state.artifacts.persist(
        payload=prepared.payload,
        digest=prepared.digest,
        workspace_id=prepared.workspace_id,
        owner_user_id=prepared.owner_user_id,
        run_id=prepared.run_id,
        task_id=payload.principal.task_id or "repository",
        kind="snapshot",
    )
    return {
        "artifact_id": artifact_id,
        "digest": prepared.digest,
        "base_sha": prepared.base_sha,
        "path_count": len(prepared.paths),
    }


@app.put("/internal/v1/sessions/{lease_id}/snapshot")
def stage_snapshot(
    lease_id: str,
    payload: StageControllerRequest,
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Coding-Token")] = None,
) -> dict[str, str]:
    _require_coding_token(request, token)
    _sandbox(request).stage_snapshot(
        lease_id=lease_id,
        snapshot_id=payload.artifact_id,
        principal=payload.principal,
    )
    return {"artifact_id": payload.artifact_id}


@app.post("/internal/v1/sessions/{lease_id}/execute")
def execute(
    lease_id: str,
    payload: ExecuteControllerRequest,
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Coding-Token")] = None,
) -> dict[str, Any]:
    _require_coding_token(request, token)
    return (
        _sandbox(request)
        .execute(
            lease_id=lease_id,
            request=payload.request,
            principal=payload.principal,
        )
        .model_dump(mode="json")
    )


@app.post("/internal/v1/sessions/{lease_id}/collect")
def collect(
    lease_id: str,
    payload: ScopedRequest,
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Coding-Token")] = None,
) -> dict[str, Any]:
    _require_coding_token(request, token)
    return (
        _sandbox(request)
        .collect(lease_id=lease_id, principal=payload.principal)
        .model_dump(mode="json")
    )


@app.post("/internal/v1/sessions/{lease_id}/checkpoint")
def checkpoint(
    lease_id: str,
    payload: ScopedRequest,
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Coding-Token")] = None,
) -> dict[str, str]:
    _require_coding_token(request, token)
    return {
        "checkpoint_ref": _sandbox(request).checkpoint(
            lease_id=lease_id,
            principal=payload.principal,
        )
    }


@app.post("/internal/v1/sessions/{lease_id}/cancel", status_code=204)
def cancel(
    lease_id: str,
    payload: ScopedRequest,
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Coding-Token")] = None,
) -> None:
    _require_coding_token(request, token)
    _sandbox(request).cancel(lease_id=lease_id, principal=payload.principal)


@app.delete("/internal/v1/sessions/{lease_id}", status_code=204)
def close(
    lease_id: str,
    payload: ScopedRequest,
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Coding-Token")] = None,
) -> None:
    _require_coding_token(request, token)
    _sandbox(request).close(lease_id=lease_id, principal=payload.principal)
