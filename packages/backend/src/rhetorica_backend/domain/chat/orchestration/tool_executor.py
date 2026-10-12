"""Tool executor - executes LLM tool calls."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import replace
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.error_contract import (
    ErrorCategory,
    build_tool_error,
)
from rhetorica_backend.domain.chat.orchestration.capability_registry import (
    RuntimeCapabilityState,
    register_tool_usage,
)
from rhetorica_backend.domain.chat.orchestration.models import ToolCallRequest
from rhetorica_backend.domain.chat.orchestration.tool_airflow_capability_service import (
    ToolAirflowCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_capability import (
    BoundToolCapability,
)
from rhetorica_backend.domain.chat.orchestration.tool_capability_registry import (
    ToolCapabilityRegistry,
)
from rhetorica_backend.domain.chat.orchestration.tool_docs_capability_service import (
    ToolDocsCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_documentation_capability_service import (
    ToolDocumentationCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_support_kernel import (
    ToolExecutionSupportKernel,
)
from rhetorica_backend.domain.chat.orchestration.tool_file_capability_service import (
    ToolFileCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_gitlab_capability_service import (
    ToolGitLabCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_group_capability_service import (
    ToolGroupCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_kubernetes_capability_service import (
    ToolKubernetesCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_mcp_capability_service import (
    ToolMcpCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_result_artifacts import (
    ToolResultArtifactStore,
)
from rhetorica_backend.domain.chat.orchestration.tool_sql_capability_service import (
    ToolSqlCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_victoriametrics_capability_service import (
    ToolVictoriaMetricsCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_visualization_capability_service import (
    ToolVisualizationCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_workspace_skill_capability_service import (
    ToolWorkspaceSkillCapabilityService,
)
from rhetorica_backend.domain.chat.settings import get_chat_settings
from rhetorica_backend.domain.chat.sql.semantic_preflight import (
    SemanticSqlPreflightService,
)
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter
from rhetorica_backend.domain.chat.tool_evidence import ToolEvidenceStore
from rhetorica_backend.domain.documentation import DocumentationRetrievalService
from rhetorica_backend.domain.mcp_connectors import McpToolExecutionService
from rhetorica_backend.domain.rag.retrieval_service import RagInsightRetriever
from rhetorica_backend.infra.postgres.airflow_connector_repository import (
    AirflowConnectorRepository,
)
from rhetorica_backend.infra.postgres.gitlab_connector_repository import (
    GitLabConnectorRepository,
)
from rhetorica_backend.infra.postgres.kubernetes_connector_repository import (
    KubernetesConnectorRepository,
)
from rhetorica_backend.infra.postgres.mcp_connector_repository import (
    McpConnectorRepository,
)
from rhetorica_backend.infra.postgres.victoriametrics_connector_repository import (
    VictoriaMetricsConnectorRepository,
)
from rhetorica_backend.infra.postgres.workspace_skill_repository import (
    WorkspaceSkillRepository,
)

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import (
        ToolOrchestrationState,
    )


logger = logging.getLogger(__name__)

_TOOL_ARTIFACT_TOOL_NAMES = frozenset({
    "inspect_tool_artifact",
    "read_tool_artifact",
    "find_tool_artifact_text",
    "list_recent_tool_artifacts",
    "remember_tool_artifact",
    "read_tool_evidence",
    "forget_tool_evidence",
})

_MAX_DURABLE_TOOL_EVIDENCE_CHARS = 500_000
_MAX_TOOL_EVIDENCE_NOTE_CHARS = 4_000


class ToolExecutor:
    """Thin coordinator that composes capability-family services."""

    def __init__(
        self,
        sql_router: SqlRouter,
        rag_retriever: RagInsightRetriever | None = None,
        documentation_retrieval_service: DocumentationRetrievalService | None = None,
        gitlab_connector_repository: GitLabConnectorRepository | None = None,
        airflow_connector_repository: AirflowConnectorRepository | None = None,
        kubernetes_connector_repository: KubernetesConnectorRepository | None = None,
        victoriametrics_connector_repository: VictoriaMetricsConnectorRepository | None = None,
        mcp_connector_repository: McpConnectorRepository | None = None,
        semantic_preflight_service: SemanticSqlPreflightService | None = None,
        workspace_skill_repository: WorkspaceSkillRepository | None = None,
        tool_evidence_store: ToolEvidenceStore | None = None,
        tool_execution_timeout: float | None = None,
        file_capability_service: ToolFileCapabilityService | None = None,
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self._tool_evidence_store = tool_evidence_store
        self._file_tools = file_capability_service
        self._tool_execution_timeout = (
            get_chat_settings().tool_execution_timeout
            if tool_execution_timeout is None
            else tool_execution_timeout
        )
        self._support = ToolExecutionSupportKernel(
            sql_router=sql_router,
            rag_retriever=rag_retriever,
            semantic_preflight_service=semantic_preflight_service,
        )
        self._sql_tools = ToolSqlCapabilityService(
            support=self._support,
            sql_router=sql_router,
            semantic_preflight_service=self._support.semantic_preflight_service,
            logger=self._logger,
        )
        self._docs_tools = ToolDocsCapabilityService(
            support=self._support,
            rag_retriever=rag_retriever,
        )
        self._documentation_tools = ToolDocumentationCapabilityService(
            documentation_retrieval_service=documentation_retrieval_service,
            bounded_int=self._support._bounded_int,
            tool_error_from_exception=self._support._tool_error_from_exception,
        )
        self._gitlab_tools = ToolGitLabCapabilityService(
            gitlab_connector_repository=gitlab_connector_repository,
            bounded_int=self._support._bounded_int,
            tool_error_from_exception=self._support._tool_error_from_exception,
        )
        self._airflow_tools = ToolAirflowCapabilityService(
            airflow_connector_repository=airflow_connector_repository,
            bounded_int=self._support._bounded_int,
            tool_error_from_exception=self._support._tool_error_from_exception,
        )
        self._kubernetes_tools = ToolKubernetesCapabilityService(
            kubernetes_connector_repository=kubernetes_connector_repository,
            bounded_int=self._support._bounded_int,
            tool_error_from_exception=self._support._tool_error_from_exception,
        )
        self._victoriametrics_tools = ToolVictoriaMetricsCapabilityService(
            victoriametrics_connector_repository=victoriametrics_connector_repository,
            bounded_int=self._support._bounded_int,
            tool_error_from_exception=self._support._tool_error_from_exception,
        )
        self._visualization_tools = ToolVisualizationCapabilityService()
        self._workspace_skill_tools = ToolWorkspaceSkillCapabilityService(
            workspace_skill_repository=workspace_skill_repository,
        )
        self._tool_group_tools = ToolGroupCapabilityService()
        self._mcp_tool_execution_service = (
            McpToolExecutionService(repository=mcp_connector_repository)
            if mcp_connector_repository is not None
            else None
        )
        self._mcp_tools = (
            ToolMcpCapabilityService(repository=mcp_connector_repository)
            if mcp_connector_repository is not None
            else None
        )
        capabilities = [
            BoundToolCapability(
                name="list_tool_groups",
                handler=self._tool_group_tools.list_groups,
            ),
            BoundToolCapability(
                name="load_tool_group",
                handler=self._tool_group_tools.load_group,
            ),
            BoundToolCapability(
                name="unload_tool_group",
                handler=self._tool_group_tools.unload_group,
            ),
            BoundToolCapability(
                name="run_sql",
                handler=self._execute_run_sql,
                requires_db_connectors=True,
            ),
            BoundToolCapability(
                name="build_visualization",
                handler=self._execute_build_visualization,
                requires_db_connectors=True,
            ),
            BoundToolCapability(
                name="search_rag_insights",
                handler=self._execute_search_rag_insights,
                time_bound=True,
            ),
            BoundToolCapability(
                name="search_documentation",
                handler=self._execute_search_documentation,
                time_bound=True,
            ),
            BoundToolCapability(
                name="read_documentation_page",
                handler=self._execute_read_documentation_page,
                time_bound=True,
            ),
            BoundToolCapability(
                name="search_gitlab_content",
                handler=self._execute_search_gitlab_content,
                time_bound=True,
            ),
            BoundToolCapability(
                name="find_gitlab_paths",
                handler=self._execute_find_gitlab_paths,
                time_bound=True,
            ),
            BoundToolCapability(
                name="read_gitlab_file",
                handler=self._execute_read_gitlab_file,
                time_bound=True,
            ),
            BoundToolCapability(
                name="list_gitlab_tree",
                handler=self._execute_list_gitlab_tree,
                time_bound=True,
            ),
            BoundToolCapability(
                name="get_gitlab_file_blame",
                handler=self._execute_get_gitlab_file_blame,
                time_bound=True,
            ),
            BoundToolCapability(
                name="search_airflow_dags",
                handler=self._execute_search_airflow_dags,
                time_bound=True,
            ),
            BoundToolCapability(
                name="get_airflow_dag",
                handler=self._execute_get_airflow_dag,
                time_bound=True,
            ),
            BoundToolCapability(
                name="list_airflow_dag_runs",
                handler=self._execute_list_airflow_dag_runs,
                time_bound=True,
            ),
            BoundToolCapability(
                name="get_airflow_dag_run",
                handler=self._execute_get_airflow_dag_run,
                time_bound=True,
            ),
            BoundToolCapability(
                name="list_airflow_task_instances",
                handler=self._execute_list_airflow_task_instances,
                time_bound=True,
            ),
            BoundToolCapability(
                name="get_airflow_task_logs",
                handler=self._execute_get_airflow_task_logs,
                time_bound=True,
            ),
            BoundToolCapability(
                name="list_airflow_import_errors",
                handler=self._execute_list_airflow_import_errors,
                time_bound=True,
            ),
            BoundToolCapability(
                name="get_airflow_health",
                handler=self._execute_get_airflow_health,
                time_bound=True,
            ),
            BoundToolCapability(
                name="list_kubernetes_pods",
                handler=self._execute_list_kubernetes_pods,
                time_bound=True,
            ),
            BoundToolCapability(
                name="get_kubernetes_namespace_status",
                handler=self._execute_get_kubernetes_namespace_status,
                time_bound=True,
            ),
            BoundToolCapability(
                name="get_kubernetes_pod",
                handler=self._execute_get_kubernetes_pod,
                time_bound=True,
            ),
            BoundToolCapability(
                name="read_kubernetes_pod_logs",
                handler=self._execute_read_kubernetes_pod_logs,
                time_bound=True,
            ),
            BoundToolCapability(
                name="list_kubernetes_deployments",
                handler=self._execute_list_kubernetes_deployments,
                time_bound=True,
            ),
            BoundToolCapability(
                name="get_kubernetes_deployment",
                handler=self._execute_get_kubernetes_deployment,
                time_bound=True,
            ),
            BoundToolCapability(
                name="get_kubernetes_rollout_status",
                handler=self._execute_get_kubernetes_rollout_status,
                time_bound=True,
            ),
            BoundToolCapability(
                name="list_kubernetes_replicasets",
                handler=self._execute_list_kubernetes_replicasets,
                time_bound=True,
            ),
            BoundToolCapability(
                name="list_kubernetes_statefulsets",
                handler=self._execute_list_kubernetes_statefulsets,
                time_bound=True,
            ),
            BoundToolCapability(
                name="list_kubernetes_jobs",
                handler=self._execute_list_kubernetes_jobs,
                time_bound=True,
            ),
            BoundToolCapability(
                name="get_kubernetes_job",
                handler=self._execute_get_kubernetes_job,
                time_bound=True,
            ),
            BoundToolCapability(
                name="list_kubernetes_ingresses",
                handler=self._execute_list_kubernetes_ingresses,
                time_bound=True,
            ),
            BoundToolCapability(
                name="list_kubernetes_services",
                handler=self._execute_list_kubernetes_services,
                time_bound=True,
            ),
            BoundToolCapability(
                name="list_kubernetes_endpoints",
                handler=self._execute_list_kubernetes_endpoints,
                time_bound=True,
            ),
            BoundToolCapability(
                name="list_kubernetes_events",
                handler=self._execute_list_kubernetes_events,
                time_bound=True,
            ),
            BoundToolCapability(
                name="list_kubernetes_resource_quotas",
                handler=self._execute_list_kubernetes_resource_quotas,
                time_bound=True,
            ),
            BoundToolCapability(
                name="list_victoriametrics_metric_templates",
                handler=self._execute_list_victoriametrics_metric_templates,
                time_bound=True,
            ),
            BoundToolCapability(
                name="query_victoriametrics_instant",
                handler=self._execute_query_victoriametrics_instant,
                time_bound=True,
            ),
            BoundToolCapability(
                name="query_victoriametrics_range",
                handler=self._execute_query_victoriametrics_range,
                time_bound=True,
            ),
            BoundToolCapability(
                name="read_workspace_skill",
                handler=self._execute_read_workspace_skill,
            ),
            BoundToolCapability(
                name="list_workspace_skill_files",
                handler=self._execute_list_workspace_skill_files,
            ),
            BoundToolCapability(
                name="inspect_tool_artifact",
                handler=self._execute_inspect_tool_artifact,
            ),
            BoundToolCapability(
                name="read_tool_artifact",
                handler=self._execute_read_tool_artifact,
            ),
            BoundToolCapability(
                name="find_tool_artifact_text",
                handler=self._execute_find_tool_artifact_text,
            ),
            BoundToolCapability(
                name="list_recent_tool_artifacts",
                handler=self._execute_list_recent_tool_artifacts,
            ),
            BoundToolCapability(
                name="remember_tool_artifact",
                handler=self._execute_remember_tool_artifact,
            ),
            BoundToolCapability(
                name="read_tool_evidence",
                handler=self._execute_read_tool_evidence,
            ),
            BoundToolCapability(
                name="forget_tool_evidence",
                handler=self._execute_forget_tool_evidence,
            ),
        ]
        if self._mcp_tools is not None:
            capabilities.extend([
                BoundToolCapability(
                    name="list_mcp_tools",
                    handler=self._mcp_tools.list_tools,
                ),
                BoundToolCapability(
                    name="load_mcp_tools",
                    handler=self._mcp_tools.load_tools,
                ),
                BoundToolCapability(
                    name="unload_mcp_tools",
                    handler=self._mcp_tools.unload_tools,
                ),
            ])
        if self._file_tools is not None:
            capabilities.extend(
                BoundToolCapability(
                    name=name,
                    handler=self._file_handler(name),
                )
                for name in ToolFileCapabilityService.TOOL_NAMES
            )
        self._capability_registry = ToolCapabilityRegistry(capabilities)

    def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        tool_request = ToolCallRequest.model_validate({
            "tool_name": tool_name,
            "arguments": arguments,
        })
        capability = self._capability_registry.get(tool_request.tool_name)
        if capability is not None:
            result = self._execute_capability_with_timeout(
                capability=capability,
                tool_name=tool_request.tool_name,
                request=request,
                state=state,
                arguments=tool_request.arguments,
                db_connectors=db_connectors,
            )
            self._record_tool_usage(
                tool_name=tool_request.tool_name,
                arguments=tool_request.arguments,
                request=request,
                result=result,
                state=state,
            )
            return result

        if self._mcp_tool_execution_service is not None and "__" in tool_request.tool_name:
            result = self._mcp_tool_execution_service.execute(
                workspace_id=request.workspace_id,
                exposed_tool_name=tool_request.tool_name,
                arguments=tool_request.arguments,
                allowed_connector_names=request.agent_allowed_mcp_connector_names,
            )
            self._record_tool_usage(
                tool_name=tool_request.tool_name,
                arguments=tool_request.arguments,
                request=request,
                result=result,
                state=state,
            )
            return result

        self._logger.warning("Unknown tool requested: %s", tool_name)
        state.add_warning("llm_unknown_tool_requested")
        result = build_tool_error(
            error_code="unknown_tool",
            error_category=ErrorCategory.RECOVERABLE,
            user_message=f"Unknown tool requested: {tool_name}.",
            retry_recommended=False,
            can_continue=True,
        )
        result["tool_name"] = tool_name
        self._record_tool_usage(
            tool_name=tool_name,
            arguments=arguments,
            request=request,
            result=result,
            state=state,
        )
        return result

    def _execute_capability_with_timeout(
        self,
        *,
        capability: Any,
        tool_name: str,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
        db_connectors: list[ConnectorItem],
    ) -> dict[str, Any]:
        timeout = self._tool_execution_timeout
        if timeout <= 0 or not getattr(capability, "time_bound", False):
            return capability.execute(
                request=request,
                arguments=arguments,
                state=state,
                db_connectors=db_connectors,
            )

        shadow_state = self._clone_timed_tool_state(state)
        result_queue: Queue[tuple[str, Any]] = Queue(maxsize=1)

        def run() -> None:
            try:
                result_queue.put_nowait((
                    "result",
                    capability.execute(
                        request=request,
                        arguments=arguments,
                        state=shadow_state,
                        db_connectors=db_connectors,
                    ),
                ))
            except BaseException as exc:
                result_queue.put_nowait(("error", exc))

        thread = threading.Thread(
            target=run,
            name=f"chat-tool-{tool_name}",
            daemon=True,
        )
        thread.start()
        try:
            kind, value = result_queue.get(timeout=timeout)
        except Empty:
            self._logger.warning(
                "Tool execution timed out",
                extra={"tool_name": tool_name, "timeout_seconds": timeout},
            )
            state.add_warning("tool_execution_timeout")
            result = build_tool_error(
                error_code="tool_timeout",
                error_category=ErrorCategory.TRANSIENT,
                user_message=f"Tool '{tool_name}' timed out after {timeout:g} seconds.",
                retry_recommended=True,
                can_continue=True,
            )
            result["tool_name"] = tool_name
            result["timeout_seconds"] = timeout
            return result

        if kind == "error":
            raise value
        self._merge_timed_tool_state(source=shadow_state, target=state)
        return value

    @staticmethod
    def _clone_timed_tool_state(
        state: ToolOrchestrationState,
    ) -> ToolOrchestrationState:
        return replace(
            state,
            warnings=list(state.warnings),
            metadata_sources=set(state.metadata_sources),
            context_tables=list(state.context_tables),
            metadata_table_columns={
                table_name: set(columns)
                for table_name, columns in state.metadata_table_columns.items()
            },
            metadata_table_schemas=dict(state.metadata_table_schemas),
            execution_attempts=list(state.execution_attempts),
            tool_call_results=dict(state.tool_call_results),
            capability_state=RuntimeCapabilityState(
                available_tools=list(state.capability_state.available_tools),
                used_tools=list(state.capability_state.used_tools),
                used_providers=list(state.capability_state.used_providers),
                degraded_capabilities=list(state.capability_state.degraded_capabilities),
                policy_decisions=list(state.capability_state.policy_decisions),
            ),
            llm_usages=list(state.llm_usages),
            discovered_metrics=list(state.discovered_metrics),
            sandbox_input_artifact_ids=list(state.sandbox_input_artifact_ids),
            sandbox_outputs={
                output_id: dict(manifest) for output_id, manifest in state.sandbox_outputs.items()
            },
            published_output_ids=set(state.published_output_ids),
            published_sandbox_outputs=dict(state.published_sandbox_outputs),
            published_files=list(state.published_files),
            deferred_tool_definitions=dict(state.deferred_tool_definitions),
            available_tool_groups=dict(state.available_tool_groups),
            loaded_tool_definitions=dict(state.loaded_tool_definitions),
            loaded_mcp_tool_definitions=dict(state.loaded_mcp_tool_definitions),
            loaded_tool_group_names=list(state.loaded_tool_group_names),
            loaded_mcp_remote_tool_names={
                connector_name: list(remote_tool_names)
                for connector_name, remote_tool_names in state.loaded_mcp_remote_tool_names.items()
            },
            loaded_mcp_exposed_tool_names={
                connector_name: dict(exposed_by_remote)
                for connector_name, exposed_by_remote in state.loaded_mcp_exposed_tool_names.items()
            },
        )

    @staticmethod
    def _merge_timed_tool_state(
        *,
        source: ToolOrchestrationState,
        target: ToolOrchestrationState,
    ) -> None:
        target.warnings = list(source.warnings)
        target.metadata_sources = set(source.metadata_sources)
        target.context_tables = list(source.context_tables)
        target.metadata_table_columns = {
            table_name: set(columns)
            for table_name, columns in source.metadata_table_columns.items()
        }
        target.metadata_table_schemas = dict(source.metadata_table_schemas)
        target.semantic_grounding = source.semantic_grounding
        target.execution_zone = source.execution_zone
        target.metadata_context_revision = source.metadata_context_revision
        target.unavailable_context_connector_fail = source.unavailable_context_connector_fail
        target.discovered_metrics = list(source.discovered_metrics)
        target.sandbox_session_id = source.sandbox_session_id
        target.sandbox_input_artifact_ids = list(source.sandbox_input_artifact_ids)
        target.sandbox_outputs = {
            output_id: dict(manifest) for output_id, manifest in source.sandbox_outputs.items()
        }
        target.published_output_ids = set(source.published_output_ids)
        target.published_sandbox_outputs = dict(source.published_sandbox_outputs)
        target.published_output_bytes = source.published_output_bytes
        target.published_files = list(source.published_files)
        target.deferred_tool_definitions = dict(source.deferred_tool_definitions)
        target.available_tool_groups = dict(source.available_tool_groups)
        target.loaded_tool_definitions = dict(source.loaded_tool_definitions)
        target.loaded_mcp_tool_definitions = dict(source.loaded_mcp_tool_definitions)
        target.loaded_tool_group_names = list(source.loaded_tool_group_names)
        target.loaded_mcp_remote_tool_names = {
            connector_name: list(remote_tool_names)
            for connector_name, remote_tool_names in source.loaded_mcp_remote_tool_names.items()
        }
        target.loaded_mcp_exposed_tool_names = {
            connector_name: dict(exposed_by_remote)
            for connector_name, exposed_by_remote in source.loaded_mcp_exposed_tool_names.items()
        }

    def _execute_run_sql(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._sql_tools.execute_run_sql(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
            state=state,
        )

    def _execute_build_visualization(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._visualization_tools.execute_build_visualization(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
            state=state,
        )

    def _execute_search_rag_insights(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._docs_tools.execute_search_rag_insights(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_search_documentation(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._documentation_tools.execute_search_documentation(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_read_documentation_page(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._documentation_tools.execute_read_documentation_page(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_search_gitlab_content(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._gitlab_tools.execute_search_gitlab_content(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_read_gitlab_file(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._gitlab_tools.execute_read_gitlab_file(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_find_gitlab_paths(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._gitlab_tools.execute_find_gitlab_paths(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_gitlab_tree(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._gitlab_tools.execute_list_gitlab_tree(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_get_gitlab_file_blame(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._gitlab_tools.execute_get_gitlab_file_blame(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_search_airflow_dags(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._airflow_tools.execute_search_airflow_dags(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_get_airflow_dag(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._airflow_tools.execute_get_airflow_dag(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_airflow_dag_runs(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._airflow_tools.execute_list_airflow_dag_runs(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_get_airflow_dag_run(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._airflow_tools.execute_get_airflow_dag_run(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_airflow_task_instances(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._airflow_tools.execute_list_airflow_task_instances(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_get_airflow_task_logs(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._airflow_tools.execute_get_airflow_task_logs(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_airflow_import_errors(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._airflow_tools.execute_list_airflow_import_errors(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_get_airflow_health(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._airflow_tools.execute_get_airflow_health(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_kubernetes_pods(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_list_kubernetes_pods(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_get_kubernetes_namespace_status(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_get_kubernetes_namespace_status(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_get_kubernetes_pod(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_get_kubernetes_pod(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_read_kubernetes_pod_logs(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_read_kubernetes_pod_logs(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_kubernetes_deployments(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_list_kubernetes_deployments(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_get_kubernetes_deployment(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_get_kubernetes_deployment(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_get_kubernetes_rollout_status(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_get_kubernetes_rollout_status(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_kubernetes_replicasets(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_list_kubernetes_replicasets(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_kubernetes_statefulsets(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_list_kubernetes_statefulsets(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_kubernetes_jobs(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_list_kubernetes_jobs(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_get_kubernetes_job(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_get_kubernetes_job(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_kubernetes_ingresses(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_list_kubernetes_ingresses(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_kubernetes_services(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_list_kubernetes_services(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_kubernetes_endpoints(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_list_kubernetes_endpoints(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_kubernetes_events(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_list_kubernetes_events(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_kubernetes_resource_quotas(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._kubernetes_tools.execute_list_kubernetes_resource_quotas(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_victoriametrics_metric_templates(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._victoriametrics_tools.execute_list_victoriametrics_metric_templates(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_query_victoriametrics_instant(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._victoriametrics_tools.execute_query_victoriametrics_instant(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_query_victoriametrics_range(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._victoriametrics_tools.execute_query_victoriametrics_range(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_read_workspace_skill(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._workspace_skill_tools.execute_read_workspace_skill(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _execute_list_workspace_skill_files(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        return self._workspace_skill_tools.execute_list_workspace_skill_files(
            request=request,
            arguments=arguments,
            state=state,
        )

    def _file_handler(self, tool_name: str) -> Callable[..., dict[str, Any]]:
        def execute(
            *,
            request: ChatAskRequest,
            arguments: dict[str, Any],
            state: ToolOrchestrationState,
        ) -> dict[str, Any]:
            if self._file_tools is None:
                return {
                    "ok": False,
                    "error_code": "file_processing_unavailable",
                    "can_continue": True,
                }
            return self._file_tools.execute(
                tool_name=tool_name,
                request=request,
                arguments=arguments,
                state=state,
            )

        return execute

    def _execute_inspect_tool_artifact(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        _ = request
        artifact_id = str(arguments.get("artifact_id") or "")
        return state.tool_result_artifacts.inspect(
            artifact_id=artifact_id,
            max_paths=arguments.get("max_paths", 200),
        )

    def _execute_read_tool_artifact(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        _ = request
        artifact_id = str(arguments.get("artifact_id") or "")
        json_pointer = arguments.get("json_pointer")
        return state.tool_result_artifacts.read(
            artifact_id=artifact_id,
            json_pointer=json_pointer if isinstance(json_pointer, str) else None,
            offset=arguments.get("offset", 0),
            limit=arguments.get("limit", 12_000),
            format=arguments.get("format", "text"),
            fields=arguments.get("fields"),
            parse_json=arguments.get("parse_json") is True,
        )

    def _execute_find_tool_artifact_text(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        _ = request
        artifact_id = str(arguments.get("artifact_id") or "")
        text = str(arguments.get("text") or "")
        return state.tool_result_artifacts.find_text(
            artifact_id=artifact_id,
            text=text,
            case_sensitive=arguments.get("case_sensitive") is True,
            max_matches=arguments.get("max_matches", 20),
            context_chars=arguments.get("context_chars", 200),
        )

    def _execute_list_recent_tool_artifacts(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        """List retained handles for exact, model-selected cross-turn access."""
        _ = request
        return state.tool_result_artifacts.list_recent(
            limit=arguments.get("limit", 20),
        )

    def _execute_remember_tool_artifact(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        """Promote a non-expired conversation artifact after an explicit decision."""
        _ = request
        if self._tool_evidence_store is None or not state.conversation_id:
            return build_tool_error(
                error_code="tool_evidence_store_unavailable",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Conversation tool evidence storage is unavailable.",
                retry_recommended=False,
                can_continue=True,
            )
        artifact_id = str(arguments.get("artifact_id") or "").strip()
        note = str(arguments.get("note") or "").strip()
        if not artifact_id or not note:
            return build_tool_error(
                error_code="tool_evidence_arguments_invalid",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="artifact_id and a factual note are required.",
                retry_recommended=False,
                can_continue=True,
            )
        if len(note) > _MAX_TOOL_EVIDENCE_NOTE_CHARS:
            return build_tool_error(
                error_code="tool_evidence_note_too_large",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=(
                    f"Tool evidence note must be at most "
                    f"{_MAX_TOOL_EVIDENCE_NOTE_CHARS} characters."
                ),
                retry_recommended=False,
                can_continue=True,
            )
        artifact = state.tool_result_artifacts.export(artifact_id=artifact_id)
        if artifact is None:
            error = build_tool_error(
                error_code="tool_artifact_not_found",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=(
                    f"Tool artifact '{artifact_id}' is unavailable or expired in this conversation."
                ),
                retry_recommended=False,
                can_continue=True,
            )
            error["next_action_hint"] = (
                "Call list_recent_tool_artifacts and retry with an exact ta_<uuid> "
                "artifact_id. Never pass execution_id."
            )
            return error
        if artifact.char_count > _MAX_DURABLE_TOOL_EVIDENCE_CHARS:
            return build_tool_error(
                error_code="tool_evidence_payload_too_large",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=(
                    "The raw tool artifact is too large for durable evidence storage. "
                    "Keep working from the current-turn artifact instead."
                ),
                retry_recommended=False,
                can_continue=True,
            )
        try:
            evidence = self._tool_evidence_store.upsert_tool_evidence(
                conversation_id=state.conversation_id,
                tool_name=artifact.tool_name,
                arguments=artifact.arguments,
                payload=artifact.payload,
                note=note,
                content_sha256=artifact.sha256,
                source_char_count=artifact.char_count,
                observed_at=artifact.observed_at,
            )
        except Exception as exc:
            self._logger.warning("Failed to persist tool evidence", exc_info=exc)
            return build_tool_error(
                error_code="tool_evidence_persist_failed",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Could not persist the tool evidence note.",
                retry_recommended=False,
                can_continue=True,
            )
        return {
            "ok": True,
            "evidence_key": evidence.evidence_key,
            "tool_name": evidence.tool_name,
            "arguments": evidence.arguments,
            "content_sha256": evidence.content_sha256,
            "observed_at": evidence.observed_at.isoformat(),
            "note": evidence.note,
            "replacement_semantics": "same tool and exact arguments replace the prior note",
        }

    def _execute_read_tool_evidence(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        """Read a verified durable artifact without calling the source system."""
        _ = request
        if self._tool_evidence_store is None or not state.conversation_id:
            return build_tool_error(
                error_code="tool_evidence_store_unavailable",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Conversation tool evidence storage is unavailable.",
                retry_recommended=False,
                can_continue=True,
            )
        evidence_key = str(arguments.get("evidence_key") or "").strip()
        evidence = self._tool_evidence_store.get_tool_evidence(
            conversation_id=state.conversation_id,
            evidence_key=evidence_key,
        )
        if evidence is None:
            return build_tool_error(
                error_code="tool_evidence_not_found",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=f"Tool evidence '{evidence_key}' was not found.",
                retry_recommended=False,
                can_continue=True,
            )
        artifact_store = ToolResultArtifactStore()
        artifact = artifact_store.register(
            tool_name=evidence.tool_name,
            arguments=evidence.arguments,
            result=evidence.payload,
        )
        result = artifact_store.read(
            artifact_id=str(artifact["artifact_id"]),
            json_pointer=(
                arguments.get("json_pointer")
                if isinstance(arguments.get("json_pointer"), str)
                else None
            ),
            offset=arguments.get("offset", 0),
            limit=arguments.get("limit", 12_000),
        )
        result.update({
            "evidence_key": evidence.evidence_key,
            "source_tool_name": evidence.tool_name,
            "source_arguments": evidence.arguments,
            "source_content_sha256": evidence.content_sha256,
            "source_observed_at": evidence.observed_at.isoformat(),
            "note": evidence.note,
            "source_system_called": False,
        })
        return result

    def _execute_forget_tool_evidence(
        self,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        """Delete one agent-owned note by its exact evidence key."""
        _ = request
        if self._tool_evidence_store is None or not state.conversation_id:
            return build_tool_error(
                error_code="tool_evidence_store_unavailable",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Conversation tool evidence storage is unavailable.",
                retry_recommended=False,
                can_continue=True,
            )
        evidence_key = str(arguments.get("evidence_key") or "").strip()
        deleted = self._tool_evidence_store.delete_tool_evidence(
            conversation_id=state.conversation_id,
            evidence_key=evidence_key,
        )
        return {
            "ok": True,
            "evidence_key": evidence_key,
            "deleted": deleted,
        }

    def _record_tool_usage(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        request: ChatAskRequest,
        result: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> None:
        providers = self._resolve_providers_for_tool_usage(
            tool_name=tool_name,
            arguments=arguments,
            request=request,
            result=result,
        )
        register_tool_usage(
            runtime_state=state.capability_state,
            tool_name=tool_name,
            providers=providers,
        )

    @classmethod
    def _resolve_providers_for_tool_usage(
        cls,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        request: ChatAskRequest,
        result: dict[str, Any],
    ) -> list[str]:
        if tool_name == "run_sql":
            connector_name = (
                result.get("selected_database_connector")
                or arguments.get("connector_name")
                or request.database_connector_name
            )
            if isinstance(connector_name, str) and connector_name.strip():
                return [f"database:{connector_name.strip()}"]
            return ["database"]

        if tool_name == "build_visualization":
            return ["visualization"]

        if tool_name == "search_rag_insights":
            connector_name = arguments.get("connector_name")
            if isinstance(connector_name, str) and connector_name.strip():
                return [f"rag:{connector_name.strip()}"]
            return ["rag"]

        if tool_name in {"search_documentation", "read_documentation_page"}:
            connector_name = arguments.get("connector_name")
            if isinstance(connector_name, str) and connector_name.strip():
                return [f"documentation:{connector_name.strip()}"]
            return ["documentation"]

        if tool_name in {
            "search_gitlab_content",
            "find_gitlab_paths",
            "read_gitlab_file",
            "list_gitlab_tree",
            "get_gitlab_file_blame",
        }:
            connector_name = arguments.get("connector_name")
            if isinstance(connector_name, str) and connector_name.strip():
                return [f"gitlab:{connector_name.strip()}"]
            return ["gitlab"]

        if tool_name in {
            "search_airflow_dags",
            "get_airflow_dag",
            "list_airflow_dag_runs",
            "get_airflow_dag_run",
            "list_airflow_task_instances",
            "get_airflow_task_logs",
            "list_airflow_import_errors",
            "get_airflow_health",
        }:
            connector_name = result.get("connector_name") or arguments.get("connector_name")
            if isinstance(connector_name, str) and connector_name.strip():
                return [f"airflow:{connector_name.strip()}"]
            return ["airflow"]

        if tool_name in {
            "list_kubernetes_pods",
            "get_kubernetes_namespace_status",
            "get_kubernetes_pod",
            "read_kubernetes_pod_logs",
            "list_kubernetes_deployments",
            "get_kubernetes_deployment",
            "get_kubernetes_rollout_status",
            "list_kubernetes_replicasets",
            "list_kubernetes_statefulsets",
            "list_kubernetes_jobs",
            "get_kubernetes_job",
            "list_kubernetes_ingresses",
            "list_kubernetes_services",
            "list_kubernetes_endpoints",
            "list_kubernetes_events",
            "list_kubernetes_resource_quotas",
        }:
            connector_name = result.get("connector_name") or arguments.get("connector_name")
            if isinstance(connector_name, str) and connector_name.strip():
                return [f"kubernetes:{connector_name.strip()}"]
            return ["kubernetes"]

        if tool_name in {
            "list_victoriametrics_metric_templates",
            "query_victoriametrics_instant",
            "query_victoriametrics_range",
        }:
            connector_name = result.get("connector_name") or arguments.get("connector_name")
            if isinstance(connector_name, str) and connector_name.strip():
                return [f"victoriametrics:{connector_name.strip()}"]
            return ["victoriametrics"]

        if tool_name == "read_workspace_skill":
            return ["workspace_skill_tool"]

        if tool_name in ToolMcpCapabilityService.TOOL_NAMES:
            connector_name = result.get("connector_name") or arguments.get("connector_name")
            if isinstance(connector_name, str) and connector_name:
                return [f"mcp:{connector_name}"]
            return ["mcp"]

        if tool_name in _TOOL_ARTIFACT_TOOL_NAMES:
            return ["tool_artifact"]

        if "__" in tool_name:
            connector_name = result.get("connector_name")
            if isinstance(connector_name, str) and connector_name.strip():
                return [f"mcp:{connector_name.strip()}"]
            return ["mcp"]

        return []
