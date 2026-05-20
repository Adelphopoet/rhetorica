"""Context and lineage hydration helpers for tool execution."""

from __future__ import annotations

import re
from typing import Any

from rhetorica_backend.contracts.execution import ExecuteSqlResponse, sql_dialect_for_engine
from rhetorica_backend.domain.chat.metadata import (
    MetadataSemanticGroundingService,
    TableContext,
)
from rhetorica_backend.domain.chat.metadata.payload_mapper import MetadataPayloadMapper
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError


class ToolExecutionContextHydrationHelpers:
    """Converts metadata payloads and runtime executions into table context."""

    _payload_mapper = MetadataPayloadMapper()

    @classmethod
    def table_context_to_catalog_result(cls, table: TableContext) -> dict[str, Any]:
        return {
            "entityType": "table",
            "displayName": table.fqn.split(".")[-1],
            "fullyQualifiedName": table.fqn,
            "description": table.description,
            "service": table.service,
            "database": table.database,
            "databaseSchema": table.schema,
            "columnNames": table.columns[:20],
            "owners": table.owners[:10],
            "owner_teams": table.owner_teams[:10],
            "owner_refs": [
                cls._payload_mapper.entity_ref_payload(ref)
                for ref in table.owner_refs[:10]
            ],
            "tags": table.tags[:10],
            "domains": table.domains[:10],
            "domain_refs": [
                cls._payload_mapper.entity_ref_payload(ref)
                for ref in table.domain_refs[:10]
            ],
            "glossary_terms": [
                cls._payload_mapper.entity_ref_payload(ref)
                for ref in table.glossary_terms[:10]
            ],
            "related_metrics": [
                cls._payload_mapper.metric_ref_payload(ref)
                for ref in table.related_metrics[:10]
            ],
            "quality_summary": (
                table.quality_summary.to_payload()
                if table.quality_summary is not None
                else None
            ),
            "quality_status": (
                table.quality_summary.status
                if table.quality_summary is not None
                else None
            ),
            "tier": table.tier,
            "certification": table.certification,
            "usage_count": table.usage_count,
            "relevance_score": round(table.relevance_score, 3),
            "matched_on": table.matched_on,
            "reason_codes": MetadataSemanticGroundingService.semantic_reason_codes(table),
            "suspicious": MetadataSemanticGroundingService.is_suspicious_table(table),
        }

    @staticmethod
    def normalized_result_entity_type(item: dict[str, Any]) -> str | None:
        entity_type = item.get("entityType")
        if isinstance(entity_type, str) and entity_type.strip():
            return entity_type.strip().lower()
        return None

    @classmethod
    def table_context_from_entity_result(
        cls,
        result: dict[str, Any] | None,
    ) -> TableContext | None:
        entity = cls.unwrap_entity_payload(result)
        if entity is None:
            return None
        fqn = entity.get("fullyQualifiedName") or entity.get("fqn") or entity.get("name")
        if not isinstance(fqn, str) or not fqn.strip():
            return None

        columns: list[str] = []
        column_types: dict[str, str] = {}
        column_descriptions: dict[str, str] = {}
        raw_columns = entity.get("columns")
        if isinstance(raw_columns, list):
            for column in raw_columns:
                if not isinstance(column, dict):
                    continue
                name = column.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                clean = name.strip()
                columns.append(clean)
                dtype = (
                    column.get("dataTypeDisplay")
                    or column.get("dataType")
                    or column.get("type")
                )
                if isinstance(dtype, str) and dtype.strip():
                    column_types[clean] = dtype.strip()
                description = column.get("description")
                if isinstance(description, str) and description.strip():
                    column_descriptions[clean] = description.strip()
        elif isinstance(entity.get("columnNames"), list):
            columns = [
                str(column).strip()
                for column in entity.get("columnNames", [])
                if str(column).strip()
            ]

        return TableContext(
            source="mcp",
            fqn=fqn.strip(),
            columns=columns,
            column_types=column_types,
            column_descriptions=column_descriptions,
            description=entity.get("description")
            if isinstance(entity.get("description"), str)
            else None,
            service=cls.nested_or_string_name(entity.get("service")),
            database=cls.nested_or_string_name(entity.get("database")),
            schema=cls.nested_or_string_name(
                entity.get("schema") or entity.get("databaseSchema")
            ),
            owners=cls.list_of_strings(entity.get("owners")),
            owner_refs=cls._payload_mapper.extract_entity_refs(
                entity.get("owner_refs") or entity.get("owners")
            ),
            owner_teams=cls.list_of_strings(entity.get("owner_teams"))
            or cls._payload_mapper.extract_owner_teams(entity.get("owners")),
            tags=cls.list_of_strings(entity.get("tags")),
            domains=cls.list_of_strings(entity.get("domains") or entity.get("domain")),
            domain_refs=cls._payload_mapper.extract_entity_refs(
                entity.get("domain_refs") or entity.get("domains") or entity.get("domain")
            ),
            glossary_terms=cls._payload_mapper.extract_entity_refs(
                entity.get("glossary_terms") or entity.get("glossaryTerms")
            )
            or cls._payload_mapper.extract_glossary_terms(entity),
            related_metrics=cls._payload_mapper.extract_metric_refs(
                entity.get("related_metrics")
                or entity.get("relatedMetrics")
                or entity.get("metrics")
            ),
            quality_summary=cls._payload_mapper.extract_quality_summary(
                entity.get("quality_summary")
            ),
            tier=cls.nested_or_string_name(entity.get("tier"))
            or cls.first_matching_prefix(entity.get("tags"), "Tier."),
            certification=cls.nested_or_string_name(entity.get("certification"))
            or cls.first_matching_prefix(entity.get("tags"), "Certification."),
            usage_count=cls.coerce_int(entity.get("usage_count")),
        )

    @classmethod
    def table_context_from_catalog_item(
        cls,
        item: dict[str, Any] | None,
    ) -> TableContext | None:
        if not isinstance(item, dict):
            return None
        entity_type = item.get("entityType")
        if isinstance(entity_type, str) and entity_type.strip().lower() != "table":
            return None
        fqn = item.get("fullyQualifiedName") or item.get("fqn") or item.get("name")
        if not isinstance(fqn, str) or not fqn.strip():
            return None
        columns = cls.list_of_strings(item.get("columnNames"))
        matched_on = cls.list_of_strings(item.get("matched_on"))
        return TableContext(
            source="mcp",
            fqn=fqn.strip(),
            columns=columns[:20],
            description=cls.nested_or_string_name(item.get("description")),
            service=cls.nested_or_string_name(item.get("service")),
            database=cls.nested_or_string_name(item.get("database")),
            schema=cls.nested_or_string_name(
                item.get("schema") or item.get("databaseSchema")
            ),
            owners=cls.list_of_strings(item.get("owners")),
            owner_refs=cls._payload_mapper.extract_entity_refs(
                item.get("owner_refs") or item.get("owners")
            ),
            owner_teams=cls.list_of_strings(item.get("owner_teams"))
            or cls._payload_mapper.extract_owner_teams(item.get("owners")),
            tags=cls.list_of_strings(item.get("tags")),
            domains=cls.list_of_strings(item.get("domains") or item.get("domain")),
            domain_refs=cls._payload_mapper.extract_entity_refs(
                item.get("domain_refs") or item.get("domains") or item.get("domain")
            ),
            glossary_terms=cls._payload_mapper.extract_entity_refs(
                item.get("glossary_terms") or item.get("glossaryTerms")
            )
            or cls._payload_mapper.extract_glossary_terms(item),
            related_metrics=cls._payload_mapper.extract_metric_refs(
                item.get("related_metrics") or item.get("relatedMetrics") or item.get("metrics")
            ),
            quality_summary=cls._payload_mapper.extract_quality_summary(
                item.get("quality_summary")
            ),
            tier=cls.nested_or_string_name(item.get("tier"))
            or cls.first_matching_prefix(item.get("tags"), "Tier."),
            certification=cls.nested_or_string_name(item.get("certification"))
            or cls.first_matching_prefix(item.get("tags"), "Certification."),
            usage_count=cls.coerce_int(item.get("usage_count")),
            relevance_score=float(item.get("relevance_score") or 0.0),
            matched_on=matched_on,
        )

    @classmethod
    def summarize_lineage_payload(
        cls,
        payload: dict[str, Any] | None,
        *,
        fqn: str,
    ) -> str | None:
        current = payload
        while isinstance(current, dict) and isinstance(current.get("lineage"), dict):
            current = current.get("lineage")
        if not isinstance(current, dict):
            return None

        upstream = cls.lineage_entity_count(current.get("upstreamEntities"))
        downstream = cls.lineage_entity_count(current.get("downstreamEntities"))
        if upstream == 0 and downstream == 0:
            nodes = current.get("nodes")
            if isinstance(nodes, list):
                node_count = len([node for node in nodes if isinstance(node, dict)])
                if node_count > 0:
                    return f"Lineage fetched for `{fqn}` with {node_count} linked nodes."
            return f"Lineage fetched for `{fqn}`."
        return (
            f"Lineage fetched for `{fqn}`: "
            f"{upstream} upstream, {downstream} downstream."
        )

    @staticmethod
    def lineage_entity_count(value: Any) -> int:
        if isinstance(value, list):
            return len([item for item in value if isinstance(item, dict)])
        if isinstance(value, dict):
            return len([item for item in value.values() if isinstance(item, dict | list)])
        return 0

    @classmethod
    def unwrap_entity_payload(
        cls,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        current = payload
        for _ in range(3):
            if not isinstance(current, dict):
                return None
            nested = current.get("entity")
            if not isinstance(nested, dict):
                return current
            current = nested
        return current if isinstance(current, dict) else None

    @staticmethod
    def nested_or_string_name(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for key in ("displayName", "name", "fullyQualifiedName"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip().split(".")[-1]
        return None

    @classmethod
    def list_of_strings(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            if isinstance(item, dict):
                tag_fqn = item.get("tagFQN")
                if isinstance(tag_fqn, str) and tag_fqn.strip():
                    items.append(tag_fqn.strip())
                    continue
            if isinstance(item, str) and item.strip():
                items.append(item.strip())
                continue
            nested = cls.nested_or_string_name(item)
            if nested:
                items.append(nested)
        return items

    @classmethod
    def first_matching_prefix(cls, value: Any, prefix: str) -> str | None:
        for item in cls.list_of_strings(value):
            if item.startswith(prefix):
                return item
        return None

    @staticmethod
    def coerce_int(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int | float):
            return max(0, int(value))
        return 0

    @staticmethod
    def extract_runtime_table_hits(
        execution: ExecuteSqlResponse,
    ) -> list[tuple[str, str]]:
        if execution.status != "succeeded":
            return []

        found_tables: list[tuple[str, str]] = []
        for row in execution.preview.rows:
            if not isinstance(row, list) or len(row) < 2:
                continue
            database = str(row[0]).strip()
            table_name = str(row[1]).strip()
            if not database or not table_name:
                continue
            found_tables.append((database, table_name))
        return found_tables

    def hydrate_context_from_execution(
        self,
        *,
        state: Any,
        sql: str,
        execution: ExecuteSqlResponse,
        merge_context_tables,
        set_table_columns,
        set_table_schema_snapshot,
    ) -> None:
        if execution.status != "succeeded":
            return
        if self.is_table_discovery_sql(sql):
            discovered_tables = self.extract_table_names_from_execution(execution)
            if not discovered_tables:
                return
            merge_context_tables(state=state, table_fqns=discovered_tables)
            return

        described_table = self.extract_described_table_name(sql)
        if described_table is None:
            probed_table = self.extract_schema_probe_table_name(
                sql,
                dialect=sql_dialect_for_engine(execution.engine),
            )
            if probed_table is None:
                return
            merge_context_tables(state=state, table_fqns=[probed_table])
            probed_columns = self.extract_probe_columns_from_execution(execution)
            if probed_columns:
                set_table_schema_snapshot(
                    state=state,
                    table_fqn=probed_table,
                    columns=probed_columns,
                    complete=False,
                    source="runtime_probe",
                )
            return
        merge_context_tables(state=state, table_fqns=[described_table])
        described_columns = self.extract_describe_columns_from_execution(execution)
        if described_columns:
            set_table_schema_snapshot(
                state=state,
                table_fqn=described_table,
                columns=described_columns,
                complete=True,
                source="runtime_describe",
                column_types=self.extract_describe_column_types_from_execution(execution),
            )

    @staticmethod
    def is_table_discovery_sql(sql: str) -> bool:
        normalized = sql.strip().lower()
        return (
            normalized.startswith("show tables")
            or "from system.tables" in normalized
            or "from information_schema.tables" in normalized
        )

    @staticmethod
    def extract_described_table_name(sql: str) -> str | None:
        match = re.match(
            r"^\s*(?:describe|desc)\s+([^\s;]+)",
            sql,
            flags=re.IGNORECASE,
        )
        if match is None:
            match = re.match(
                r"^\s*show\s+columns\s+from\s+([^\s;]+)",
                sql,
                flags=re.IGNORECASE,
            )
        if match is None:
            return None
        return match.group(1).strip().strip("`\"")

    @staticmethod
    def extract_schema_probe_table_name(sql: str, *, dialect: str) -> str | None:
        try:
            parsed = parse_one(sql, read=dialect)
        except ParseError:
            return None
        select = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
        if not isinstance(select, exp.Select):
            return None
        if list(select.find_all(exp.Join)):
            return None
        from_exp = select.args.get("from")
        if not isinstance(from_exp, exp.From) or not isinstance(from_exp.this, exp.Table):
            return None
        source_table = from_exp.this
        for projection in select.expressions:
            if isinstance(projection, exp.Star):
                continue
            if isinstance(projection, exp.Column):
                continue
            if isinstance(projection, exp.Alias) and isinstance(projection.this, exp.Column):
                continue
            return None
        parts = [
            part.strip("`\"")
            for part in (source_table.catalog, source_table.db, source_table.name)
            if isinstance(part, str) and part.strip()
        ]
        if not parts:
            return None
        return ".".join(parts)

    @staticmethod
    def extract_describe_columns_from_execution(
        execution: ExecuteSqlResponse,
    ) -> set[str]:
        columns = [column.strip().lower() for column in execution.preview.columns]
        if not columns or not execution.preview.rows:
            return set()

        name_index = columns.index("name") if "name" in columns else None
        if name_index is None and "column" in columns:
            name_index = columns.index("column")
        if name_index is None and len(columns) == 1:
            name_index = 0
        if name_index is None:
            return set()

        extracted: set[str] = set()
        for row in execution.preview.rows:
            if not isinstance(row, list) or len(row) <= name_index:
                continue
            value = row[name_index]
            if isinstance(value, str) and value.strip():
                extracted.add(value.strip().lower())
        return extracted

    @staticmethod
    def extract_describe_column_types_from_execution(
        execution: ExecuteSqlResponse,
    ) -> dict[str, str]:
        columns = [column.strip().lower() for column in execution.preview.columns]
        if not columns or not execution.preview.rows:
            return {}

        name_index = columns.index("name") if "name" in columns else None
        if name_index is None and "column" in columns:
            name_index = columns.index("column")
        if name_index is None and len(columns) >= 1:
            name_index = 0

        type_index = columns.index("type") if "type" in columns else None
        if type_index is None and "data_type" in columns:
            type_index = columns.index("data_type")
        if name_index is None or type_index is None:
            return {}

        extracted: dict[str, str] = {}
        for row in execution.preview.rows:
            if not isinstance(row, list) or len(row) <= max(name_index, type_index):
                continue
            column_name = str(row[name_index]).strip()
            column_type = str(row[type_index]).strip()
            if not column_name:
                continue
            if column_type:
                extracted[column_name.lower()] = column_type
        return extracted

    @staticmethod
    def extract_probe_columns_from_execution(
        execution: ExecuteSqlResponse,
    ) -> set[str]:
        return {
            column.strip().lower()
            for column in execution.preview.columns
            if isinstance(column, str) and column.strip()
        }

    @staticmethod
    def extract_table_names_from_execution(
        execution: ExecuteSqlResponse,
    ) -> list[str]:
        columns = [column.strip().lower() for column in execution.preview.columns]
        rows = execution.preview.rows
        if not rows:
            return []

        name_index = columns.index("name") if "name" in columns else None
        if name_index is None and len(columns) == 1:
            name_index = 0
        if name_index is None:
            return []

        names: list[str] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, list) or len(row) <= name_index:
                continue
            value = row[name_index]
            if not isinstance(value, str) or not value.strip():
                continue
            clean = value.strip()
            lowered = clean.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            names.append(clean)
        return names
