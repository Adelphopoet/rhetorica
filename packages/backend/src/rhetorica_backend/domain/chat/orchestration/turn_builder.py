"""Shared builder for orchestration turn setup."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import ExecuteSqlResponse
from rhetorica_backend.domain.chat.conversation.session_state import (
    extract_runtime_context_from_history,
)
from rhetorica_backend.domain.chat.llm.prompt_builder import PromptBuilder
from rhetorica_backend.domain.chat.metadata import (
    MetadataCatalogSearchService,
    MetadataFetcher,
    MetadataRankingService,
    MetadataResolver,
    MetadataSemanticGroundingService,
    MetadataTableLoader,
    resolve_metadata_runtime_services,
)
from rhetorica_backend.domain.chat.orchestration.analysis_artifacts import (
    AnalysisCheckpoint,
    AnalysisPlan,
    AnalysisWorkerResult,
)
from rhetorica_backend.domain.chat.orchestration.capability_registry import (
    build_workspace_capability_surface,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.tool_execution_context_support import (
    ToolExecutionContextSupport,
)
from rhetorica_backend.domain.chat.orchestration.tool_executor import ToolExecutor
from rhetorica_backend.domain.chat.orchestration.turn_policy import (
    TurnPolicy,
    resolve_turn_policy,
)
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter
from rhetorica_backend.domain.rag.retrieval_service import RagInsightRetriever
from rhetorica_backend.domain.workspace_skills.runtime import (
    build_workspace_skill_prompt_guidance,
)
from rhetorica_backend.infra.postgres.confluence_connector_repository import (
    ConfluenceConnectorRepository,
)
from rhetorica_backend.infra.postgres.jira_connector_repository import (
    JiraConnectorRepository,
)
from rhetorica_backend.infra.postgres.openmetadata_connector_repository import (
    OpenMetadataConnectorRepository,
)
from rhetorica_backend.infra.postgres.workspace_skill_repository import (
    WorkspaceSkillRepository,
)


@dataclass(frozen=True)
class WorkspaceToolingSnapshot:
    """Available workspace tooling for orchestration planning."""

    has_db_connectors: bool
    metadata_configured: bool
    metadata_connector_names: list[str]
    rag_configured: bool
    confluence_configured: bool
    confluence_connector_names: list[str]
    jira_configured: bool = False
    jira_connector_names: list[str] = field(default_factory=list)


@dataclass
class MetadataBootstrapResult:
    """Pre-LLM metadata bootstrap result kept empty by design."""

    tables: list[Any] = field(default_factory=list)
    metadata_sources: set[str] = field(default_factory=set)
    semantic_grounding: dict[str, Any] | None = None


@dataclass
class PreparedTurn:
    """Prepared orchestration turn shared by sync and streaming flows."""

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    state: ToolOrchestrationState
    tool_executor: ToolExecutor
    protocol_repair_enabled: bool
    policy: TurnPolicy
    workspace_snapshot: WorkspaceToolingSnapshot
    bootstrap_result: MetadataBootstrapResult


class _NullOpenMetadataConnectorRepository:
    """Fallback repository that behaves like no metadata connectors exist."""

    def get_connection(self, *args: Any, **kwargs: Any) -> None:
        return None

    def list(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


class TurnBuilder:
    """Builds shared orchestration context for a single chat turn."""

    def __init__(
        self,
        *,
        openmetadata_connector_repository: OpenMetadataConnectorRepository | None,
        metadata_fetcher: MetadataFetcher | None,
        metadata_table_loader: MetadataTableLoader | None = None,
        metadata_catalog_search_service: MetadataCatalogSearchService | None = None,
        metadata_ranking_service: MetadataRankingService | None = None,
        metadata_semantic_grounding_service: MetadataSemanticGroundingService | None = None,
        sql_router: SqlRouter,
        rag_retriever: RagInsightRetriever | None = None,
        confluence_connector_repository: ConfluenceConnectorRepository | None = None,
        jira_connector_repository: JiraConnectorRepository | None = None,
        workspace_skill_repository: WorkspaceSkillRepository | None = None,
        tool_executor_factory: Callable[..., ToolExecutor] = ToolExecutor,
    ) -> None:
        self._openmetadata_connector_repository = openmetadata_connector_repository
        metadata_services = resolve_metadata_runtime_services(
            metadata_fetcher=metadata_fetcher,
            metadata_table_loader=metadata_table_loader,
            metadata_catalog_search_service=metadata_catalog_search_service,
            metadata_ranking_service=metadata_ranking_service,
            metadata_semantic_grounding_service=metadata_semantic_grounding_service,
        )
        self._metadata_table_loader = metadata_services.table_loader
        self._metadata_catalog_search_service = metadata_services.catalog_search_service
        self._metadata_ranking_service = metadata_services.ranking_service
        self._metadata_semantic_grounding_service = metadata_services.semantic_grounding_service
        self._sql_router = sql_router
        self._rag_retriever = rag_retriever
        self._confluence_connector_repository = confluence_connector_repository
        self._jira_connector_repository = jira_connector_repository
        self._workspace_skill_repository = workspace_skill_repository
        self._tool_executor_factory = tool_executor_factory

    def inspect_workspace(
        self,
        *,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
    ) -> WorkspaceToolingSnapshot:
        """Inspect configured connectors and knowledge sources for the workspace."""
        metadata_connector_names: list[str] = []
        if self._openmetadata_connector_repository is not None:
            metadata_connectors = self._openmetadata_connector_repository.list(
                workspace_id=request.workspace_id,
                limit=100,
            )
            metadata_connectors = [item for item in metadata_connectors if item.is_active]
            metadata_connector_names = [item.name for item in metadata_connectors]

        confluence_connector_names: list[str] = []
        if self._confluence_connector_repository is not None:
            confluence_connectors = self._confluence_connector_repository.list(
                workspace_id=request.workspace_id,
                limit=100,
            )
            confluence_connectors = [item for item in confluence_connectors if item.is_active]
            confluence_connector_names = [item.name for item in confluence_connectors]

        jira_connector_names: list[str] = []
        if self._jira_connector_repository is not None:
            jira_connectors = self._jira_connector_repository.list(
                workspace_id=request.workspace_id,
                limit=100,
            )
            jira_connectors = [item for item in jira_connectors if item.is_active]
            jira_connector_names = [item.name for item in jira_connectors]

        rag_configured = (
            self._rag_retriever.is_available(
                workspace_id=request.workspace_id,
                rag_connector_name=None,
            )
            if self._rag_retriever is not None
            else False
        )

        return WorkspaceToolingSnapshot(
            has_db_connectors=bool(db_connectors),
            metadata_configured=bool(metadata_connector_names),
            metadata_connector_names=metadata_connector_names,
            rag_configured=rag_configured,
            confluence_configured=bool(confluence_connector_names),
            confluence_connector_names=confluence_connector_names,
            jira_configured=bool(jira_connector_names),
            jira_connector_names=jira_connector_names,
        )

    def prepare_turn(
        self,
        *,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        conversation_history: list[dict[str, Any]] | None,
        conversation_state: dict[str, Any] | None = None,
        initial_warnings: list[str] | None = None,
        requires_metadata_lookup: bool = False,
        workspace_snapshot: WorkspaceToolingSnapshot | None = None,
        state_factory: Callable[[], ToolOrchestrationState] = ToolOrchestrationState,
    ) -> PreparedTurn:
        """Build prompt, tools, state, and tool executor for one orchestration turn."""
        workspace_snapshot = workspace_snapshot or self.inspect_workspace(
            request=request,
            db_connectors=db_connectors,
        )
        policy = resolve_turn_policy(
            request=request,
            has_db_connectors=workspace_snapshot.has_db_connectors,
            metadata_configured=workspace_snapshot.metadata_configured,
            rag_configured=workspace_snapshot.rag_configured,
            confluence_configured=workspace_snapshot.confluence_configured,
            jira_configured=workspace_snapshot.jira_configured,
            sql_router=self._sql_router,
            requires_metadata_lookup=requires_metadata_lookup,
        )
        capability_surface = build_workspace_capability_surface(
            has_db_connectors=workspace_snapshot.has_db_connectors,
            metadata_configured=workspace_snapshot.metadata_configured,
            rag_configured=workspace_snapshot.rag_configured,
            confluence_configured=workspace_snapshot.confluence_configured,
            jira_configured=workspace_snapshot.jira_configured,
            policy=policy,
            run_sql_requested=request.run_sql,
        )
        workspace_skill_guidance = self._build_workspace_skill_guidance(
            workspace_id=request.workspace_id,
            db_connectors=db_connectors,
            prompt_mode=policy.prompt_mode,
        )

        system_prompt = PromptBuilder.build_system_prompt(
            run_sql_allowed=policy.run_sql_tools_enabled,
            metadata_configured=policy.metadata_tools_enabled,
            has_db_connectors=workspace_snapshot.has_db_connectors,
            rag_configured=policy.rag_tools_enabled,
            confluence_configured=policy.confluence_tools_enabled,
            jira_read_configured=policy.jira_read_tools_enabled,
            jira_write_configured=policy.jira_write_tools_enabled,
            profile_tools_allowed=policy.profile_tools_enabled,
            prompt_mode=policy.prompt_mode,
            workspace_skill_guidance=workspace_skill_guidance,
        )

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        if conversation_history:
            messages.extend(conversation_history)
        ambient_context = self._normalize_ambient_context(request.ambient_context_markdown)
        if ambient_context is not None:
            messages.append({
                "role": "system",
                "content": (
                    "External chat context from Mattermost/Tag. "
                    "Use it only to understand the current user question; "
                    "do not quote it unless it is directly relevant.\n\n"
                    f"{ambient_context}"
                ),
            })

        runtime_context = extract_runtime_context_from_history(conversation_history)
        explicit_connector = self._resolve_explicit_database_connector(
            question=request.question,
            db_connectors=db_connectors,
        )
        if explicit_connector is not None:
            runtime_context.selected_connector = explicit_connector.name
            runtime_context.recent_tables = (
                ToolExecutionContextSupport._context_tables_for_connector_scope(
                    context_tables=runtime_context.recent_tables,
                    selected_connector=explicit_connector,
                )
            )
            runtime_context.metadata_table_columns = (
                ToolExecutionContextSupport._metadata_columns_for_connector_scope(
                    metadata_table_columns=runtime_context.metadata_table_columns,
                    selected_connector=explicit_connector,
                )
            )
        warnings = list(initial_warnings or [])
        metadata_resolver = (
            MetadataResolver(
                openmetadata_connector_repository=self._openmetadata_connector_repository
            )
            if self._openmetadata_connector_repository is not None
            else None
        )
        bootstrap_result = MetadataBootstrapResult()

        messages.append({
            "role": "user",
            "content": PromptBuilder.build_user_message(
                question=request.question,
                db_connectors=db_connectors,
                metadata_connector_names=workspace_snapshot.metadata_connector_names,
                rag_enabled=policy.rag_tools_enabled,
                confluence_connector_names=workspace_snapshot.confluence_connector_names,
                confluence_enabled=policy.confluence_tools_enabled,
                jira_connector_names=workspace_snapshot.jira_connector_names,
                jira_enabled=policy.jira_read_tools_enabled,
                jira_create_enabled=policy.jira_write_tools_enabled,
                profile_tools_allowed=policy.profile_tools_enabled,
                recent_tables=runtime_context.recent_tables,
                grounded_tables=bootstrap_result.tables,
                prompt_mode=policy.prompt_mode,
            ),
        })

        tools = PromptBuilder.build_tools(
            run_sql_allowed=policy.run_sql_tools_enabled,
            metadata_configured=policy.metadata_tools_enabled,
            has_db_connectors=workspace_snapshot.has_db_connectors,
            rag_configured=policy.rag_tools_enabled,
            confluence_configured=policy.confluence_tools_enabled,
            jira_read_configured=policy.jira_read_tools_enabled,
            jira_write_configured=policy.jira_write_tools_enabled,
            profile_tools_allowed=policy.profile_tools_enabled,
        )
        state = state_factory()
        state.prompt_version = PromptBuilder.get_prompt_version()
        state.capability_state.available_tools = list(capability_surface.available_tools)
        state.capability_state.degraded_capabilities = list(
            capability_surface.degraded_capabilities
        )
        state.capability_state.policy_decisions = list(capability_surface.policy_decisions)
        if workspace_skill_guidance:
            state.capability_state.used_providers.append("workspace_skill_prompt")
        state.context_tables = runtime_context.recent_tables[:20]
        state.metadata_table_columns = {
            table_name: set(columns)
            for table_name, columns in runtime_context.metadata_table_columns.items()
        }
        state.semantic_grounding = runtime_context.semantic_grounding
        state.selected_database_connector = runtime_context.selected_connector
        ToolExecutionContextSupport._sync_execution_zone(
            state=state,
            db_connectors=db_connectors,
        )
        self._extend_unique(state.warnings, warnings)
        self._initialize_analysis_runtime_state(
            state=state,
            conversation_state=conversation_state,
        )

        tool_executor = self._tool_executor_factory(
            metadata_resolver=metadata_resolver
            if metadata_resolver is not None
            else MetadataResolver(
                openmetadata_connector_repository=_NullOpenMetadataConnectorRepository()
            ),
            metadata_fetcher=None,
            metadata_table_loader=self._metadata_table_loader,
            metadata_catalog_search_service=self._metadata_catalog_search_service,
            metadata_ranking_service=self._metadata_ranking_service,
            metadata_semantic_grounding_service=self._metadata_semantic_grounding_service,
            sql_router=self._sql_router,
            rag_retriever=self._rag_retriever,
            confluence_connector_repository=self._confluence_connector_repository,
            jira_connector_repository=self._jira_connector_repository,
            workspace_skill_repository=self._workspace_skill_repository,
        )

        return PreparedTurn(
            messages=messages,
            tools=tools,
            state=state,
            tool_executor=tool_executor,
            protocol_repair_enabled=bool(tools),
            policy=policy,
            workspace_snapshot=workspace_snapshot,
            bootstrap_result=bootstrap_result,
        )

    @staticmethod
    def _initialize_analysis_runtime_state(
        *,
        state: ToolOrchestrationState,
        conversation_state: dict[str, Any] | None = None,
    ) -> None:
        if TurnBuilder._restore_active_analysis_runtime_state(
            state=state,
            conversation_state=conversation_state,
        ):
            return

    @staticmethod
    def _restore_active_analysis_runtime_state(
        *,
        state: ToolOrchestrationState,
        conversation_state: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(conversation_state, dict):
            return False
        raw_checkpoint = conversation_state.get("analysis_checkpoint")
        if not isinstance(raw_checkpoint, dict):
            return False
        try:
            checkpoint = AnalysisCheckpoint.model_validate(raw_checkpoint)
        except Exception:
            state.add_warning("analysis_checkpoint_restore_failed")
            return False
        if checkpoint.status not in {"planned", "running", "blocked", "awaiting_user", "failed"}:
            return False

        raw_plan = conversation_state.get("analysis_plan")
        plan: AnalysisPlan | None = None
        if isinstance(raw_plan, dict):
            try:
                plan = AnalysisPlan.model_validate(raw_plan)
            except Exception:
                state.add_warning("analysis_plan_restore_failed")
                return False
        if plan is None or (checkpoint.plan_id is not None and plan.plan_id != checkpoint.plan_id):
            return False

        state.analysis_plan = plan
        state.checkpoint = checkpoint
        raw_results = conversation_state.get("analysis_worker_results")
        if isinstance(raw_results, list):
            for item in raw_results[-5:]:
                if not isinstance(item, dict):
                    continue
                try:
                    result = AnalysisWorkerResult.model_validate(item)
                except Exception:
                    state.add_warning("analysis_worker_result_restore_failed")
                    continue
                state.worker_results.append(result)
                TurnBuilder._restore_worker_execution_outcome(
                    state=state,
                    result=result,
                )
        state.add_warning("analysis_checkpoint_restored")
        return True

    @staticmethod
    def _restore_worker_execution_outcome(
        *,
        state: ToolOrchestrationState,
        result: AnalysisWorkerResult,
    ) -> None:
        outcome = result.execution_outcome
        if not isinstance(outcome, dict):
            return
        execution_payload = outcome.get("execution")
        if not isinstance(execution_payload, dict):
            return
        try:
            execution = ExecuteSqlResponse.model_validate(execution_payload)
        except Exception:
            state.add_warning("analysis_worker_execution_restore_failed")
            return
        if execution.execution_id and any(
            attempt.execution.execution_id == execution.execution_id
            for attempt in state.execution_attempts
        ):
            return
        state.record_execution_outcome(
            sql=str(outcome.get("sql") or ""),
            connector_name=(
                outcome.get("connector_name")
                if isinstance(outcome.get("connector_name"), str)
                else None
            ),
            execution=execution,
            preflight_status=(
                outcome.get("preflight_status")
                if isinstance(outcome.get("preflight_status"), str)
                else None
            ),
        )

    @classmethod
    def _resolve_explicit_database_connector(
        cls,
        *,
        question: str,
        db_connectors: list[ConnectorItem],
    ) -> ConnectorItem | None:
        if not isinstance(question, str) or not question.strip() or not db_connectors:
            return None

        exact_matches = [
            connector
            for connector in db_connectors
            if cls._contains_explicit_reference(question=question, value=connector.name)
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            return None

        engine_matches: list[ConnectorItem] = []
        for engine_name in ("clickhouse", "trino", "greenplum", "impala"):
            if not cls._contains_explicit_reference(question=question, value=engine_name):
                continue
            connectors_for_engine = [
                connector
                for connector in db_connectors
                if str(getattr(connector.connection, "engine", "") or "").strip().lower()
                == engine_name
            ]
            if len(connectors_for_engine) != 1:
                return None
            engine_matches.append(connectors_for_engine[0])

        if len(engine_matches) == 1:
            return engine_matches[0]
        return None

    @staticmethod
    def _contains_explicit_reference(*, question: str, value: str) -> bool:
        clean_value = value.strip().lower()
        if not clean_value:
            return False
        pattern = rf"(?<![a-z0-9_]){re.escape(clean_value)}(?![a-z0-9_])"
        return re.search(pattern, question.lower()) is not None

    def _build_workspace_skill_guidance(
        self,
        *,
        workspace_id: str,
        db_connectors: list[ConnectorItem],
        prompt_mode: str,
    ) -> str | None:
        if (
            self._workspace_skill_repository is None
            or prompt_mode != "sql_generation"
            or not db_connectors
        ):
            return None

        skills_by_id: dict[str, Any] = {}
        engine_scopes = {
            str(item.connection.engine).strip().lower()
            for item in db_connectors
            if getattr(item, "connection", None) is not None
            and getattr(item.connection, "engine", None)
        }
        for engine_scope in sorted(engine_scopes):
            for skill in self._workspace_skill_repository.list_active_for_capability(
                workspace_id=workspace_id,
                capability_family="sql_optimization_advisor",
                engine_scope=engine_scope,
                limit=5,
            ):
                skills_by_id[skill.id] = skill
        return build_workspace_skill_prompt_guidance(list(skills_by_id.values()))

    @staticmethod
    def _normalize_ambient_context(value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _extend_unique(target: list[str], items: list[str]) -> None:
        for item in items:
            if item not in target:
                target.append(item)
