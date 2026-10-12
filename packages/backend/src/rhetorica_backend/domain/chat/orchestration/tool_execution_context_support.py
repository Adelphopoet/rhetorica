"""Compatibility facade over focused tool-execution context services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.chat import SemanticGrounding
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.metadata import TableContext
from rhetorica_backend.domain.chat.orchestration.tool_execution_connector_scope_policy import (
    ToolExecutionConnectorScopePolicy,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_context_hydration_helpers import (
    ToolExecutionContextHydrationHelpers,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_metadata_context_service import (
    ToolExecutionMetadataContextService,
)

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


class ToolExecutionContextSupport:
    """Legacy compatibility surface backed by explicit policy/services."""

    _connector_scope_policy = ToolExecutionConnectorScopePolicy()
    _metadata_context_service = ToolExecutionMetadataContextService(
        connector_scope_policy=_connector_scope_policy,
    )
    _context_hydration_helpers = ToolExecutionContextHydrationHelpers()

    @staticmethod
    def _bounded_int(
        value: Any,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        return ToolExecutionContextSupport._metadata_context_service.bounded_int(
            value,
            default=default,
            minimum=minimum,
            maximum=maximum,
        )

    @staticmethod
    def _metadata_source_names(
        metadata_connection: Any | None,
        tables: list[TableContext],
    ) -> list[str]:
        return ToolExecutionContextSupport._metadata_context_service.metadata_source_names(
            metadata_connection,
            tables,
        )

    @staticmethod
    def _connector_mapping_value(
        connector: ConnectorItem,
        key: str,
    ) -> str | None:
        return ToolExecutionContextSupport._connector_scope_policy.connector_mapping_value(
            connector,
            key,
        )

    @staticmethod
    def _normalize_leaf_table_name(value: str) -> str:
        return ToolExecutionContextSupport._metadata_context_service.normalize_leaf_table_name(
            value
        )

    @staticmethod
    def _update_table_column_state(
        state: ToolOrchestrationState,
        tables: list[TableContext],
    ) -> None:
        ToolExecutionContextSupport._metadata_context_service.update_table_column_state(
            state=state,
            tables=tables,
        )

    @staticmethod
    def _bump_metadata_context_revision(state: ToolOrchestrationState) -> None:
        ToolExecutionContextSupport._metadata_context_service.bump_metadata_context_revision(state)

    @staticmethod
    def _set_table_columns(
        *,
        state: ToolOrchestrationState,
        table_fqn: str,
        columns: set[str],
    ) -> None:
        ToolExecutionContextSupport._metadata_context_service.set_table_columns(
            state=state,
            table_fqn=table_fqn,
            columns=columns,
        )

    @staticmethod
    def _update_semantic_grounding(
        state: ToolOrchestrationState,
        tables: list[TableContext],
        *,
        chosen_table_fqn: str | None = None,
        lineage_summary: str | None = None,
        preserve_stronger_existing: bool = True,
    ) -> None:
        ToolExecutionContextSupport._metadata_context_service.update_semantic_grounding(
            state=state,
            tables=tables,
            chosen_table_fqn=chosen_table_fqn,
            lineage_summary=lineage_summary,
            preserve_stronger_existing=preserve_stronger_existing,
        )

    @staticmethod
    def _prefer_semantic_tables(
        *,
        current: list[TableContext],
        candidate: list[TableContext],
    ) -> list[TableContext]:
        return ToolExecutionContextSupport._metadata_context_service.prefer_semantic_tables(
            current=current,
            candidate=candidate,
        )

    @staticmethod
    def _semantic_grounding_priority(
        grounding: SemanticGrounding,
    ) -> tuple[int, int, int, int, int, float, int]:
        return ToolExecutionContextSupport._metadata_context_service.semantic_grounding_priority(
            grounding
        )

    @staticmethod
    def _selected_semantic_candidate(grounding: SemanticGrounding):
        return ToolExecutionContextSupport._metadata_context_service.selected_semantic_candidate(
            grounding
        )

    @staticmethod
    def _table_priority(
        table: TableContext,
    ) -> tuple[int, int, int, int, int, float, int]:
        return ToolExecutionContextSupport._metadata_context_service.table_priority(table)

    @staticmethod
    def _set_semantic_lineage_summary(
        state: ToolOrchestrationState,
        *,
        fqn: str,
        lineage_summary: str | None,
    ) -> None:
        ToolExecutionContextSupport._metadata_context_service.set_semantic_lineage_summary(
            state=state,
            fqn=fqn,
            lineage_summary=lineage_summary,
        )

    @classmethod
    def _sync_execution_zone(
        cls,
        *,
        state: ToolOrchestrationState,
        db_connectors: list[ConnectorItem],
    ) -> None:
        cls._metadata_context_service.sync_execution_zone(
            state=state,
            db_connectors=db_connectors,
        )

    @staticmethod
    def _merge_context_tables(
        *,
        state: ToolOrchestrationState,
        table_fqns: list[str | None],
    ) -> None:
        ToolExecutionContextSupport._metadata_context_service.merge_context_tables(
            state=state,
            table_fqns=table_fqns,
        )

    @classmethod
    def _filter_context_tables_by_available_connectors(
        cls,
        *,
        table_fqns: list[str | None],
        db_connectors: list[ConnectorItem],
    ) -> list[str]:
        return cls._connector_scope_policy.filter_context_tables_by_available_connectors(
            table_fqns=table_fqns,
            db_connectors=db_connectors,
        )

    @classmethod
    def _filter_table_contexts_by_available_connectors(
        cls,
        *,
        tables: list[TableContext],
        db_connectors: list[ConnectorItem],
    ) -> list[TableContext]:
        return cls._connector_scope_policy.filter_table_contexts_by_available_connectors(
            tables=tables,
            db_connectors=db_connectors,
        )

    @classmethod
    def _filter_catalog_results_by_available_connectors(
        cls,
        *,
        results: list[dict[str, Any]],
        db_connectors: list[ConnectorItem],
    ) -> list[dict[str, Any]]:
        return cls._connector_scope_policy.filter_catalog_results_by_available_connectors(
            results=results,
            db_connectors=db_connectors,
            table_context_from_entity_result=cls._table_context_from_entity_result,
            normalized_result_entity_type=cls._normalized_result_entity_type,
        )

    @classmethod
    def _table_context_is_available_for_connectors(
        cls,
        *,
        table: TableContext,
        db_connectors: list[ConnectorItem],
    ) -> bool:
        return cls._connector_scope_policy.table_context_is_available_for_connectors(
            table=table,
            db_connectors=db_connectors,
        )

    @classmethod
    def _fetch_context_tables(
        cls,
        *,
        search: Any,
        filtered: list[TableContext],
        db_connectors: list[ConnectorItem],
    ) -> list[str]:
        return cls._metadata_context_service.fetch_context_tables(
            search=search,
            filtered=filtered,
            db_connectors=db_connectors,
        )

    @staticmethod
    def _prioritize_context_tables(
        *,
        state: ToolOrchestrationState,
        preferred_table_fqns: list[str],
        max_items: int = 8,
    ) -> None:
        ToolExecutionContextSupport._metadata_context_service.prioritize_context_tables(
            state=state,
            preferred_table_fqns=preferred_table_fqns,
            max_items=max_items,
        )

    @classmethod
    def _strong_catalog_table_fqns(
        cls,
        results: list[dict[str, Any]],
        *,
        max_items: int = 5,
    ) -> list[str]:
        return cls._metadata_context_service.strong_catalog_table_fqns(
            results,
            max_items=max_items,
        )

    @staticmethod
    def _should_fallback_to_table_lookup(
        *,
        query: str,
        entity_type: Any,
        results: list[dict[str, Any]],
    ) -> bool:
        return (
            ToolExecutionContextSupport._metadata_context_service.should_fallback_to_table_lookup(
                query=query,
                entity_type=entity_type,
                results=results,
            )
        )

    @staticmethod
    def _table_context_to_catalog_result(table: TableContext) -> dict[str, Any]:
        return (
            ToolExecutionContextSupport._context_hydration_helpers.table_context_to_catalog_result(
                table
            )
        )

    @staticmethod
    def _normalized_result_entity_type(item: dict[str, Any]) -> str | None:
        return ToolExecutionContextSupport._context_hydration_helpers.normalized_result_entity_type(
            item
        )

    @classmethod
    def _should_fallback_fetch_to_catalog(
        cls,
        *,
        search: Any,
        connection: Any | None,
    ) -> bool:
        return cls._metadata_context_service.should_fallback_fetch_to_catalog(
            search=search,
            connection=connection,
        )

    @classmethod
    def _should_attempt_live_table_lookup(
        cls,
        *,
        search: Any,
        table_fqns: list[str],
    ) -> bool:
        return cls._metadata_context_service.should_attempt_live_table_lookup(
            search=search,
            table_fqns=table_fqns,
        )

    @staticmethod
    def _should_prefer_semantic_output(
        *,
        search: Any,
        entity_type: Any = "table",
    ) -> bool:
        return ToolExecutionContextSupport._metadata_context_service.should_prefer_semantic_output(
            search=search,
            entity_type=entity_type,
        )

    @classmethod
    def _metadata_lookup_limit(
        cls,
        *,
        search: Any,
        requested_limit: Any,
        default: int,
        maximum: int,
        entity_type: Any = "table",
    ) -> int:
        return cls._metadata_context_service.metadata_lookup_limit(
            search=search,
            requested_limit=requested_limit,
            default=default,
            maximum=maximum,
            entity_type=entity_type,
        )

    @classmethod
    def _table_context_from_entity_result(
        cls,
        result: dict[str, Any] | None,
    ) -> TableContext | None:
        return cls._context_hydration_helpers.table_context_from_entity_result(result)

    @classmethod
    def _table_context_from_catalog_item(
        cls,
        item: dict[str, Any] | None,
    ) -> TableContext | None:
        return cls._context_hydration_helpers.table_context_from_catalog_item(item)

    @classmethod
    def _summarize_lineage_payload(
        cls,
        payload: dict[str, Any] | None,
        *,
        fqn: str,
    ) -> str | None:
        return cls._context_hydration_helpers.summarize_lineage_payload(
            payload,
            fqn=fqn,
        )

    @staticmethod
    def _lineage_entity_count(value: Any) -> int:
        return ToolExecutionContextSupport._context_hydration_helpers.lineage_entity_count(value)

    @classmethod
    def _context_tables_for_connector_scope(
        cls,
        *,
        context_tables: list[str],
        selected_connector: ConnectorItem,
    ) -> list[str]:
        return cls._connector_scope_policy.context_tables_for_connector_scope(
            context_tables=context_tables,
            selected_connector=selected_connector,
        )

    @classmethod
    def _metadata_columns_for_connector_scope(
        cls,
        *,
        metadata_table_columns: dict[str, set[str]],
        selected_connector: ConnectorItem,
    ) -> dict[str, set[str]]:
        return cls._connector_scope_policy.metadata_columns_for_connector_scope(
            metadata_table_columns=metadata_table_columns,
            selected_connector=selected_connector,
        )

    @classmethod
    def _table_fqn_matches_connector_scope(
        cls,
        *,
        table_fqn: str,
        selected_connector: ConnectorItem,
    ) -> bool:
        return cls._connector_scope_policy.table_fqn_matches_connector_scope(
            table_fqn=table_fqn,
            selected_connector=selected_connector,
        )

    @classmethod
    def _find_connector_by_mapping(
        cls,
        *,
        db_connectors: list[ConnectorItem],
        service: str | None,
        database: str | None,
        schema: str | None,
    ) -> ConnectorItem | None:
        return cls._connector_scope_policy.find_connector_by_mapping(
            db_connectors=db_connectors,
            service=service,
            database=database,
            schema=schema,
        )

    @classmethod
    def _find_connector_by_name(
        cls,
        *,
        db_connectors: list[ConnectorItem],
        connector_name: str,
    ) -> ConnectorItem | None:
        return cls._connector_scope_policy.find_connector_by_name(
            db_connectors=db_connectors,
            connector_name=connector_name,
        )

    @staticmethod
    def _table_mapping_from_fqn(table_fqn: str) -> dict[str, str] | None:
        return ToolExecutionContextSupport._connector_scope_policy.table_mapping_from_fqn(table_fqn)

    @classmethod
    def _execution_zone_from_selected_connector(
        cls,
        *,
        state: ToolOrchestrationState,
        db_connectors: list[ConnectorItem],
    ):
        return cls._connector_scope_policy.execution_zone_from_selected_connector(
            state=state,
            db_connectors=db_connectors,
        )

    @classmethod
    def _context_execution_zones(
        cls,
        *,
        context_tables: list[str],
        db_connectors: list[ConnectorItem],
    ):
        return cls._connector_scope_policy.context_execution_zones(
            context_tables=context_tables,
            db_connectors=db_connectors,
        )

    @classmethod
    def _mapping_match_score(
        cls,
        *,
        settings: Any,
        service: str | None,
        database: str | None,
        schema: str | None,
    ) -> tuple[int, int, int] | None:
        return cls._connector_scope_policy.mapping_match_score(
            settings=settings,
            service=service,
            database=database,
            schema=schema,
        )

    @staticmethod
    def _mapping_component_score(
        *,
        mapping_value: Any,
        expected: str | None,
        allow_unset_wildcard: bool,
    ) -> int | None:
        return ToolExecutionContextSupport._connector_scope_policy.mapping_component_score(
            mapping_value=mapping_value,
            expected=expected,
            allow_unset_wildcard=allow_unset_wildcard,
        )

    @classmethod
    def _unwrap_entity_payload(
        cls,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        return cls._context_hydration_helpers.unwrap_entity_payload(payload)

    @staticmethod
    def _nested_or_string_name(value: Any) -> str | None:
        return ToolExecutionContextSupport._context_hydration_helpers.nested_or_string_name(value)

    @classmethod
    def _list_of_strings(cls, value: Any) -> list[str]:
        return cls._context_hydration_helpers.list_of_strings(value)

    @classmethod
    def _first_matching_prefix(cls, value: Any, prefix: str) -> str | None:
        return cls._context_hydration_helpers.first_matching_prefix(value, prefix)

    @staticmethod
    def _coerce_int(value: Any) -> int:
        return ToolExecutionContextSupport._context_hydration_helpers.coerce_int(value)
