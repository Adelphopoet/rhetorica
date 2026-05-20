"""Connector scope and metadata mapping policy for tool execution."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.metadata import TableContext
from rhetorica_backend.domain.chat.orchestration.state import ExecutionZone


class ToolExecutionConnectorScopePolicy:
    """Owns connector-name lookup, metadata scope matching, and zone derivation."""

    @staticmethod
    def connector_mapping_value(
        connector: ConnectorItem,
        key: str,
    ) -> str | None:
        settings = getattr(connector.connection, "extra_settings", None)
        if not isinstance(settings, dict):
            return None
        value = settings.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @classmethod
    def filter_context_tables_by_available_connectors(
        cls,
        *,
        table_fqns: list[str | None],
        db_connectors: list[ConnectorItem],
    ) -> list[str]:
        if (
            not db_connectors
            or not any(
                isinstance(getattr(connector.connection, "extra_settings", None), dict)
                and connector.connection.extra_settings.get("metadata_service")
                and connector.connection.extra_settings.get("metadata_database")
                is not None
                for connector in db_connectors
            )
        ):
            return [
                table.strip()
                for table in table_fqns
                if isinstance(table, str) and table.strip()
            ]

        filtered: list[str] = []
        for table_fqn in table_fqns:
            if not isinstance(table_fqn, str) or not table_fqn.strip():
                continue
            clean = table_fqn.strip()
            mapping = cls.table_mapping_from_fqn(clean)
            if mapping is None:
                filtered.append(clean)
                continue
            if (
                cls.find_connector_by_mapping(
                    db_connectors=db_connectors,
                    service=mapping["service"],
                    database=mapping["database"],
                    schema=mapping["schema"],
                )
                is None
            ):
                continue
            filtered.append(clean)
        return filtered

    @classmethod
    def filter_table_contexts_by_available_connectors(
        cls,
        *,
        tables: list[TableContext],
        db_connectors: list[ConnectorItem],
    ) -> list[TableContext]:
        if not db_connectors:
            return tables
        return [
            table
            for table in tables
            if cls.table_context_is_available_for_connectors(
                table=table,
                db_connectors=db_connectors,
            )
        ]

    @classmethod
    def filter_catalog_results_by_available_connectors(
        cls,
        *,
        results: list[dict[str, Any]],
        db_connectors: list[ConnectorItem],
        table_context_from_entity_result,
        normalized_result_entity_type,
    ) -> list[dict[str, Any]]:
        if not db_connectors:
            return results

        filtered: list[dict[str, Any]] = []
        for item in results:
            if normalized_result_entity_type(item) != "table":
                filtered.append(item)
                continue
            table = table_context_from_entity_result(item)
            if table is None:
                continue
            if not cls.table_context_is_available_for_connectors(
                table=table,
                db_connectors=db_connectors,
            ):
                continue
            filtered.append(item)
        return filtered

    @classmethod
    def table_context_is_available_for_connectors(
        cls,
        *,
        table: TableContext,
        db_connectors: list[ConnectorItem],
    ) -> bool:
        if (
            not db_connectors
            or not any(
                isinstance(getattr(connector.connection, "extra_settings", None), dict)
                and connector.connection.extra_settings.get("metadata_service")
                and connector.connection.extra_settings.get("metadata_database")
                is not None
                for connector in db_connectors
            )
        ):
            return True

        mapping = cls.table_mapping_from_fqn(table.fqn)
        if mapping is None:
            mapping = {
                "service": table.service,
                "database": table.database,
                "schema": table.schema,
                "table": table.fqn.split(".")[-1],
            }
        if not mapping["service"] or not mapping["database"]:
            return True
        return (
            cls.find_connector_by_mapping(
                db_connectors=db_connectors,
                service=mapping["service"],
                database=mapping["database"],
                schema=mapping["schema"],
            )
            is not None
        )

    @classmethod
    def context_tables_for_connector_scope(
        cls,
        *,
        context_tables: list[str],
        selected_connector: ConnectorItem,
    ) -> list[str]:
        filtered: list[str] = []
        for table_fqn in context_tables:
            if not isinstance(table_fqn, str) or not table_fqn.strip():
                continue
            if not cls.table_fqn_matches_connector_scope(
                table_fqn=table_fqn,
                selected_connector=selected_connector,
            ):
                continue
            filtered.append(table_fqn.strip())
        return filtered

    @classmethod
    def metadata_columns_for_connector_scope(
        cls,
        *,
        metadata_table_columns: dict[str, set[str]],
        selected_connector: ConnectorItem,
    ) -> dict[str, set[str]]:
        filtered: dict[str, set[str]] = {}
        for table_fqn, columns in metadata_table_columns.items():
            if not isinstance(table_fqn, str) or not table_fqn.strip():
                continue
            if not cls.table_fqn_matches_connector_scope(
                table_fqn=table_fqn,
                selected_connector=selected_connector,
            ):
                continue
            filtered[table_fqn] = set(columns)
        for table_fqn, columns in list(filtered.items()):
            for execution_ref in cls.execution_refs_for_metadata_table(
                table_fqn=table_fqn,
                selected_connector=selected_connector,
            ):
                existing = filtered.get(execution_ref)
                filtered[execution_ref] = (
                    set(existing).union(columns)
                    if isinstance(existing, set)
                    else set(columns)
                )
        return filtered

    @classmethod
    def table_fqn_matches_connector_scope(
        cls,
        *,
        table_fqn: str,
        selected_connector: ConnectorItem,
    ) -> bool:
        metadata_mapping = cls.table_mapping_from_fqn(table_fqn)
        if metadata_mapping is not None:
            selected_service = cls.connector_mapping_value(
                selected_connector,
                "metadata_service",
            )
            return (
                isinstance(selected_service, str)
                and selected_service.strip().lower()
                == metadata_mapping["service"].lower()
            )
        mapping = cls.execution_table_mapping_from_fqn(
            table_fqn=table_fqn,
            selected_connector=selected_connector,
        )
        if mapping is None:
            return False
        engine = str(getattr(selected_connector.connection, "engine", "") or "").lower()
        if engine == "trino":
            selected_service = cls.connector_mapping_value(
                selected_connector,
                "metadata_service",
            )
            return (
                isinstance(selected_service, str)
                and selected_service.strip().lower()
                == mapping["service"].lower()
            )
        settings = getattr(selected_connector.connection, "extra_settings", {}) or {}
        return cls.mapping_match_score(
            settings=settings,
            service=mapping["service"],
            database=mapping["database"],
            schema=mapping["schema"],
        ) is not None

    @classmethod
    def find_connector_by_mapping(
        cls,
        *,
        db_connectors: list[ConnectorItem],
        service: str | None,
        database: str | None,
        schema: str | None,
    ) -> ConnectorItem | None:
        best_match: ConnectorItem | None = None
        best_score: tuple[int, int, int] | None = None
        for connector in db_connectors:
            settings = getattr(connector.connection, "extra_settings", {}) or {}
            score = cls.mapping_match_score(
                settings=settings,
                service=service,
                database=database,
                schema=schema,
            )
            if score is None:
                continue
            if best_score is None or score > best_score:
                best_match = connector
                best_score = score
        return best_match

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
            "schema": parts[2],
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
        settings = getattr(selected_connector.connection, "extra_settings", {}) or {}
        service = settings.get("metadata_service")
        database = settings.get("metadata_database")
        configured_schema = settings.get("metadata_schema")
        if not isinstance(service, str) or not service.strip():
            return None

        engine = str(getattr(selected_connector.connection, "engine", "") or "").lower()
        schema: str | None = None
        table: str | None = None
        if engine == "trino":
            if len(parts) != 3:
                return None
            database = parts[0]
            schema = parts[1]
            table = parts[2]
        elif len(parts) == 2:
            if not isinstance(database, str) or not database.strip():
                return None
            schema = parts[0]
            table = parts[1]
        elif len(parts) == 3:
            if not isinstance(database, str) or not database.strip():
                return None
            if parts[0].lower() != database.strip().lower():
                return None
            schema = parts[1]
            table = parts[2]
        else:
            return None

        if (
            engine != "trino"
            and isinstance(configured_schema, str)
            and configured_schema.strip()
            and schema.lower() != configured_schema.strip().lower()
        ):
            return None
        return {
            "service": service.strip(),
            "database": database.strip(),
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
        if cls.table_mapping_from_fqn(table_fqn) is not None:
            return table_fqn.strip()
        mapping = cls.execution_table_mapping_from_fqn(
            table_fqn=table_fqn,
            selected_connector=selected_connector,
        )
        if mapping is None:
            return None
        return (
            f"{mapping['service']}.{mapping['database']}."
            f"{mapping['schema']}.{mapping['table']}"
        )

    @classmethod
    def execution_refs_for_metadata_table(
        cls,
        *,
        table_fqn: str,
        selected_connector: ConnectorItem,
    ) -> list[str]:
        mapping = cls.table_mapping_from_fqn(table_fqn)
        if mapping is None:
            return []
        engine = str(getattr(selected_connector.connection, "engine", "") or "").lower()
        if engine == "trino":
            selected_service = cls.connector_mapping_value(
                selected_connector,
                "metadata_service",
            )
            if (
                not isinstance(selected_service, str)
                or selected_service.strip().lower() != mapping["service"].lower()
            ):
                return []
            return [f"{mapping['database']}.{mapping['schema']}.{mapping['table']}"]
        settings = getattr(selected_connector.connection, "extra_settings", {}) or {}
        if (
            cls.mapping_match_score(
                settings=settings,
                service=mapping["service"],
                database=mapping["database"],
                schema=mapping["schema"],
            )
            is None
        ):
            return []
        return [f"{mapping['schema']}.{mapping['table']}"]

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
        settings = getattr(connector.connection, "extra_settings", {}) or {}
        service = settings.get("metadata_service")
        database = settings.get("metadata_database")
        schema = settings.get("metadata_schema")
        if not isinstance(service, str) or not service.strip():
            return None
        if not isinstance(database, str) or not database.strip():
            return None
        return ExecutionZone(
            service=service.strip(),
            database=database.strip(),
            schema=schema.strip() if isinstance(schema, str) and schema.strip() else None,
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
        seen: set[tuple[str, str, str | None]] = set()
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
            zone = ExecutionZone(
                service=mapping["service"],
                database=mapping["database"],
                schema=mapping["schema"],
                connector_name=connector.name if connector is not None else None,
            )
            key = (zone.service.lower(), zone.database.lower(), zone.connector_name)
            if key in seen:
                continue
            seen.add(key)
            zones.append(zone)
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
            mapping_value=settings.get("metadata_service"),
            expected=service,
            allow_unset_wildcard=False,
        )
        database_score = cls.mapping_component_score(
            mapping_value=settings.get("metadata_database"),
            expected=database,
            allow_unset_wildcard=False,
        )
        schema_score = cls.mapping_component_score(
            mapping_value=settings.get("metadata_schema"),
            expected=schema,
            allow_unset_wildcard=True,
        )
        if None in (service_score, database_score, schema_score):
            return None
        return (int(service_score), int(database_score), int(schema_score))

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
            if mapping_value.strip().lower() == expected.strip().lower():
                return 3
            return None
        if allow_unset_wildcard:
            return 1
        return None
