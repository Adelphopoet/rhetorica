"""Metadata context mutation service for tool execution."""

from __future__ import annotations

import re
from typing import Any

from rhetorica_backend.contracts.chat import SemanticGrounding
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.metadata import (
    MetadataSemanticGroundingService,
    TableContext,
)
from rhetorica_backend.domain.chat.orchestration.state import (
    ExecutionZone,
    SchemaSnapshotSource,
    TableSchemaSnapshot,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_connector_scope_policy import (
    ToolExecutionConnectorScopePolicy,
)


class ToolExecutionMetadataContextService:
    """Owns state mutation for metadata context and semantic grounding."""

    def __init__(
        self,
        *,
        connector_scope_policy: ToolExecutionConnectorScopePolicy,
    ) -> None:
        self._connector_scope_policy = connector_scope_policy

    @staticmethod
    def bounded_int(
        value: Any,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, int | float):
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return default
            return max(minimum, min(maximum, parsed))
        return default

    @staticmethod
    def metadata_source_names(
        metadata_connection: Any | None,
        tables: list[TableContext],
    ) -> list[str]:
        del metadata_connection
        return sorted({table.source for table in tables if table.source})

    @staticmethod
    def normalize_leaf_table_name(value: str) -> str:
        return value.strip().replace("`", "").replace('"', "").split(".")[-1].lower()

    def update_table_column_state(
        self,
        *,
        state: Any,
        tables: list[TableContext],
    ) -> None:
        for table in tables:
            normalized_columns = {
                column.strip().lower()
                for column in table.columns
                if isinstance(column, str) and column.strip()
            }
            if not normalized_columns:
                continue
            self.set_table_columns(
                state=state,
                table_fqn=table.fqn,
                columns=normalized_columns,
            )

    @staticmethod
    def table_schema_snapshot(
        *,
        state: Any,
        table_fqn: str,
    ) -> TableSchemaSnapshot | None:
        normalized_table = table_fqn.strip().lower()
        if not normalized_table:
            return None
        snapshot = state.metadata_table_schemas.get(normalized_table)
        if isinstance(snapshot, TableSchemaSnapshot):
            return snapshot
        return None

    @classmethod
    def has_complete_table_schema(
        cls,
        *,
        state: Any,
        table_fqn: str,
    ) -> bool:
        snapshot = cls.table_schema_snapshot(state=state, table_fqn=table_fqn)
        return bool(snapshot and snapshot.complete)

    @staticmethod
    def bump_metadata_context_revision(state: Any) -> None:
        state.metadata_context_revision += 1

    def set_table_columns(
        self,
        *,
        state: Any,
        table_fqn: str,
        columns: set[str],
    ) -> None:
        normalized_table = table_fqn.strip().lower()
        normalized_columns = {
            column.strip().lower()
            for column in columns
            if isinstance(column, str) and column.strip()
        }
        if not normalized_table or not normalized_columns:
            return
        existing = state.metadata_table_columns.get(normalized_table)
        merged_columns = (
            set(existing).union(normalized_columns)
            if isinstance(existing, set)
            else set(normalized_columns)
        )
        if existing == merged_columns:
            return
        state.metadata_table_columns[normalized_table] = merged_columns
        self.bump_metadata_context_revision(state)

    def set_table_schema_snapshot(
        self,
        *,
        state: Any,
        table_fqn: str,
        columns: set[str],
        complete: bool,
        source: SchemaSnapshotSource,
        column_types: dict[str, str] | None = None,
        column_descriptions: dict[str, str] | None = None,
    ) -> None:
        normalized_table = table_fqn.strip().lower()
        normalized_columns = {
            column.strip().lower()
            for column in columns
            if isinstance(column, str) and column.strip()
        }
        if not normalized_table or not normalized_columns:
            return

        snapshot = TableSchemaSnapshot(
            columns=frozenset(normalized_columns),
            complete=complete,
            source=source,
            column_types={
                key.strip().lower(): value
                for key, value in (column_types or {}).items()
                if isinstance(key, str) and key.strip()
            },
            column_descriptions={
                key.strip().lower(): value
                for key, value in (column_descriptions or {}).items()
                if isinstance(key, str) and key.strip()
            },
        )
        current = self.table_schema_snapshot(state=state, table_fqn=table_fqn)
        if current is not None:
            current_priority = (
                int(current.complete),
                len(current.columns),
                len(current.column_types),
                len(current.column_descriptions),
            )
            new_priority = (
                int(snapshot.complete),
                len(snapshot.columns),
                len(snapshot.column_types),
                len(snapshot.column_descriptions),
            )
            if current_priority > new_priority:
                self.set_table_columns(
                    state=state,
                    table_fqn=table_fqn,
                    columns=set(current.columns),
                )
                return
            if current == snapshot and state.metadata_table_columns.get(normalized_table) == set(
                snapshot.columns
            ):
                return

        state.metadata_table_schemas[normalized_table] = snapshot
        existing_columns = state.metadata_table_columns.get(normalized_table)
        if existing_columns != set(snapshot.columns):
            state.metadata_table_columns[normalized_table] = set(snapshot.columns)
            self.bump_metadata_context_revision(state)
            return
        self.bump_metadata_context_revision(state)

    def update_semantic_grounding(
        self,
        *,
        state: Any,
        tables: list[TableContext],
        chosen_table_fqn: str | None = None,
        lineage_summary: str | None = None,
        preserve_stronger_existing: bool = True,
    ) -> None:
        payload = MetadataSemanticGroundingService.build_semantic_grounding_payload(
            tables=tables,
            chosen_table_fqn=chosen_table_fqn,
            lineage_summary=(
                lineage_summary
                or (
                    state.semantic_grounding.lineage_summary
                    if state.semantic_grounding is not None
                    else None
                )
            ),
        )
        if payload is None:
            return
        incoming_grounding = SemanticGrounding.model_validate(payload)
        if (
            preserve_stronger_existing
            and state.semantic_grounding is not None
            and self.semantic_grounding_priority(state.semantic_grounding)
            > self.semantic_grounding_priority(incoming_grounding)
        ):
            return
        if state.semantic_grounding == incoming_grounding:
            return
        state.semantic_grounding = incoming_grounding
        self.bump_metadata_context_revision(state)

    def prefer_semantic_tables(
        self,
        *,
        current: list[TableContext],
        candidate: list[TableContext],
    ) -> list[TableContext]:
        if not candidate:
            return current
        if not current:
            return candidate
        if self.table_priority(candidate[0]) >= self.table_priority(current[0]):
            return candidate
        return current

    def semantic_grounding_priority(
        self,
        grounding: SemanticGrounding,
    ) -> tuple[int, int, int, int, int, float, int]:
        candidate = self.selected_semantic_candidate(grounding)
        if candidate is None:
            return (0, 0, 0, 0, 0, float("-inf"), 0)
        governance = int(
            "governance_strength" in candidate.reason_codes
            or bool(candidate.certification)
            or bool(candidate.tier)
            or bool(candidate.owners)
            or bool(candidate.owner_teams)
            or bool(candidate.domains)
            or bool(candidate.glossary_terms)
            or bool(candidate.related_metrics)
            or candidate.usage_count > 0
        )
        business_layer = int(
            "business_layer_preference" in candidate.reason_codes
            or (candidate.schema_name or "").strip().lower() in {"dm", "mart", "datamart"}
        )
        preferred_scope = int("preferred_connector_map_match" in candidate.reason_codes)
        return (
            int(candidate.relevance_score > 0),
            int(not candidate.suspicious),
            governance,
            business_layer,
            preferred_scope,
            float(candidate.relevance_score),
            int(candidate.usage_count),
        )

    @staticmethod
    def selected_semantic_candidate(grounding: SemanticGrounding):
        if grounding.chosen_table_fqn:
            for candidate in grounding.candidates:
                if candidate.fqn == grounding.chosen_table_fqn:
                    return candidate
        if grounding.candidates:
            return grounding.candidates[0]
        return None

    @staticmethod
    def table_priority(
        table: TableContext,
    ) -> tuple[int, int, int, int, int, float, int]:
        reason_codes = set(MetadataSemanticGroundingService.semantic_reason_codes(table))
        governance = int(
            "governance_strength" in reason_codes
            or bool(table.certification)
            or bool(table.tier)
            or bool(table.owners)
            or bool(table.owner_teams)
            or bool(table.domains)
            or bool(table.glossary_terms)
            or bool(table.related_metrics)
            or table.usage_count > 0
            or bool(table.description)
        )
        business_layer = int(
            "business_layer_preference" in reason_codes
            or (table.schema or "").strip().lower() in {"dm", "mart", "datamart"}
        )
        preferred_scope = int("preferred_connector_map_match" in reason_codes)
        suspicious = MetadataSemanticGroundingService.is_suspicious_table(table)
        return (
            int(table.relevance_score > 0),
            int(not suspicious),
            governance,
            business_layer,
            preferred_scope,
            float(table.relevance_score),
            int(table.usage_count),
        )

    def set_semantic_lineage_summary(
        self,
        *,
        state: Any,
        fqn: str,
        lineage_summary: str | None,
    ) -> None:
        if not lineage_summary:
            return
        if state.semantic_grounding is None:
            state.semantic_grounding = SemanticGrounding(
                chosen_table_fqn=fqn,
                candidates=[],
                selection_rationale=None,
                lineage_summary=lineage_summary,
            )
            self.bump_metadata_context_revision(state)
            return
        chosen_table_fqn = state.semantic_grounding.chosen_table_fqn or fqn
        updated_grounding = state.semantic_grounding.model_copy(
            update={
                "chosen_table_fqn": chosen_table_fqn,
                "lineage_summary": lineage_summary,
            }
        )
        if updated_grounding == state.semantic_grounding:
            return
        state.semantic_grounding = updated_grounding
        self.bump_metadata_context_revision(state)

    def sync_execution_zone(
        self,
        *,
        state: Any,
        db_connectors: list[ConnectorItem],
    ) -> None:
        selected_zone = self._connector_scope_policy.execution_zone_from_selected_connector(
            state=state,
            db_connectors=db_connectors,
        )
        if selected_zone is not None:
            state.execution_zone = selected_zone
            return

        context_zones = self._connector_scope_policy.context_execution_zones(
            context_tables=state.context_tables,
            db_connectors=db_connectors,
        )
        if len(context_zones) == 1:
            state.execution_zone = context_zones[0]
            return
        if len(context_zones) == 0:
            state.execution_zone = None
            return

        state.execution_zone = ExecutionZone(
            service=context_zones[0].service,
            database=context_zones[0].database,
            connector_name=None,
        )

    def merge_context_tables(
        self,
        *,
        state: Any,
        table_fqns: list[str | None],
    ) -> None:
        merged = list(state.context_tables)
        changed = False
        for table in table_fqns:
            if not isinstance(table, str) or not table.strip():
                continue
            clean = table.strip()
            if clean not in merged:
                merged.append(clean)
                changed = True
        merged = merged[:20]
        if merged != state.context_tables:
            state.context_tables = merged
            if changed or merged:
                self.bump_metadata_context_revision(state)

    def fetch_context_tables(
        self,
        *,
        search: Any,
        filtered: list[TableContext],
        db_connectors: list[ConnectorItem],
    ) -> list[str]:
        if not isinstance(search, str) or not search.strip():
            return []

        filtered_fqns = self._connector_scope_policy.filter_context_tables_by_available_connectors(
            table_fqns=[table.fqn for table in filtered[:20]],
            db_connectors=db_connectors,
        )
        normalized_search = self.normalize_leaf_table_name(search)
        exact_matches = [
            table_fqn
            for table_fqn in filtered_fqns
            if table_fqn.lower().split(".")[-1] == normalized_search
        ]
        if exact_matches:
            return exact_matches[:20]
        if len(filtered_fqns) <= 5:
            return filtered_fqns[:20]
        return []

    def prioritize_context_tables(
        self,
        *,
        state: Any,
        preferred_table_fqns: list[str],
        max_items: int = 8,
    ) -> None:
        prioritized: list[str] = []
        for table_fqn in [*preferred_table_fqns, *state.context_tables]:
            if not isinstance(table_fqn, str) or not table_fqn.strip():
                continue
            clean = table_fqn.strip()
            if clean in prioritized:
                continue
            prioritized.append(clean)
            if len(prioritized) >= max_items:
                break
        if prioritized and prioritized != state.context_tables:
            state.context_tables = prioritized
            self.bump_metadata_context_revision(state)

    @staticmethod
    def strong_catalog_table_fqns(
        results: list[dict[str, Any]],
        *,
        max_items: int = 5,
    ) -> list[str]:
        table_rows = [
            row
            for row in results
            if row.get("fullyQualifiedName")
            and str(row.get("entityType") or "").strip().lower() == "table"
        ]
        if not table_rows:
            return []
        scored_rows = [
            row for row in table_rows if isinstance(row.get("relevance_score"), int | float)
        ]
        if not scored_rows:
            return [str(row["fullyQualifiedName"]).strip() for row in table_rows[:max_items]]
        best_score = max(float(row.get("relevance_score", 0.0)) for row in scored_rows)
        score_floor = max(35.0, best_score - 18.0)
        strong_rows = [
            row for row in scored_rows if float(row.get("relevance_score", 0.0)) >= score_floor
        ]
        if not strong_rows:
            strong_rows = scored_rows[:1]
        return [str(row["fullyQualifiedName"]).strip() for row in strong_rows[:max_items]]

    @staticmethod
    def should_fallback_to_table_lookup(
        *,
        query: str,
        entity_type: Any,
        results: list[dict[str, Any]],
    ) -> bool:
        if not isinstance(query, str) or not query.strip():
            return False
        normalized_query = query.strip().lower()
        if not re.fullmatch(r"[a-z0-9_.]+", normalized_query):
            return False
        if entity_type not in (None, "table"):
            return False
        if not results:
            return True
        for item in results:
            name = item.get("fullyQualifiedName") or item.get("displayName") or item.get("name")
            if not isinstance(name, str):
                continue
            lowered = name.strip().lower()
            leaf = lowered.split(".")[-1]
            if leaf == normalized_query or leaf.startswith(normalized_query):
                return False
        return True

    @staticmethod
    def should_fallback_fetch_to_catalog(
        *,
        search: Any,
        connection: Any | None,
    ) -> bool:
        if connection is None or not getattr(connection, "mcp_tables_url", None):
            return False
        if not isinstance(search, str) or not search.strip():
            return False
        normalized = search.strip().lower()
        return bool(re.fullmatch(r"[a-z0-9_.]+", normalized))

    def should_attempt_live_table_lookup(
        self,
        *,
        search: Any,
        table_fqns: list[str],
    ) -> bool:
        if not isinstance(search, str) or not search.strip():
            return False
        normalized = search.strip().lower()
        if not re.fullmatch(r"[a-z0-9_.]+", normalized):
            return False
        expected_leaf = self.normalize_leaf_table_name(normalized)
        for table_fqn in table_fqns:
            if not isinstance(table_fqn, str) or not table_fqn.strip():
                continue
            candidate_leaf = table_fqn.strip().lower().split(".")[-1]
            if candidate_leaf == expected_leaf:
                return False
        return True

    @staticmethod
    def should_prefer_semantic_output(
        *,
        search: Any,
        entity_type: Any = "table",
    ) -> bool:
        if not isinstance(search, str) or not search.strip():
            return False
        if entity_type not in (None, "table", "TABLE"):
            return False
        return bool(re.fullmatch(r"[a-z0-9_.]+", search.strip().lower()))

    def metadata_lookup_limit(
        self,
        *,
        search: Any,
        requested_limit: Any,
        default: int,
        maximum: int,
        entity_type: Any = "table",
    ) -> int:
        _ = search, entity_type
        return self.bounded_int(
            requested_limit,
            default=default,
            minimum=1,
            maximum=maximum,
        )
