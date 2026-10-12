"""Deterministic database connector scope policy for tool execution."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.metadata import TableContext
from rhetorica_backend.domain.chat.orchestration.state import ExecutionZone


class ToolExecutionConnectorScopePolicy:
    """Resolve exact connector and SQL namespaces without catalog-specific mappings."""

    @staticmethod
    def connector_mapping_value(
        connector: ConnectorItem,
        key: str,
    ) -> str | None:
        connection = connector.connection
        raw: Any
        if key == "service":
            raw = connector.name
        elif key == "database":
            raw = connection.catalog or connection.database
        elif key == "schema":
            raw = connection.schema_name
        else:
            return None
        return raw.strip() if isinstance(raw, str) and raw.strip() else None

    @staticmethod
    def filter_context_tables_by_available_connectors(
        *,
        table_fqns: list[str | None],
        db_connectors: list[ConnectorItem],
    ) -> list[str]:
        del db_connectors
        return [table.strip() for table in table_fqns if isinstance(table, str) and table.strip()]

    @staticmethod
    def filter_table_contexts_by_available_connectors(
        *,
        tables: list[TableContext],
        db_connectors: list[ConnectorItem],
    ) -> list[TableContext]:
        del db_connectors
        return tables

    @staticmethod
    def filter_catalog_results_by_available_connectors(
        *,
        results: list[dict[str, Any]],
        db_connectors: list[ConnectorItem],
        table_context_from_entity_result,
        normalized_result_entity_type,
    ) -> list[dict[str, Any]]:
        del db_connectors, table_context_from_entity_result, normalized_result_entity_type
        return results

    @staticmethod
    def table_context_is_available_for_connectors(
        *,
        table: TableContext,
        db_connectors: list[ConnectorItem],
    ) -> bool:
        del table, db_connectors
        return True

    @staticmethod
    def context_tables_for_connector_scope(
        *,
        context_tables: list[str],
        selected_connector: ConnectorItem,
    ) -> list[str]:
        del selected_connector
        return list(context_tables)

    @classmethod
    def metadata_columns_for_connector_scope(
        cls,
        *,
        metadata_table_columns: dict[str, set[str]],
        selected_connector: ConnectorItem,
    ) -> dict[str, set[str]]:
        scoped = {table: set(columns) for table, columns in metadata_table_columns.items()}
        for table_fqn, columns in list(scoped.items()):
            for execution_ref in cls.execution_refs_for_metadata_table(
                table_fqn=table_fqn,
                selected_connector=selected_connector,
            ):
                scoped.setdefault(execution_ref, set()).update(columns)
        return scoped

    @staticmethod
    def table_fqn_matches_connector_scope(
        *,
        table_fqn: str,
        selected_connector: ConnectorItem,
    ) -> bool:
        del selected_connector
        return bool(table_fqn.strip())

    @classmethod
    def find_connector_by_mapping(
        cls,
        *,
        db_connectors: list[ConnectorItem],
        service: str | None,
        database: str | None,
        schema: str | None,
    ) -> ConnectorItem | None:
        matches: list[tuple[tuple[int, int, int], ConnectorItem]] = []
        for connector in db_connectors:
            values = {
                "service": cls.connector_mapping_value(connector, "service"),
                "database": cls.connector_mapping_value(connector, "database"),
                "schema": cls.connector_mapping_value(connector, "schema"),
            }
            score = cls.mapping_match_score(
                settings=values,
                service=service,
                database=database,
                schema=schema,
            )
            if score is not None:
                matches.append((score, connector))
        if not matches:
            return None
        matches.sort(key=lambda item: item[0], reverse=True)
        if len(matches) > 1 and matches[0][0] == matches[1][0]:
            return None
        return matches[0][1]

    @staticmethod
    def find_connector_by_name(
        *,
        db_connectors: list[ConnectorItem],
        connector_name: str,
    ) -> ConnectorItem | None:
        normalized_name = connector_name.strip().lower()
        for connector in db_connectors:
            if connector.name.strip().lower() == normalized_name:
                return connector
        return None

    @staticmethod
    def table_mapping_from_fqn(table_fqn: str) -> dict[str, str] | None:
        parts = [part.strip() for part in table_fqn.split(".") if part.strip()]
        if len(parts) < 4:
            return None
        return {
            "service": parts[0],
            "database": parts[1],
            "schema": parts[-2],
            "table": parts[-1],
        }

    @classmethod
    def execution_table_mapping_from_fqn(
        cls,
        *,
        table_fqn: str,
        selected_connector: ConnectorItem,
    ) -> dict[str, str] | None:
        parts = [part.strip() for part in table_fqn.split(".") if part.strip()]
        if len(parts) < 2:
            return None
        engine = str(selected_connector.connection.engine).lower()
        if engine == "trino" and len(parts) >= 3:
            database, schema, table = parts[-3:]
        else:
            database = (
                selected_connector.connection.catalog
                or selected_connector.connection.database
                or ""
            )
            schema, table = parts[-2:]
        return {
            "service": selected_connector.name,
            "database": database,
            "schema": schema,
            "table": table,
        }

    @classmethod
    def metadata_table_fqn_for_execution_ref(
        cls,
        *,
        table_fqn: str,
        selected_connector: ConnectorItem,
    ) -> str | None:
        mapping = cls.execution_table_mapping_from_fqn(
            table_fqn=table_fqn,
            selected_connector=selected_connector,
        )
        if mapping is None:
            return None
        parts = [mapping["service"], mapping["database"], mapping["schema"], mapping["table"]]
        return ".".join(part for part in parts if part)

    @staticmethod
    def execution_refs_for_metadata_table(
        *,
        table_fqn: str,
        selected_connector: ConnectorItem,
    ) -> list[str]:
        parts = [part.strip() for part in table_fqn.split(".") if part.strip()]
        if len(parts) < 2:
            return []
        if selected_connector.connection.engine == "trino":
            return [".".join(parts[-3:])] if len(parts) >= 3 else []
        return [".".join(parts[-2:])]

    @classmethod
    def execution_zone_from_selected_connector(
        cls,
        *,
        state: Any,
        db_connectors: list[ConnectorItem],
    ) -> ExecutionZone | None:
        connector_name = state.selected_database_connector
        if not isinstance(connector_name, str) or not connector_name.strip():
            return None
        connector = cls.find_connector_by_name(
            db_connectors=db_connectors,
            connector_name=connector_name,
        )
        if connector is None:
            return None
        return ExecutionZone(
            service=connector.name,
            database=(connector.connection.catalog or connector.connection.database or ""),
            schema=connector.connection.schema_name,
            connector_name=connector.name,
        )

    @classmethod
    def context_execution_zones(
        cls,
        *,
        context_tables: list[str],
        db_connectors: list[ConnectorItem],
    ) -> list[ExecutionZone]:
        zones: list[ExecutionZone] = []
        seen: set[str] = set()
        for table_fqn in context_tables:
            mapping = cls.table_mapping_from_fqn(table_fqn)
            if mapping is None:
                continue
            connector = cls.find_connector_by_mapping(
                db_connectors=db_connectors,
                service=mapping["service"],
                database=mapping["database"],
                schema=mapping["schema"],
            )
            if connector is None or connector.name in seen:
                continue
            seen.add(connector.name)
            zones.append(
                ExecutionZone(
                    service=connector.name,
                    database=(connector.connection.catalog or connector.connection.database or ""),
                    schema=connector.connection.schema_name,
                    connector_name=connector.name,
                )
            )
        return zones

    @classmethod
    def mapping_match_score(
        cls,
        *,
        settings: Any,
        service: str | None,
        database: str | None,
        schema: str | None,
    ) -> tuple[int, int, int] | None:
        if not isinstance(settings, dict):
            return None
        service_score = cls.mapping_component_score(
            mapping_value=settings.get("service"),
            expected=service,
            allow_unset_wildcard=False,
        )
        database_score = cls.mapping_component_score(
            mapping_value=settings.get("database"),
            expected=database,
            allow_unset_wildcard=True,
        )
        schema_score = cls.mapping_component_score(
            mapping_value=settings.get("schema"),
            expected=schema,
            allow_unset_wildcard=True,
        )
        if None in (service_score, database_score, schema_score):
            return None
        return int(service_score), int(database_score), int(schema_score)

    @staticmethod
    def mapping_component_score(
        *,
        mapping_value: Any,
        expected: str | None,
        allow_unset_wildcard: bool,
    ) -> int | None:
        if expected is None:
            return 1
        if isinstance(mapping_value, str) and mapping_value.strip():
            return 3 if mapping_value.strip().lower() == expected.strip().lower() else None
        return 1 if allow_unset_wildcard else None
