from __future__ import annotations

from contextlib import suppress
from functools import lru_cache
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from rhetorica_backend.contracts.execution import (
    CancelExecutionResponse,
    EngineConnectionConfig,
    ExecuteSqlRequest,
    ExecuteSqlResponse,
    ExecutionDetailResponse,
    ExecutionHistoryItem,
    ExternalSqlExecutionRequest,
)
from rhetorica_backend.domain.execution import build_execution_service
from rhetorica_backend.domain.execution.policy import sql_guard_policy_for_connection
from rhetorica_backend.domain.execution.service import ExecutionService
from rhetorica_backend.infra.celery_runtime import build_celery_app
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime

from rhetorica_api.app.identity import resolve_current_user
from rhetorica_api.app.settings import settings

router = APIRouter(prefix="/v1/sql", tags=["execution"])


@lru_cache(maxsize=1)
def _runtime() -> tuple[ExecutionService, DatabaseRuntime]:
    return build_execution_service(settings.database_url)


def _services() -> tuple[ExecutionService, DatabaseRuntime]:
    return _runtime()


def _resolve_connector(
    runtime: DatabaseRuntime,
    workspace_id: str,
    connector_name: str | None,
) -> tuple[EngineConnectionConfig, str]:
    """Resolve a connector for execution.

    Args:
        runtime: Database runtime for connector repository
        workspace_id: Workspace identifier
        connector_name: Optional specific connector name; uses first available if None

    Returns:
        Tuple of (connection_config, resolved_connector_name)

    Raises:
        ValueError: If no connector found for the workspace
    """
    conn_repo = ConnectorRepository(db=runtime)

    if connector_name:
        config = conn_repo.get_connection_for_execution(
            workspace_id=workspace_id,
            name=connector_name,
        )
        if config is None:
            raise ValueError(
                f"Connector '{connector_name}' not found for workspace '{workspace_id}'."
            )
        return config, connector_name

    # Use first available connector
    connectors = conn_repo.list(workspace_id=workspace_id, limit=1)
    if not connectors:
        raise ValueError(
            f"No database connector configured for workspace '{workspace_id}'."
        )
    first_name = connectors[0].name
    config = conn_repo.get_connection_for_execution(
        workspace_id=workspace_id,
        name=first_name,
    )
    if config is None:
        raise ValueError(
            f"Connector '{first_name}' not found for workspace '{workspace_id}'."
        )
    return config, first_name


@lru_cache(maxsize=1)
def _celery_app():
    return build_celery_app(
        broker_url=settings.celery_broker_url,
        result_backend=settings.celery_result_backend,
    )


def _result_payload(execution_id: str) -> ExecuteSqlResponse | None:
    task_result = _celery_app().AsyncResult(execution_id)
    if not task_result.ready() or not isinstance(task_result.result, dict):
        return None
    return ExecuteSqlResponse.model_validate(task_result.result)


@router.post("/execute", response_model=ExecuteSqlResponse)
def execute_sql(payload: ExternalSqlExecutionRequest) -> ExecuteSqlResponse:
    service, runtime = _services()

    try:
        connection, _connector_name = _resolve_connector(
            runtime=runtime,
            workspace_id=payload.workspace_id,
            connector_name=payload.connector_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    internal_request = ExecuteSqlRequest(
        sql=payload.sql,
        connection=connection,
        dialect=payload.dialect,
        policy=sql_guard_policy_for_connection(connection, payload.policy),
        workspace_id=payload.workspace_id,
        actor_id=payload.actor_id,
    )

    return service.execute(internal_request)


@router.post("/executions", response_model=ExecuteSqlResponse)
def submit_sql_execution(payload: ExternalSqlExecutionRequest) -> ExecuteSqlResponse:
    service, runtime = _services()

    try:
        connection, _connector_name = _resolve_connector(
            runtime=runtime,
            workspace_id=payload.workspace_id,
            connector_name=payload.connector_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    internal_request = ExecuteSqlRequest(
        sql=payload.sql,
        connection=connection,
        dialect=payload.dialect,
        policy=sql_guard_policy_for_connection(connection, payload.policy),
        workspace_id=payload.workspace_id,
        actor_id=payload.actor_id,
    )

    execution_id = str(uuid4())
    queued = service.create_queued_execution(
        execution_id=execution_id,
        request=internal_request,
    )

    try:
        _celery_app().send_task(
            "sql_execute.run",
            kwargs={
                "execution_id": execution_id,
                "payload": internal_request.model_dump(mode="json"),
            },
            task_id=execution_id,
        )
    except Exception as exc:
        service.mark_enqueue_failed(execution_id, f"Failed to enqueue execution: {exc}")
        return ExecuteSqlResponse(
            execution_id=execution_id,
            status="failed",
            engine=connection.engine,
            error_message=f"Failed to enqueue execution: {exc}",
        )

    return queued


@router.get("/executions/{execution_id}", response_model=ExecutionDetailResponse)
def get_execution(
    execution_id: str,
) -> ExecutionDetailResponse:
    service, _ = _services()
    detail = service.get_detail(execution_id=execution_id, result=_result_payload(execution_id))
    if detail is None:
        raise HTTPException(status_code=404, detail="Execution id is unknown.")
    # Legacy executions without an actor remain readable. Protected executions
    # trust only the authenticated identity, never a caller-supplied actor id.
    if (
        detail.history.actor_id is not None
        and detail.history.actor_id
        != resolve_current_user(detail.history.workspace_id).id
    ):
        raise HTTPException(status_code=403, detail="Access denied.")
    return detail


@router.post("/executions/{execution_id}/cancel", response_model=CancelExecutionResponse)
def cancel_execution(
    execution_id: str,
    actor_id: str | None = Query(default=None),
) -> CancelExecutionResponse:
    service, _ = _services()
    # Fetch execution to verify ownership
    history = service.get(execution_id)
    # ACL check: if execution has actor_id, require matching actor_id
    # Legacy executions (actor_id=None) are accessible without auth
    if history is not None and history.actor_id is not None and history.actor_id != actor_id:
        raise HTTPException(status_code=403, detail="Access denied. Only the owner can cancel.")
    response = service.cancel(execution_id)
    if response.cancelled:
        with suppress(Exception):
            _celery_app().AsyncResult(execution_id).revoke(terminate=False)
    return response


@router.get("/executions", response_model=list[ExecutionHistoryItem])
def list_executions(
    workspace_id: str = Query(default="default", min_length=1),
    limit: int = Query(default=50, ge=1, le=500),
    actor_id: str | None = Query(default=None),
) -> list[ExecutionHistoryItem]:
    service, _ = _services()
    return service.list_history(workspace_id=workspace_id, limit=limit, actor_id=actor_id)
