from functools import lru_cache

from rhetorica_backend.contracts import ExecuteSqlRequest, SqlGuardRequest
from rhetorica_backend.domain.execution import build_execution_service
from rhetorica_backend.domain.execution.service import ExecutionService
from rhetorica_backend.domain.rag import RagInsightPipelineService, RagPipelineTaskRequest
from rhetorica_backend.infra.celery_runtime import build_celery_app
from rhetorica_backend.infra.logging import configure_logging, get_logger
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.rag_connector_repository import RagConnectorRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.sql_guard.service import SqlGuardService

from rhetorica_worker.runtime.settings import settings

configure_logging(
    service_name="worker",
    environment=settings.app_env,
    log_level=settings.log_level,
    json_logs=settings.log_json,
)

celery_app = build_celery_app(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,
)

_service = SqlGuardService()
logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _execution_runtime() -> tuple[ExecutionService, DatabaseRuntime]:
    service, runtime = build_execution_service(settings.database_url)
    runtime.create_schema()
    return service, runtime


@lru_cache(maxsize=1)
def _rag_pipeline_service() -> RagInsightPipelineService:
    _, runtime = _execution_runtime()
    return RagInsightPipelineService(
        chat_repository=ChatRepository(db=runtime),
        llm_connector_repository=LlmConnectorRepository(db=runtime),
        rag_connector_repository=RagConnectorRepository(db=runtime),
    )


@celery_app.task(name="sql_guard.check")
def sql_guard_check(payload: dict[str, object]) -> dict[str, object]:
    request = SqlGuardRequest.model_validate(payload)
    task_logger = logger.bind(task_name="sql_guard.check", workspace_id=request.workspace_id)
    task_logger.info("worker.sql_guard.started")
    response = _service.guard(request)
    task_logger.info(
        "worker.sql_guard.completed",
        allowed=response.allowed,
        warnings=response.warnings,
    )
    return response.model_dump(mode="json")


@celery_app.task(name="sql_execute.run")
def sql_execute_run(execution_id: str, payload: dict[str, object]) -> dict[str, object]:
    request = ExecuteSqlRequest.model_validate(payload)
    task_logger = logger.bind(
        task_name="sql_execute.run",
        execution_id=execution_id,
        workspace_id=request.workspace_id,
        engine=request.connection.engine,
    )
    task_logger.info("worker.sql_execute.started")
    response = _execution_runtime()[0].execute_prepared(execution_id=execution_id, request=request)
    response_status = getattr(response, "status", None)
    response_query_id = getattr(response, "query_id", None)
    if (response_status is None or response_query_id is None) and hasattr(response, "model_dump"):
        payload_dump = response.model_dump(mode="json")
        if isinstance(payload_dump, dict):
            response_status = payload_dump.get("status", response_status)
            response_query_id = payload_dump.get("query_id", response_query_id)
    task_logger.info(
        "worker.sql_execute.completed",
        status=response_status,
        query_id=response_query_id,
    )
    return response.model_dump(mode="json")


@celery_app.task(name="sql_execute.cancel")
def sql_execute_cancel(execution_id: str) -> dict[str, object]:
    task_logger = logger.bind(task_name="sql_execute.cancel", execution_id=execution_id)
    task_logger.info("worker.sql_execute.cancel_requested")
    response = _execution_runtime()[0].cancel(execution_id)
    task_logger.info("worker.sql_execute.cancel_completed", status=response.status)
    return response.model_dump(mode="json")


@celery_app.task(name="rag_insights.process_feedback")
def rag_insights_process_feedback(payload: dict[str, object]) -> dict[str, object]:
    request = RagPipelineTaskRequest.model_validate(payload)
    task_logger = logger.bind(
        task_name="rag_insights.process_feedback",
        workspace_id=request.workspace_id,
        conversation_id=request.conversation_id,
        message_id=request.message_id,
    )
    task_logger.info("worker.rag_feedback.started")
    try:
        result = _rag_pipeline_service().process_feedback(request)
    except Exception:
        task_logger.exception("worker.rag_feedback.failed")
        return {"status": "skipped", "reason": "pipeline_error", "point_id": None}
    task_logger.info(
        "worker.rag_feedback.completed",
        status=result.status,
        reason=result.reason,
        point_id=result.point_id,
    )
    return result.model_dump(mode="json")
