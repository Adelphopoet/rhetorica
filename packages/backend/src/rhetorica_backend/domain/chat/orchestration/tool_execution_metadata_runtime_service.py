"""Runtime metadata resolution and context-repair service."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.error_contract import retry_idempotent
from rhetorica_backend.domain.chat.metadata import (
    MetadataCatalogSearchService,
    MetadataRankingService,
    MetadataResolver,
    MetadataTableLoader,
    TableContext,
)
from rhetorica_backend.domain.chat.orchestration.state import SchemaSnapshotSource
from rhetorica_backend.domain.chat.orchestration.tool_execution_connector_scope_policy import (
    ToolExecutionConnectorScopePolicy,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_context_hydration_helpers import (
    ToolExecutionContextHydrationHelpers,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_metadata_context_service import (
    ToolExecutionMetadataContextService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_runtime_sql_policy import (
    ToolExecutionRuntimeSqlPolicy,
)
from rhetorica_backend.domain.metadata.metric_registry_service import (
    MetricRegistryService,
)

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


logger = logging.getLogger(__name__)


class ToolExecutionMetadataRuntimeService:
    """Owns metadata connection resolution and missing-context repair flow."""

    def __init__(
        self,
        *,
        metadata_resolver: MetadataResolver | None,
        metadata_table_loader: MetadataTableLoader | None,
        metadata_catalog_search_service: MetadataCatalogSearchService | None,
        metadata_ranking_service: MetadataRankingService | None,
        connector_scope_policy: ToolExecutionConnectorScopePolicy,
        metadata_context_service: ToolExecutionMetadataContextService,
        runtime_sql_policy: ToolExecutionRuntimeSqlPolicy,
        context_hydration_helpers: ToolExecutionContextHydrationHelpers,
        lookup_runtime_tables: Callable[..., list[TableContext]],
        metric_registry_service: MetricRegistryService | None = None,
    ) -> None:
        self._metadata_resolver = metadata_resolver
        self._metadata_table_loader = metadata_table_loader
        self._metadata_catalog_search_service = metadata_catalog_search_service
        self._metadata_ranking_service = metadata_ranking_service
        self._connector_scope_policy = connector_scope_policy
        self._metadata_context_service = metadata_context_service
        self._runtime_sql_policy = runtime_sql_policy
        self._context_hydration_helpers = context_hydration_helpers
        self._lookup_runtime_tables = lookup_runtime_tables
        self._metric_registry_service = metric_registry_service

    def resolve_metadata_connection(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        warnings: list[str],
        db_connectors: list[ConnectorItem],
    ) -> Any | None:
        if self._metadata_resolver is None:
            return None
        connector_name = self._runtime_sql_policy.metadata_tool_connector_name(
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
        )
        return self._metadata_resolver.resolve_connection(
            workspace_id=request.workspace_id,
            requested_name=connector_name,
            fallback_name=request.metadata_connector_name,
            warnings=warnings,
        )

    def resolve_effective_metadata_connection(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        warnings: list[str],
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState | None = None,
    ) -> Any | None:
        raw_connection = self.resolve_metadata_connection(
            request=request,
            arguments=arguments,
            warnings=warnings,
            db_connectors=db_connectors,
        )
        if raw_connection is None:
            return None
        return self._runtime_sql_policy.effective_metadata_connection(
            metadata_connection=raw_connection,
            request=request,
            arguments=arguments,
            db_connectors=db_connectors,
            state=state,
        )

    def attempt_metadata_context_repair(
        self,
        *,
        request: ChatAskRequest,
        state: ToolOrchestrationState,
        selected_connector: ConnectorItem,
        connection: Any,
        missing_tables: list[str],
    ) -> bool:
        if not missing_tables:
            return False

        metadata_connection = None
        if self._metadata_resolver is not None:
            metadata_connection = self.resolve_effective_metadata_connection(
                request=request,
                arguments={},
                warnings=state.warnings,
                db_connectors=[selected_connector],
                state=state,
            )

        tables: list[TableContext] = []
        metadata_warnings: list[str] = []
        if (
            metadata_connection is not None
            and self._metadata_table_loader is not None
            and self._metadata_ranking_service is not None
        ):
            try:
                tables, metadata_warnings = retry_idempotent(
                    lambda: self._metadata_table_loader.fetch_tables(
                        connection=metadata_connection,
                        workspace_id=request.workspace_id,
                        connector_name=request.metadata_connector_name,
                    ),
                    max_attempts=3,
                )
            except Exception:
                tables = []
                metadata_warnings = []

        state.warnings.extend(metadata_warnings)
        preferred_service = self._runtime_sql_policy.metadata_service_name(connection)
        preferred_database = self._runtime_sql_policy.metadata_mapping_value(
            connection,
            "metadata_database",
        )
        preferred_schema = self._runtime_sql_policy.metadata_mapping_value(
            connection,
            "metadata_schema",
        )

        repaired_tables: dict[str, TableContext] = {}
        repair_targets: dict[str, str] = {}
        for missing_table in missing_tables:
            leaf_name = self._metadata_context_service.normalize_leaf_table_name(
                missing_table
            )
            if not leaf_name:
                continue
            repair_targets.setdefault(leaf_name, missing_table)
            if self._metadata_ranking_service is None:
                continue
            ranked = self._metadata_ranking_service.filter_tables(
                tables=tables,
                search=leaf_name,
                service=None,
                database=None,
                schema=None,
                workspace_id=request.workspace_id,
                connector_name=request.metadata_connector_name,
                top_n=5,
                preferred_service=preferred_service,
                preferred_database=preferred_database,
                preferred_schema=preferred_schema,
            )
            ranked = self._connector_scope_policy.filter_table_contexts_by_available_connectors(
                tables=ranked,
                db_connectors=[selected_connector],
            )
            for table in ranked:
                if table.fqn.lower().split(".")[-1] != leaf_name:
                    continue
                self._store_repaired_table(
                    repaired_tables=repaired_tables,
                    candidate=table,
                )

        unresolved_leaf_names = [
            leaf_name
            for leaf_name in repair_targets
            if not any(
                table.fqn.lower().split(".")[-1] == leaf_name
                and self._repair_adds_columns(state=state, table=table)
                for table in repaired_tables.values()
            )
        ]
        if (
            unresolved_leaf_names
            and metadata_connection is not None
            and self._metadata_catalog_search_service is not None
            and getattr(metadata_connection, "mcp_tables_url", None)
        ):
            for leaf_name in unresolved_leaf_names:
                try:
                    catalog_results, catalog_warnings = retry_idempotent(
                        lambda leaf_name=leaf_name: self._metadata_catalog_search_service.search_catalog(
                            connection=metadata_connection,
                            query=repair_targets[leaf_name],
                            entity_type="table",
                            limit=5,
                            offset=0,
                        ),
                        max_attempts=3,
                    )
                except Exception:
                    logger.debug(
                        "Metadata catalog repair lookup failed during SQL preflight recovery",
                        exc_info=True,
                    )
                    continue
                state.warnings.extend(catalog_warnings)
                for item in catalog_results:
                    table = self._context_hydration_helpers.table_context_from_entity_result(
                        item
                    )
                    if table is None:
                        continue
                    if not self._connector_scope_policy.table_context_is_available_for_connectors(
                        table=table,
                        db_connectors=[selected_connector],
                    ):
                        continue
                    if table.fqn.lower().split(".")[-1] != leaf_name:
                        continue
                    self._store_repaired_table(
                        repaired_tables=repaired_tables,
                        candidate=table,
                    )

        unresolved_leaf_names = [
            leaf_name
            for leaf_name in repair_targets
            if not any(
                table.fqn.lower().split(".")[-1] == leaf_name
                and self._repair_adds_columns(state=state, table=table)
                for table in repaired_tables.values()
            )
        ]
        for leaf_name in unresolved_leaf_names:
            try:
                runtime_tables = self._lookup_runtime_tables(
                    request=request,
                    db_connectors=[selected_connector],
                    metadata_connection=metadata_connection,
                    search=repair_targets[leaf_name],
                    limit=5,
                )
            except Exception:
                logger.debug(
                    "Runtime table lookup failed during SQL preflight recovery",
                    exc_info=True,
                )
                continue
            for table in runtime_tables:
                if table.fqn.lower().split(".")[-1] != leaf_name:
                    continue
                self._store_repaired_table(
                    repaired_tables=repaired_tables,
                    candidate=table,
                )

        if not repaired_tables:
            return False

        repaired_list = list(repaired_tables.values())
        self._metadata_context_service.merge_context_tables(
            state=state,
            table_fqns=[table.fqn for table in repaired_list],
        )
        self._metadata_context_service.update_table_column_state(
            state=state,
            tables=repaired_list,
        )
        for table in repaired_list:
            self._apply_repaired_table_schema_snapshot(
                state=state,
                table=table,
            )
        self._metadata_context_service.sync_execution_zone(
            state=state,
            db_connectors=[selected_connector],
        )
        state.metadata_sources.update(
            self._metadata_context_service.metadata_source_names(
                metadata_connection,
                repaired_list,
            )
        )
        if state.selected_database_connector is None:
            state.selected_database_connector = selected_connector.name
        return True

    @staticmethod
    def _repair_adds_columns(
        *,
        state: ToolOrchestrationState,
        table: TableContext,
    ) -> bool:
        candidate_columns = {
            column.strip().lower()
            for column in table.columns
            if isinstance(column, str) and column.strip()
        }
        if not candidate_columns:
            return False
        existing_columns = state.metadata_table_columns.get(table.fqn.strip().lower())
        if not existing_columns:
            return True
        return not candidate_columns.issubset(existing_columns)

    @staticmethod
    def _store_repaired_table(
        *,
        repaired_tables: dict[str, TableContext],
        candidate: TableContext,
    ) -> None:
        existing = repaired_tables.get(candidate.fqn)
        if existing is None or len(candidate.columns) > len(existing.columns):
            repaired_tables[candidate.fqn] = candidate

    def _apply_repaired_table_schema_snapshot(
        self,
        *,
        state: ToolOrchestrationState,
        table: TableContext,
    ) -> None:
        columns = {
            column.strip().lower()
            for column in table.columns
            if isinstance(column, str) and column.strip()
        }
        if not columns:
            return
        snapshot = self._snapshot_descriptor_for_table(table)
        if snapshot is None:
            return
        source, complete = snapshot
        self._metadata_context_service.set_table_schema_snapshot(
            state=state,
            table_fqn=table.fqn,
            columns=columns,
            complete=complete,
            source=source,
            column_types=table.column_types,
            column_descriptions=table.column_descriptions,
        )

    @staticmethod
    def _snapshot_descriptor_for_table(
        table: TableContext,
    ) -> tuple[SchemaSnapshotSource, bool] | None:
        normalized_source = table.source.strip().lower()
        if normalized_source == "runtime":
            return "runtime_lookup", True
        if normalized_source == "openmetadata":
            return "metadata_loader", True
        if normalized_source == "mcp":
            return "catalog_search", False
        return None

    def get_metric_definitions_for_context(
        self,
        *,
        workspace_id: str,
        question: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get governed metric definitions for injection into metadata context.

        Discovers metrics from business terms in the question and returns compact
        metric cards suitable for LLM prompt injection.

        Args:
            workspace_id: The workspace identifier.
            question: Optional question text for metric discovery.

        Returns:
            List of compact metric card dictionaries for prompt injection.
        """
        if self._metric_registry_service is None:
            return []

        if question:
            metrics = self._metric_registry_service.find_by_business_term(
                term=question,
                workspace_id=workspace_id,
            )
        else:
            metrics = self._metric_registry_service.list_all(
                workspace_id=workspace_id,
            )

        return [
            self._metric_registry_service.get_compact_card(metric)
            for metric in metrics
        ]
