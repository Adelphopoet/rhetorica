from datetime import UTC, datetime, timedelta
from functools import lru_cache

from celery import signals  # type: ignore[import-untyped]
from rhetorica_backend.contracts import ExecuteSqlRequest, SqlGuardRequest
from rhetorica_backend.domain.documentation import DocumentationSyncService
from rhetorica_backend.domain.execution import build_execution_service
from rhetorica_backend.domain.execution.service import ExecutionService
from rhetorica_backend.domain.files import ArtifactPolicy, ArtifactService
from rhetorica_backend.domain.rag import RagInsightPipelineService, RagPipelineTaskRequest
from rhetorica_backend.infra.celery_runtime import build_celery_app
from rhetorica_backend.infra.langfuse import LangfuseReviewPublisher
from rhetorica_backend.infra.logging import configure_logging, get_logger
from rhetorica_backend.infra.object_storage import (
    ObjectStorageSqlResultSnapshotStore,
    S3ObjectStorage,
    S3StorageConfig,
)
from rhetorica_backend.infra.observability.file_telemetry import (
    OpenTelemetryFileTelemetry,
)
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.documentation_connector_repository import (
    DocumentationConnectorRepository,
)
from rhetorica_backend.infra.postgres.execution_history_repository import (
    ExecutionHistoryRepository,
)
from rhetorica_backend.infra.postgres.file_artifact_repository import FileArtifactRepository
from rhetorica_backend.infra.postgres.langfuse_feedback_repository import (
    LangfuseFeedbackOutboxRepository,
)
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.rag_connector_repository import RagConnectorRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.sql_guard.service import SqlGuardService

from rhetorica_worker.runtime.settings import settings
from rhetorica_worker.runtime.telemetry import WorkerTelemetry

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
celery_app.conf.beat_schedule = {
    "human-review-deliver-langfuse": {
        "task": "human_review.deliver_langfuse",
        "schedule": 60.0,
    },
    "documentation-sync-due": {
        "task": "documentation.sync_due",
        "schedule": 60.0,
    },
    "file-artifacts-cleanup": {
        "task": "file_artifacts.cleanup",
        "schedule": 300.0,
    },
}

_telemetry = WorkerTelemetry(
    enabled=settings.otel_enabled,
    service_version=settings.app_version,
    environment=settings.app_env,
    otlp_endpoint=settings.otel_exporter_otlp_endpoint,
    otlp_insecure=settings.otel_exporter_otlp_insecure,
)


@signals.worker_process_init.connect(weak=False)
def _start_worker_telemetry(**_: object) -> None:
    _telemetry.start()


@signals.worker_process_shutdown.connect(weak=False)
def _stop_worker_telemetry(**_: object) -> None:
    _telemetry.stop()


_service = SqlGuardService()
logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _execution_runtime() -> tuple[ExecutionService, DatabaseRuntime]:
    service, runtime = build_execution_service(
        settings.database_url,
        snapshot_store=_snapshot_store(),
        preview_row_limit=settings.sql_result_preview_rows,
    )
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


@celery_app.task(name="human_review.deliver_langfuse")
def deliver_human_review_to_langfuse() -> dict[str, int]:
    """Project locally committed feedback to Langfuse without affecting local state."""
    _, runtime = _execution_runtime()
    repository = LangfuseFeedbackOutboxRepository(db=runtime)
    publisher = LangfuseReviewPublisher()
    delivered = 0
    failed = 0
    for entry in repository.claim_pending_delivery(limit=100):
        try:
            published = publisher.publish(entry.payload)
        except Exception as exc:
            repository.mark_delivery(
                outbox_id=entry.id,
                revision=entry.revision,
                claim_token=entry.claim_token,
                error=f"{type(exc).__name__}: {exc}"[:2_000],
            )
            failed += 1
            logger.exception("human_review.langfuse_delivery_failed", extra={"outbox_id": entry.id})
        else:
            if not published:
                repository.mark_delivery(
                    outbox_id=entry.id,
                    revision=entry.revision,
                    claim_token=entry.claim_token,
                    error="publisher_not_enabled",
                )
                failed += 1
                continue
            if repository.mark_delivery(
                outbox_id=entry.id,
                revision=entry.revision,
                claim_token=entry.claim_token,
                error=None,
            ):
                delivered += 1
    return {"delivered": delivered, "failed": failed}


