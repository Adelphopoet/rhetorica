"""Shared composition root; API and worker entrypoints only choose their transport."""

from dataclasses import dataclass

from rhetorica_backend.domain.execution import build_execution_service
from rhetorica_backend.domain.files import ArtifactPolicy, ArtifactPolicyConfig, ArtifactService
from rhetorica_backend.domain.files.sql_export import SqlResultExportService
from rhetorica_backend.domain.scenarios.activities import ScenarioActivities
from rhetorica_backend.domain.scenarios.data_store import ScenarioDataStore
from rhetorica_backend.domain.scenarios.llm_step import ScenarioLlmStepRunner
from rhetorica_backend.domain.scenarios.llm_tools import ScenarioLlmTools
from rhetorica_backend.domain.scenarios.python_step import ScenarioPythonStepRunner
from rhetorica_backend.domain.scenarios.resources import ScenarioResourcePolicy
from rhetorica_backend.domain.scenarios.settings import ScenarioSettings
from rhetorica_backend.domain.scenarios.sql_step import ScenarioSqlStepRunner
from rhetorica_backend.domain.scenarios.workflow_service import ScenarioWorkflowService
from rhetorica_backend.infra.object_storage import (
    ObjectStorageSqlResultSnapshotStore,
    S3ObjectStorage,
    S3StorageConfig,
)
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.file_artifact_repository import FileArtifactRepository
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.scenario_activity_repository import ScenarioActivityRepository
from rhetorica_backend.infra.postgres.scenario_authoring_repository import (
    ScenarioAuthoringRepository,
)
from rhetorica_backend.infra.postgres.scenario_run_repository import ScenarioRunRepository
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.sandbox.http_gateway import HttpSandboxGateway


@dataclass
class ScenarioRuntime:
    service: ScenarioWorkflowService
    activities: ScenarioActivities
    repository: ScenarioActivityRepository


def build_scenario_runtime(settings: ScenarioSettings) -> ScenarioRuntime:
    storage = S3ObjectStorage(
        S3StorageConfig(
            settings.s3_endpoint,
            settings.s3_access_key,
            settings.s3_secret_key,
            settings.s3_bucket,
            settings.s3_region,
            settings.s3_secure,
        )
    )
    snapshots = ObjectStorageSqlResultSnapshotStore(
        storage=storage,
        max_rows=settings.file_sql_export_max_rows,
        max_bytes=settings.sql_result_snapshot_max_bytes,
        max_cell_bytes=settings.sql_result_snapshot_max_cell_bytes,
        max_row_bytes=settings.sql_result_snapshot_max_row_bytes,
    )
    execution, db = build_execution_service(
        settings.database_url,
        snapshot_store=snapshots,
        preview_row_limit=settings.sql_result_preview_rows,
    )
    data = ScenarioDataStore(storage)
    repository = ScenarioActivityRepository(db)
    chats = ChatRepository(db)
    artifacts = ArtifactService(
        repository=FileArtifactRepository(db),
        storage=storage,
        policy=ArtifactPolicy(
            ArtifactPolicyConfig(
                max_input_bytes=settings.file_max_input_bytes,
                max_files_per_turn=settings.file_max_files_per_turn,
                max_turn_bytes=settings.file_max_turn_bytes,
                max_output_bytes=settings.file_max_output_bytes,
                max_session_output_bytes=settings.file_max_session_output_bytes,
                user_quota_bytes=settings.file_user_quota_bytes,
                workspace_quota_bytes=settings.file_workspace_quota_bytes,
            )
        ),
    )
    resources = ScenarioResourcePolicy(
        UserAccountRepository(db),
        AgentProfileRepository(db),
        ConnectorRepository(db),
        LlmConnectorRepository(db),
    )
    exports = SqlResultExportService(
        execution_reader=execution,
        message_reader=chats,
        artifact_service=artifacts,
        snapshot_store=snapshots,
        max_export_rows=settings.file_sql_export_max_rows,
        max_export_bytes=settings.file_sql_export_max_bytes,
    )
    sql = ScenarioSqlStepRunner(execution, exports, resources, repository)
    python = ScenarioPythonStepRunner(
        HttpSandboxGateway(
            base_url=settings.sandbox_url, internal_token=settings.sandbox_internal_token
        ),
        artifacts,
        data,
        repository,
    )
    llm = ScenarioLlmStepRunner(resources, repository, data)
    tools = ScenarioLlmTools(sql, python, resources, repository, data)
    return ScenarioRuntime(
        ScenarioWorkflowService(
            ScenarioAuthoringRepository(db),
            ScenarioRunRepository(db),
            resources,
            chats,
            data,
            llm,
            enabled=settings.scenario_workflows_enabled,
        ),
        ScenarioActivities(repository, data, resources, sql, python, llm, tools),
        repository,
    )
