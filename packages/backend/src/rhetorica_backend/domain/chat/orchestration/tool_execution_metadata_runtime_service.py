"""Runtime database schema lookup for guarded SQL execution."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.metadata import TableContext
from rhetorica_backend.domain.chat.orchestration.state import SchemaSnapshotSource
from rhetorica_backend.domain.chat.orchestration.tool_execution_metadata_context_service import (
    ToolExecutionMetadataContextService,
)
from rhetorica_backend.domain.metadata.metric_registry_service import MetricRegistryService

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


logger = logging.getLogger(__name__)


class ToolExecutionMetadataRuntimeService:
    """Repairs missing SQL context by inspecting the selected database directly."""

    def __init__(
        self,
        *,
        metadata_context_service: ToolExecutionMetadataContextService,
        lookup_runtime_tables: Callable[..., list[TableContext]],
        metric_registry_service: MetricRegistryService | None = None,
        **_: Any,
    ) -> None:
        self._metadata_context_service = metadata_context_service
        self._lookup_runtime_tables = lookup_runtime_tables
        self._metric_registry_service = metric_registry_service

    def attempt_metadata_context_repair(
        self,
        *,
        request: ChatAskRequest,
        state: ToolOrchestrationState,
        selected_connector: ConnectorItem,
        connection: Any,
        missing_tables: list[str],
    ) -> bool:
        del connection
        if not missing_tables:
            return False

        repaired_tables: dict[str, TableContext] = {}
        for missing_table in missing_tables:
            leaf_name = self._metadata_context_service.normalize_leaf_table_name(missing_table)
            if not leaf_name:
                continue
            try:
                runtime_tables = self._lookup_runtime_tables(
                    request=request,
                    db_connectors=[selected_connector],
                    search=missing_table,
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
            self._apply_repaired_table_schema_snapshot(state=state, table=table)
        self._metadata_context_service.sync_execution_zone(
            state=state,
            db_connectors=[selected_connector],
        )
        state.metadata_sources.update(
            self._metadata_context_service.metadata_source_names(None, repaired_list)
        )
        if state.selected_database_connector is None:
            state.selected_database_connector = selected_connector.name
        return True

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
        if table.source.strip().lower() == "runtime":
            return "runtime_lookup", True
        return None

    def get_metric_definitions_for_context(
        self,
        *,
        workspace_id: str,
        question: str | None = None,
    ) -> list[dict[str, Any]]:
        if self._metric_registry_service is None:
            return []
        if question:
            metrics = self._metric_registry_service.find_by_business_term(
                term=question,
                workspace_id=workspace_id,
            )
        else:
            metrics = self._metric_registry_service.list_all(workspace_id=workspace_id)
        return [self._metric_registry_service.get_compact_card(metric) for metric in metrics]