@lru_cache(maxsize=1)
def _documentation_repository() -> DocumentationConnectorRepository:
    _, runtime = _execution_runtime()
    return DocumentationConnectorRepository(db=runtime)


@lru_cache(maxsize=1)
def _documentation_sync_service() -> DocumentationSyncService:
    return DocumentationSyncService(repository=_documentation_repository())


@lru_cache(maxsize=1)
def _object_storage() -> S3ObjectStorage:
    return S3ObjectStorage(
        S3StorageConfig(
            endpoint_url=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            secure=settings.s3_secure,
        )
    )


@lru_cache(maxsize=1)
def _snapshot_store() -> ObjectStorageSqlResultSnapshotStore:
    return ObjectStorageSqlResultSnapshotStore(
        storage=_object_storage(),
        max_rows=settings.file_sql_export_max_rows,
        max_bytes=settings.sql_result_snapshot_max_bytes,
        max_cell_bytes=settings.sql_result_snapshot_max_cell_bytes,
        max_row_bytes=settings.sql_result_snapshot_max_row_bytes,
    )


@lru_cache(maxsize=1)
def _artifact_service() -> ArtifactService:
    _, runtime = _execution_runtime()
    return ArtifactService(
        repository=FileArtifactRepository(runtime),
        storage=_object_storage(),
        policy=ArtifactPolicy(),
        telemetry=OpenTelemetryFileTelemetry(),
    )


@celery_app.task(name="file_artifacts.cleanup")
def file_artifacts_cleanup() -> dict[str, int]:
    cleaned = _artifact_service().cleanup(staged_ttl_hours=settings.file_staged_ttl_hours)
    _, runtime = _execution_runtime()
    history = ExecutionHistoryRepository(runtime)
    snapshots_cleaned = 0
    snapshots_failed = 0
    snapshots = history.list_snapshots_for_cleanup(
        orphan_before=datetime.now(tz=UTC) - timedelta(hours=settings.file_staged_ttl_hours),
        limit=100,
    )
    for snapshot in snapshots:
        try:
            _snapshot_store().delete(snapshot)
            history.finish_snapshot_cleanup(snapshot.execution_id)
            snapshots_cleaned += 1
        except Exception:
            snapshots_failed += 1
            logger.exception(
                "worker.sql_result_snapshot.cleanup_failed",
                execution_id=snapshot.execution_id,
            )
    logger.info(
        "worker.file_artifacts.cleanup_completed",
        cleaned=cleaned,
        sql_snapshots_cleaned=snapshots_cleaned,
        sql_snapshots_failed=snapshots_failed,
    )
    return {
        "cleaned": cleaned,
        "sql_snapshots_cleaned": snapshots_cleaned,
        "sql_snapshots_failed": snapshots_failed,
    }


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


@celery_app.task(name="documentation.sync_connector")
def documentation_sync_connector(
    connector_id: str,
    workspace_id: str,
    trigger: str = "manual",
) -> dict[str, object]:
    task_logger = logger.bind(
        task_name="documentation.sync_connector",
        connector_id=connector_id,
        workspace_id=workspace_id,
        trigger=trigger,
    )
    task_logger.info("worker.documentation_sync.started")
    connector = _documentation_repository().get_by_id(
        connector_id=connector_id,
        workspace_id=workspace_id,
    )
    if connector is None:
        task_logger.warning("worker.documentation_sync.connector_missing")
        return {"status": "failed", "error_message": "documentation_connector_missing"}
    result = _documentation_sync_service().sync_connector(
        connector=connector,
        trigger=trigger,
    )
    task_logger.info(
        "worker.documentation_sync.completed",
        status=result.status,
        pages_seen=result.pages_seen,
        pages_updated=result.pages_updated,
        chunks_indexed=result.chunks_indexed,
    )
    return result.model_dump(mode="json")


@celery_app.task(name="documentation.sync_due")
def documentation_sync_due() -> dict[str, object]:
    task_logger = logger.bind(task_name="documentation.sync_due")
    task_logger.info("worker.documentation_sync_due.started")
    results = _documentation_sync_service().sync_due(limit=20)
    task_logger.info("worker.documentation_sync_due.completed", count=len(results))
    return {
        "status": "completed",
        "count": len(results),
        "results": [result.model_dump(mode="json") for result in results],
    }
