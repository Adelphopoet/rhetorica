"""Composable support kernel for tool execution helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.metadata import TableContext
from rhetorica_backend.domain.chat.orchestration.metadata_runtime_lookup_service import (
    MetadataRuntimeLookupService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_metadata_runtime_service import (
    ToolExecutionMetadataRuntimeService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_runtime_support import (
    ToolExecutionRuntimeSupport,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_sql_schema_hydration_service import (
    ToolExecutionSqlSchemaHydrationService,
)
from rhetorica_backend.domain.chat.sql.optimization import SqlOptimizationService
from rhetorica_backend.domain.chat.sql.semantic_preflight import (
    SemanticSqlPreflightService,
)
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter
from rhetorica_backend.domain.rag.retrieval_service import RagInsightRetriever


class ToolExecutionSupportKernel(ToolExecutionRuntimeSupport):
    """Composition root for focused tool-execution collaborators."""

    def __init__(
        self,
        *,
        sql_router: SqlRouter,
        rag_retriever: RagInsightRetriever | None = None,
        semantic_preflight_service: SemanticSqlPreflightService | None = None,
        sql_optimization_service: SqlOptimizationService | None = None,
    ) -> None:
        self._sql_router = sql_router
        self._rag_retriever = rag_retriever
        self._semantic_preflight_service = (
            semantic_preflight_service or SemanticSqlPreflightService()
        )
        self._sql_optimization_service = sql_optimization_service
        self._metadata_state_context_service = self._metadata_context_service
        self._metadata_runtime_service = ToolExecutionMetadataRuntimeService(
            connector_scope_policy=self._connector_scope_policy,
            metadata_context_service=self._metadata_state_context_service,
            runtime_sql_policy=self._runtime_sql_policy,
            context_hydration_helpers=self._context_hydration_helpers,
            lookup_runtime_tables=lambda **kwargs: self._lookup_runtime_tables(**kwargs),
        )
        self._metadata_runtime_lookup_service = MetadataRuntimeLookupService(
            runtime_sql_policy=self._runtime_sql_policy,
            sql_router=self._sql_router,
        )
        self._sql_schema_hydration_service = ToolExecutionSqlSchemaHydrationService(
            semantic_preflight_service=self._semantic_preflight_service,
            metadata_runtime_service=self._metadata_runtime_service,
            metadata_context_service=self._metadata_state_context_service,
            runtime_sql_policy=self._runtime_sql_policy,
        )
        self._sql_optimizer_start: Callable[..., None] | None = None
        self._sql_optimizer_end: Callable[..., None] | None = None

    @property
    def metadata_runtime_lookup_service(self) -> MetadataRuntimeLookupService:
        return self._metadata_runtime_lookup_service

    @property
    def semantic_preflight_service(self) -> SemanticSqlPreflightService:
        return self._semantic_preflight_service

    @property
    def sql_optimization_service(self) -> SqlOptimizationService | None:
        return self._sql_optimization_service

    def bind_sql_optimizer_hooks(
        self,
        *,
        on_start: Callable[..., None],
        on_end: Callable[..., None],
    ) -> None:
        self._sql_optimizer_start = on_start
        self._sql_optimizer_end = on_end

    def _lookup_runtime_tables(
        self,
        *,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        search: str,
        limit: int,
    ) -> list[TableContext]:
        return self._metadata_runtime_lookup_service.lookup_tables(
            request=request,
            db_connectors=db_connectors,
            search=search,
            limit=limit,
        )

    def _emit_sql_optimizer_start(self, **kwargs: Any) -> None:
        if self._sql_optimizer_start is None:
            return
        self._sql_optimizer_start(**kwargs)

    def _emit_sql_optimizer_end(self, **kwargs: Any) -> None:
        if self._sql_optimizer_end is None:
            return
        self._sql_optimizer_end(**kwargs)

    @staticmethod
    def _successful_usage_table_fqns(
        *,
        referenced_tables: list[str],
        state: Any,
    ) -> list[str]:
        selected: list[str] = []
        semantic_grounding = getattr(state, "semantic_grounding", None)
        chosen_table = getattr(semantic_grounding, "chosen_table_fqn", None)
        if isinstance(chosen_table, str) and chosen_table.strip():
            selected.append(chosen_table.strip())

        context_tables = [
            table_fqn
            for table_fqn in getattr(state, "context_tables", [])
            if isinstance(table_fqn, str) and table_fqn.strip()
        ]
        referenced_keys = {
            key
            for table in referenced_tables
            for key in ToolExecutionSupportKernel._table_match_keys(table)
        }
        for table_fqn in context_tables:
            table_keys = ToolExecutionSupportKernel._table_match_keys(table_fqn)
            if referenced_keys and referenced_keys.isdisjoint(table_keys):
                continue
            selected.append(table_fqn.strip())
        return ToolExecutionSupportKernel._dedupe_table_fqns(selected)

    @staticmethod
    def _table_match_keys(table_fqn: str) -> set[str]:
        value = table_fqn.strip().lower()
        if not value:
            return set()
        parts = [part for part in value.split(".") if part]
        keys = {value}
        if parts:
            keys.add(parts[-1])
        if len(parts) >= 2:
            keys.add(".".join(parts[-2:]))
        if len(parts) >= 3:
            keys.add(".".join(parts[-3:]))
        return keys

    @staticmethod
    def _dedupe_table_fqns(table_fqns: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for table_fqn in table_fqns:
            key = table_fqn.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(table_fqn)
        return deduped
