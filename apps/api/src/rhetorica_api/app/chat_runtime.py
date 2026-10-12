from functools import lru_cache

from rhetorica_backend.domain.chat.orchestration.factory import ChatServiceFactory
from rhetorica_backend.domain.chat.orchestration.tool_file_capability_service import (
    ToolFileCapabilityService,
)
from rhetorica_backend.domain.execution import build_execution_service
from rhetorica_backend.domain.files.sql_export import SqlResultExportService
from rhetorica_backend.infra.llm.capability_registry import (
    get_global_capability_registry,
)
from rhetorica_backend.infra.postgres.airflow_connector_repository import (
    AirflowConnectorRepository,
)
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.documentation_connector_repository import (
    DocumentationConnectorRepository,
)
from rhetorica_backend.infra.postgres.gitlab_connector_repository import (
    GitLabConnectorRepository,
)
from rhetorica_backend.infra.postgres.kubernetes_connector_repository import (
    KubernetesConnectorRepository,
)
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.mcp_connector_repository import (
    McpConnectorRepository,
)
from rhetorica_backend.infra.postgres.rag_connector_repository import RagConnectorRepository
from rhetorica_backend.infra.postgres.victoriametrics_connector_repository import (
    VictoriaMetricsConnectorRepository,
)
from rhetorica_backend.infra.postgres.workspace_skill_repository import (
    WorkspaceSkillRepository,
)
from rhetorica_backend.infra.sandbox import HttpSandboxGateway

from rhetorica_api.app.file_runtime import (
    get_artifact_service,
    get_file_telemetry,
    get_sql_result_snapshot_store,
)
from rhetorica_api.app.settings import settings


@lru_cache(maxsize=1)
def get_chat_service_factory() -> ChatServiceFactory:
    execution_service, db_runtime = build_execution_service(
        settings.database_url,
        snapshot_store=get_sql_result_snapshot_store(),
        preview_row_limit=settings.sql_result_preview_rows,
    )
    db_runtime.create_schema()
    chat_repository = ChatRepository(db=db_runtime)

    def build_file_capability(owner_user_id: str) -> ToolFileCapabilityService:
        return ToolFileCapabilityService(
            artifact_service=get_artifact_service(),
            sandbox_gateway=HttpSandboxGateway(
                base_url=settings.sandbox_url,
                internal_token=settings.sandbox_internal_token,
            ),
            sql_export_service=SqlResultExportService(
                execution_reader=execution_service,
                message_reader=chat_repository,
                artifact_service=get_artifact_service(),
                snapshot_store=get_sql_result_snapshot_store(),
                max_export_rows=settings.file_sql_export_max_rows,
                max_export_bytes=settings.file_sql_export_max_bytes,
            ),
            owner_user_id=owner_user_id,
            telemetry=get_file_telemetry(),
        )

    return ChatServiceFactory(
        execution_service=execution_service,
        db_connector_repository=ConnectorRepository(db=db_runtime),
        llm_connector_repository=LlmConnectorRepository(db=db_runtime),
        rag_connector_repository=RagConnectorRepository(db=db_runtime),
        documentation_connector_repository=DocumentationConnectorRepository(db=db_runtime),
        gitlab_connector_repository=GitLabConnectorRepository(db=db_runtime),
        airflow_connector_repository=AirflowConnectorRepository(db=db_runtime),
        kubernetes_connector_repository=KubernetesConnectorRepository(db=db_runtime),
        victoriametrics_connector_repository=VictoriaMetricsConnectorRepository(db=db_runtime),
        mcp_connector_repository=McpConnectorRepository(db=db_runtime),
        workspace_skill_repository=WorkspaceSkillRepository(db=db_runtime),
        chat_repository=chat_repository,
        db=db_runtime,
        capability_registry=get_global_capability_registry(),
        file_capability_service_factory=build_file_capability,
    )
