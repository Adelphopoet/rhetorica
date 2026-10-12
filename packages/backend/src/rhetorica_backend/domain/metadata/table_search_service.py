"""OpenMetadata-first table search for external MCP clients.

Why this exists (probe of 2026-09-03, production, clean Claude Code client, question
"how many FoodTech orders on 2026-05-01"):

- `openmetadata__semantic_search` / `find_context` answered "Semantic search is not
  enabled" — the OpenMetadata deployment has no vector index.
- `openmetadata__search_metadata` for "orders" ranked `sandbox.daily_order_counts`,
  Teradata and Trino/tpch tables on top; no FoodTech mart in sight. Narrowing to the
  workspace's services is possible only through a raw OpenSearch `queryFilter`, which the
  model never wrote.
- The raw response for `size=20` was ~150k characters; the client spilled it to a file.
- The model then abandoned OpenMetadata and probed `system.tables` directly: 12 turns,
  5 of them a workaround.

Second probe on the local stand (same day): the scoped OpenMetadata search for
"заказы фудтех" returned eight undescribed unit-economics tables and nothing else, while
`system.tables.comment` of the order marts said "Витрина заказов FoodTech". Stopping at the
first non-empty stage reproduces the original failure with a different table, so the
stages are merged and ranked instead. Some ClickHouse comments arrive hex-encoded from the
OpenMetadata sync (`d0a2d0b0…`); they are decoded so ranking can read them.

Rhetorica already prefers OpenMetadata candidates over runtime probes inside the agent
turn (`prefer_semantic_tables`). This service makes that order executable in one tool
call: OpenMetadata scoped to an exact selected service or the workspace's engines →
OpenMetadata unscoped → column names → runtime catalog probes. Metadata services and
runtime connectors remain separate identities. Every stage is reported in `tried`, so
an empty answer explains what was attempted instead of looking like "no such table".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from rhetorica_backend.contracts.execution import sql_dialect_for_engine
from rhetorica_backend.domain.chat.metadata.semantic_grounding_service import (
    MetadataSemanticGroundingService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_context_hydration_helpers import (
    ToolExecutionContextHydrationHelpers,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_runtime_sql_policy import (
    ToolExecutionRuntimeSqlPolicy,
)

# OpenMetadata `serviceType` values for the engines Rhetorica can execute against.
# Greenplum is registered either natively or as Postgres depending on the ingestion.
_ENGINE_SERVICE_TYPES: dict[str, tuple[str, ...]] = {
    "clickhouse": ("Clickhouse",),
    "greenplum": ("Greenplum", "Postgres"),
    "trino": ("Trino",),
    "impala": ("Impala",),
}
_SERVICE_TYPE_ENGINE: dict[str, str] = {
    service_type.lower(): engine
    for engine, service_types in _ENGINE_SERVICE_TYPES.items()
    for service_type in service_types
}

_OMD_SEARCH_REMOTE_TOOL = "search_metadata"
_OMD_SEARCH_SCHEMA_PROPERTIES = frozenset({
    "query",
    "entityType",
    "size",
    "queryFilter",
    "cursor",
})
_DESCRIPTION_LIMIT = 300
_COLUMN_LIMIT = 10
_OWNER_LIMIT = 3
_RUNTIME_COLUMN_PROBES = 5
# Catalog probes read wide and rank in Python: an alphabetical cut put delivery_cpo_* and
# adtech.*_orders ahead of the order marts on the stand. Shorter names come first in SQL
# because the base mart is usually shorter than its aggregates and variants.
_RUNTIME_PROBE_ROWS = 200
_SYSTEM_DATABASES = ("system", "information_schema", "INFORMATION_SCHEMA")

_IDENTIFIER_TOKEN = re.compile(r"^[a-z0-9_]{3,}$")
_WORD_TOKEN = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
_HEX_TEXT = re.compile(r"^(?:[0-9a-f]{2}){4,}$")
# Question words and glue that only add noise to a catalog comment search.
_STOP_WORDS = frozenset({
    "сколько", "было", "была", "были", "есть", "какой", "какая", "какие", "что", "как",
    "где", "когда", "для", "при", "или", "это", "через", "был", "год", "года", "году",
    "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
    "октября", "ноября", "декабря", "число", "всего", "the", "and", "for", "how", "many",
    "count", "total", "table", "таблица", "витрина",
})
# Russian inflection endings stripped so "заказы" also matches "заказов" and "заказ".
_RU_ENDINGS = (
    "ами", "ями", "ов", "ев", "ам", "ям", "ах", "ях", "ой", "ей", "ом", "ем", "ую", "юю",
    "ые", "ие", "ых", "их", "а", "я", "ы", "и", "у", "ю", "е", "о", "ь",
)
_MIN_STEM = 4
# Schemas that hold staging/import copies of marts. They stay in the result (the copy may
# be the only described one) but rank below the mart layer.
_STAGING_SCHEMA_PREFIXES = ("stg", "staging", "imp", "import", "raw", "ods", "src", "landing")
_STAGING_PENALTY = 3
# Table names are English while questions and comments are Russian: a short glossary of
# the business terms that name marts, applied to the name probe only. Keyed by stem.
_RU_EN_NAME_TERMS: dict[str, tuple[str, ...]] = {
    "заказ": ("order",),
    "чек": ("cheque", "receipt"),
    "товар": ("art", "product", "item"),
    "клиент": ("customer", "client"),
    "пользовател": ("user",),
    "доставк": ("delivery",),
    "магазин": ("store", "shop"),
    "выручк": ("revenue",),
    "промо": ("promo",),
    "купон": ("coupon",),
    "фудтех": ("foodtech", "ft"),
    "аптек": ("pharma",),
    "курьер": ("courier",),
    "отмен": ("cancel",),
    "возврат": ("return", "refund"),
}


@dataclass(frozen=True)
class TableSearchStage:
    """One executed stage of the cascade, reported back to the caller."""

    name: str
    detail: str
    found: int
    status: str = "succeeded"
    error: str | None = None


@dataclass(frozen=True)
class MetadataSearchPage:
    """Normalized page returned by OpenMetadata `search_metadata`."""

    hits: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class MetadataServiceDiscoveryError(RuntimeError):
    """Safe, model-readable failure of native OpenMetadata service discovery."""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class TableSearchResult:
    tables: list[dict[str, Any]] = field(default_factory=list)
    tried: list[TableSearchStage] = field(default_factory=list)
    found_by: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "tables": list(self.tables),
            "tried": [
                {
                    "stage": stage.name,
                    "detail": stage.detail,
                    "found": stage.found,
                    "status": stage.status,
                    **({"error": stage.error} if stage.error else {}),
                }
                for stage in self.tried
            ],
            "found_by": self.found_by,
        }


class TableSearchService:
    """Find candidate tables for a natural-language question across workspace sources."""

    def __init__(
        self,
        *,
        mcp_tool_execution_service: Any,
        mcp_connector_repository: Any,
        db_connector_repository: Any,
        sql_router: Any,
    ) -> None:
        self._mcp_tools = mcp_tool_execution_service
        self._mcp_connectors = mcp_connector_repository
        self._db_connectors = db_connector_repository
        self._sql_router = sql_router

    def search(
        self,
        *,
        workspace_id: str,
        query: str,
        connector_name: str | None = None,
        service_name: str | None = None,
        database: str | None = None,
        size: int = 10,
    ) -> TableSearchResult:
        result = TableSearchResult()
        db_connectors = self._workspace_db_connectors(
            workspace_id=workspace_id,
            connector_name=connector_name,
        )
        engines = sorted({connector.connection.engine for connector in db_connectors})
        identifiers = self._identifier_tokens(query)
        stems = self._word_stems(query)
        translated = self._translated_terms(stems)
        name_tokens = identifiers + [term for term in translated if term not in identifiers]

        omd_stage, omd_tables = self._omd_cascade(
            result,
            workspace_id=workspace_id,
            query=query,
            engines=engines,
            service_name=service_name,
            database=database,
            size=size,
            identifiers=identifiers,
        )

        # Runtime catalogs always run: on the stand the scoped OpenMetadata stage came back
        # full of undescribed noise while the runtime comment said "Витрина заказов".
        runtime_tables = self._runtime_stage(
            result,
            workspace_id=workspace_id,
            identifiers=name_tokens,
            stems=stems,
            database=database,
            size=size,
            db_connectors=db_connectors,
        )

        merged = self._merge(omd_tables, runtime_tables)
        ranked = self._rank(
            merged,
            identifiers=identifiers,
            translated=translated,
            stems=stems,
        )[:size]
        self._fill_runtime_columns(ranked, workspace_id=workspace_id)
        result.tables = ranked
        sources = [
            name
            for name, tables in ((omd_stage, omd_tables), ("runtime", runtime_tables))
            if tables and name
        ]
        result.found_by = "+".join(sources) if sources else None
        return result

    def list_metadata_services(self, *, workspace_id: str) -> list[dict[str, Any]]:
        """Return every visible OpenMetadata database service across all remote pages."""
        omd_tool = self._omd_search_tool(workspace_id=workspace_id)
        if omd_tool is None:
            raise MetadataServiceDiscoveryError(
                code="openmetadata_search_unavailable",
                message="No active OpenMetadata search tool is available in this workspace.",
            )

        services: dict[str, dict[str, Any]] = {}
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = self._omd_search(
                workspace_id=workspace_id,
                exposed_tool_name=omd_tool,
                query="*",
                entity_type="databaseService",
                query_filter=None,
                size=50,
                cursor=cursor,
            )
            if page.error_code:
                raise MetadataServiceDiscoveryError(
                    code=page.error_code,
                    message=page.error_message or "OpenMetadata service discovery failed.",
                )
            for hit in page.hits:
                name = self._reference_name(hit.get("name")) or self._reference_name(
                    hit.get("fullyQualifiedName")
                )
                if not name:
                    continue
                fqn = self._reference_name(hit.get("fullyQualifiedName")) or name
                services.setdefault(
                    fqn.lower(),
                    {
                        "name": name,
                        "fqn": fqn,
                        "service_type": self._reference_name(hit.get("serviceType")),
                        "description": self._compact_description(
                            self._reference_name(hit.get("description"))
                        ),
                    },
                )
            if page.next_cursor is None:
                break
            if page.next_cursor in seen_cursors:
                raise MetadataServiceDiscoveryError(
                    code="openmetadata_invalid_pagination",
                    message="OpenMetadata returned a repeated pagination cursor.",
                )
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor

        return sorted(services.values(), key=lambda item: (item["name"].lower(), item["fqn"]))

    # --- stages --------------------------------------------------------------------

    def _omd_cascade(
        self,
        result: TableSearchResult,
        *,
        workspace_id: str,
        query: str,
        engines: list[str],
        service_name: str | None,
        database: str | None,
        size: int,
        identifiers: list[str],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """OpenMetadata stages, stopping at the first one that returns anything."""
        omd_tool = self._omd_search_tool(workspace_id=workspace_id)
        if omd_tool is None:
            result.tried.append(
                TableSearchStage(
                    name="omd_scoped",
                    detail="skipped: no OpenMetadata MCP connector in workspace",
                    found=0,
                    status="skipped",
                )
            )
            return None, []

        # Stage 1: scoped to the engines the workspace can actually query. Unscoped search
        # is what buried the FoodTech marts under sandbox and tpch noise.
        scoped_filter = self._omd_query_filter(
            engines=engines,
            service_name=service_name,
            database=database,
        )
        scope_detail = f"service={service_name}" if service_name else f"serviceType in {engines}"
        stages: list[tuple[str, str, str, str | None]] = [
            (
                "omd_scoped",
                f"OpenMetadata keyword search, {scope_detail}"
                + (f", schema={database}" if database else ""),
                query,
                scoped_filter,
            ),
        ]
        if service_name is None:
            # An explicit service scope is never widened to unrelated services.
            stages.append(
                ("omd_unscoped", "OpenMetadata keyword search, all services", query, None)
            )
        # Stage 3: identifier tokens as column names — "order_id" finds order marts even
        # when the table name and description say nothing about orders.
        if identifiers:
            stages.append(
                (
                    "omd_columns",
                    f"OpenMetadata search by column names {identifiers}",
                    "columns.name:(" + " OR ".join(identifiers) + ")",
                    scoped_filter,
                )
            )
        for name, detail, stage_query, query_filter in stages:
            page = self._omd_search(
                workspace_id=workspace_id,
                exposed_tool_name=omd_tool,
                query=stage_query,
                entity_type="table",
                query_filter=query_filter,
                size=size,
                cursor=None,
            )
            result.tried.append(
                TableSearchStage(
                    name=name,
                    detail=detail,
                    found=len(page.hits),
                    status="failed" if page.error_code else "succeeded",
                    error=page.error_message,
                )
            )
            if page.hits:
                return name, [
                    self._candidate_from_omd_hit(hit, found_by=name)
                    for hit in page.hits
                ]
        if not identifiers:
            result.tried.append(
                TableSearchStage(
                    name="omd_columns",
                    detail="skipped: no identifier tokens in query",
                    found=0,
                    status="skipped",
                )
            )
        return None, []

    def _runtime_stage(
        self,
        result: TableSearchResult,
        *,
        workspace_id: str,
        identifiers: list[str],
        stems: list[str],
        database: str | None,
        size: int,
        db_connectors: list[Any],
    ) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        probed: list[str] = []
        succeeded: list[str] = []
        failed: list[str] = []
        for connector in db_connectors:
            connection = self._sql_router.get_connection_for_execution(
                workspace_id=workspace_id,
                connector_name=connector.name,
            )
            if connection is None:
                continue
            sql = self._runtime_tables_sql(
                connection=connection,
                identifiers=identifiers,
                stems=stems,
                database=database,
                size=size,
            )
            if sql is None:
                continue
            probed.append(connector.name)
            dialect = sql_dialect_for_engine(connection.engine)
            try:
                execution = self._sql_router.execute_sql(
                    sql=sql,
                    connection=connection,
                    workspace_id=workspace_id,
                    dialect=dialect,
                )
            except Exception:
                failed.append(connector.name)
                continue
            if execution.status != "succeeded":
                failed.append(connector.name)
                continue
            succeeded.append(connector.name)
            for row in execution.preview.rows:
                if not isinstance(row, list) or len(row) < 2:
                    continue
                schema_name = str(row[0]).strip()
                table_name = str(row[1]).strip()
                comment = self._decode_comment(row[2]) if len(row) > 2 else None
                found.append({
                    "fqn": ToolExecutionRuntimeSqlPolicy.runtime_table_fqn(
                        connection=connection,
                        schema_name=schema_name,
                        table_name=table_name,
                    ),
                    "origin": "runtime",
                    "service": None,
                    "connector": connector.name,
                    "engine": connection.engine,
                    "connectors": [connector.name],
                    "database": connection.catalog or connection.database,
                    "schema": schema_name,
                    "description": self._compact_description(comment),
                    "columns": [],
                    "tier": None,
                    "owners": [],
                    "found_by": "runtime",
                    "suspicious": self._is_suspicious_schema(schema_name),
                    "layer": self._layer(schema_name),
                    "_runtime": (connection, dialect, schema_name, table_name),
                })
        status = "skipped"
        error = None
        if failed and succeeded:
            status = "partial"
            error = f"Catalog query failed for connectors: {', '.join(sorted(failed))}."
        elif failed:
            status = "failed"
            error = f"Catalog query failed for connectors: {', '.join(sorted(failed))}."
        elif succeeded:
            status = "succeeded"
        result.tried.append(
            TableSearchStage(
                name="runtime",
                detail=(
                    f"catalog probe in {probed}: names like {identifiers}, comments like {stems}"
                    if probed
                    else "skipped: no database connector or no usable tokens"
                ),
                found=len(found),
                status=status,
                error=error,
            )
        )
        return found

    def _fill_runtime_columns(self, ranked: list[dict[str, Any]], *, workspace_id: str) -> None:
        """Column lookups cost one query each, so only the top-ranked runtime hits get them."""
        probes = 0
        for candidate in ranked:
            runtime = candidate.pop("_runtime", None)
            if runtime is None or candidate.get("columns") or probes >= _RUNTIME_COLUMN_PROBES:
                continue
            connection, dialect, schema_name, table_name = runtime
            candidate["columns"] = self._runtime_columns(
                connection=connection,
                workspace_id=workspace_id,
                dialect=dialect,
                schema_name=schema_name,
                table_name=table_name,
            )
            probes += 1
        for candidate in ranked:
            candidate.pop("_runtime", None)

    # --- merge and rank --------------------------------------------------------------

    @staticmethod
    def _table_key(candidate: dict[str, Any]) -> tuple[str, ...]:
        """Keep origin identity so metadata cannot leak across services or connectors."""
        origin = str(candidate.get("origin") or "").lower()
        source = candidate.get("service") if origin == "openmetadata" else candidate.get("connector")
        return tuple(
            str(value or "").lower().replace('"', "")
            for value in (
                origin,
                source,
                candidate.get("database"),
                candidate.get("schema"),
                str(candidate.get("fqn") or "").rsplit(".", 1)[-1],
            )
        )

    def _merge(
        self,
        omd_tables: list[dict[str, Any]],
        runtime_tables: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[tuple[str, ...], dict[str, Any]] = {}
        for candidate in (*omd_tables, *runtime_tables):
            merged.setdefault(self._table_key(candidate), candidate)
        return list(merged.values())

    @classmethod
    def _rank(
        cls,
        candidates: list[dict[str, Any]],
        *,
        identifiers: list[str],
        translated: list[str],
        stems: list[str],
    ) -> list[dict[str, Any]]:
        """Score by evidence the caller can see, then keep the source order.

        An identifier from the query equal to the table name weighs most; identifiers and
        translated terms inside the name next (a translated term never counts as an exact
        name — `dm.order` must not beat `dm.ft_order_cheque` for "заказы"); then query
        stems in the description and having any description at all. OpenMetadata wins ties
        over runtime, staging/import layers rank below marts, sandbox/test/tmp scopes go
        last but stay visible.
        """
        name_tokens = [t for t in (*identifiers, *translated) if len(t) >= 4]

        def score(candidate: dict[str, Any]) -> int:
            fqn = str(candidate.get("fqn") or "").lower()
            table_name = fqn.rsplit(".", 1)[-1]
            description = str(candidate.get("description") or "").lower()
            total = 8 * sum(1 for token in identifiers if token == table_name)
            # Short tokens such as "ft" or "art" match half the catalog; they only count
            # as exact names above.
            total += 4 * sum(1 for token in name_tokens if token in fqn)
            total += 2 * sum(1 for stem in {*stems, *name_tokens} if stem in description)
            total += 1 if description else 0
            total += 1 if candidate.get("found_by") != "runtime" else 0
            total -= _STAGING_PENALTY if candidate.get("layer") == "staging" else 0
            total -= 10 if candidate.get("suspicious") else 0
            return total

        def name_length(candidate: dict[str, Any]) -> int:
            return len(str(candidate.get("fqn") or "").rsplit(".", 1)[-1])

        # Equal evidence: the shorter name is usually the base mart, the longer one its
        # aggregate or a variant; then keep the source order.
        indexed = list(enumerate(candidates))
        indexed.sort(key=lambda pair: (-score(pair[1]), name_length(pair[1]), pair[0]))
        return [candidate for _, candidate in indexed]

    # --- sources -------------------------------------------------------------------

    def _workspace_db_connectors(self, *, workspace_id: str, connector_name: str | None) -> list[Any]:
        connectors = list(self._db_connectors.list(workspace_id=workspace_id, limit=None))
        if connector_name:
            connectors = [c for c in connectors if c.name == connector_name]
        return [c for c in connectors if c.connection.engine in _ENGINE_SERVICE_TYPES]

    def _omd_search_tool(self, *, workspace_id: str) -> str | None:
        """Find a synced search tool that supports the OpenMetadata argument contract."""
        for item in self._mcp_connectors.list_active_tools(workspace_id=workspace_id, limit=None):
            if item.remote_tool_name != _OMD_SEARCH_REMOTE_TOOL or not item.is_active:
                continue
            schema = item.input_schema if isinstance(item.input_schema, dict) else {}
            properties = schema.get("properties")
            if isinstance(properties, dict) and properties.keys() >= _OMD_SEARCH_SCHEMA_PROPERTIES:
                return item.exposed_tool_name
        return None

    @staticmethod
    def _omd_query_filter(
        *,
        engines: list[str],
        service_name: str | None,
        database: str | None,
    ) -> str | None:
        must: list[dict[str, Any]] = []
        service_types = [
            service_type
            for engine in engines
            for service_type in _ENGINE_SERVICE_TYPES.get(engine, ())
        ]
        if service_types:
            must.append({"terms": {"serviceType": service_types}})
        if service_name:
            must.append({"term": {"service.name.keyword": service_name}})
        if database:
            must.append({"term": {"databaseSchema.name.keyword": database}})
        if not must:
            return None
        return json.dumps({"query": {"bool": {"must": must}}})

    def _omd_search(
        self,
        *,
        workspace_id: str,
        exposed_tool_name: str,
        query: str,
        entity_type: str,
        query_filter: str | None,
        size: int,
        cursor: str | None,
    ) -> MetadataSearchPage:
        arguments: dict[str, Any] = {"query": query, "entityType": entity_type, "size": size}
        if query_filter:
            arguments["queryFilter"] = query_filter
        if cursor:
            arguments["cursor"] = cursor
        payload = self._mcp_tools.execute(
            workspace_id=workspace_id,
            exposed_tool_name=exposed_tool_name,
            arguments=arguments,
        )
        if payload.get("ok") is not True:
            code = self._reference_name(payload.get("error_code")) or "openmetadata_search_failed"
            message = self._reference_name(payload.get("user_message")) or "OpenMetadata search failed."
            return MetadataSearchPage(error_code=code, error_message=message)
        body = self._remote_json(payload.get("result"))
        if body is None:
            return MetadataSearchPage(
                error_code="openmetadata_invalid_response",
                error_message="OpenMetadata returned an invalid search response.",
            )
        hits = body.get("results")
        if not isinstance(hits, list):
            return MetadataSearchPage(
                error_code="openmetadata_invalid_response",
                error_message="OpenMetadata search response has no results list.",
            )
        next_cursor = body.get("nextCursor") if body.get("hasMore") else None
        if next_cursor is not None and not isinstance(next_cursor, str):
            return MetadataSearchPage(
                error_code="openmetadata_invalid_response",
                error_message="OpenMetadata returned an invalid pagination cursor.",
            )
        return MetadataSearchPage(
            hits=[hit for hit in hits if isinstance(hit, dict)],
            next_cursor=next_cursor,
        )

    @staticmethod
    def _remote_json(result: Any) -> dict[str, Any] | None:
        content = result.get("content") if isinstance(result, dict) else None
        if not isinstance(content, list):
            return None
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                try:
                    parsed = json.loads(item.get("text") or "")
                except (TypeError, ValueError):
                    return None
                return parsed if isinstance(parsed, dict) else None
        return None

    @staticmethod
    def _runtime_tables_sql(
        *,
        connection: Any,
        identifiers: list[str],
        stems: list[str],
        database: str | None,
        size: int,
    ) -> str | None:
        limit = _RUNTIME_PROBE_ROWS
        if connection.engine == "clickhouse":
            clauses: list[str] = []
            clauses.extend(f"lower(name) LIKE '%{_sql_literal(token)}%'" for token in identifiers)
            clauses.extend(f"comment ILIKE '%{_sql_literal(stem)}%'" for stem in stems)
            if not clauses:
                return None
            excluded = ", ".join(f"'{name}'" for name in _SYSTEM_DATABASES)
            where = f"database NOT IN ({excluded}) AND ({' OR '.join(clauses)})"
            if database:
                where += f" AND database = '{_sql_literal(database)}'"
            # Tokens are quoted by _sql_literal, the statement is a SELECT over system.tables
            # only, and it still passes SqlGuard through SqlRouter like every other query.
            return (
                "SELECT database, name, comment FROM system.tables "  # noqa: S608
                f"WHERE {where} ORDER BY length(name), database, name LIMIT {limit}"
            )
        if not identifiers:
            return None
        # Other engines have no comment column in their catalog views; reuse the agent's
        # runtime lookup SQL on the first identifier token.
        return ToolExecutionRuntimeSqlPolicy.runtime_tables_lookup_sql(
            connection=connection,
            leaf_name=identifiers[0],
            database_hint=database,
            limit=limit,
            exact=False,
        )

    def _runtime_columns(
        self,
        *,
        connection: Any,
        workspace_id: str,
        dialect: str,
        schema_name: str,
        table_name: str,
    ) -> list[str]:
        execution = self._sql_router.execute_sql(
            sql=ToolExecutionRuntimeSqlPolicy.runtime_columns_lookup_sql(
                connection=connection,
                schema_name=schema_name,
                table_name=table_name,
            ),
            connection=connection,
            workspace_id=workspace_id,
            dialect=dialect,
        )
        if execution.status != "succeeded":
            return []
        return [
            str(row[0]).strip()
            for row in execution.preview.rows[:_COLUMN_LIMIT]
            if isinstance(row, list) and row and str(row[0]).strip()
        ]

    # --- candidates ----------------------------------------------------------------

    def _candidate_from_omd_hit(
        self,
        hit: dict[str, Any],
        *,
        found_by: str,
    ) -> dict[str, Any]:
        table = ToolExecutionContextHydrationHelpers.table_context_from_entity_result(hit)
        service_type = str(hit.get("serviceType") or "")
        engine = _SERVICE_TYPE_ENGINE.get(service_type.lower())
        description = table.description if table is not None else None
        columns = list(table.columns) if table is not None else []
        return {
            "fqn": table.fqn if table is not None else hit.get("fullyQualifiedName"),
            "origin": "openmetadata",
            "service": table.service if table is not None else None,
            "connector": None,
            "engine": engine,
            # serviceType only identifies an engine family, not the physical DWH.
            "connectors": [],
            "database": table.database if table is not None else None,
            "schema": table.schema if table is not None else None,
            "description": self._compact_description(description),
            "columns": columns[:_COLUMN_LIMIT],
            "tier": table.tier if table is not None else None,
            "owners": list(table.owners[:_OWNER_LIMIT]) if table is not None else [],
            "found_by": found_by,
            "suspicious": (
                MetadataSemanticGroundingService.is_suspicious_table(table)
                if table is not None
                else False
            ),
            "layer": self._layer(table.schema if table is not None else None),
        }

    @staticmethod
    def _reference_name(value: Any) -> str | None:
        """Read an OpenMetadata reference without changing its exact identifier."""
        if isinstance(value, dict):
            value = value.get("fullyQualifiedName") or value.get("name")
        if not isinstance(value, str) or not value:
            return None
        return value

    @staticmethod
    def _is_suspicious_schema(schema_name: str) -> bool:
        tokens = set(re.split(r"[^a-z0-9]+", schema_name.lower()))
        return bool(tokens & MetadataSemanticGroundingService.SUSPICIOUS_SCOPE_TOKENS)

    @staticmethod
    def _identifier_tokens(query: str) -> list[str]:
        tokens: list[str] = []
        for raw in re.split(r"[\s,;()]+", query.lower()):
            token = raw.strip("`\"'.")
            if _IDENTIFIER_TOKEN.match(token) and token not in tokens:
                tokens.append(token)
        return tokens

    @staticmethod
    def _translated_terms(stems: list[str]) -> list[str]:
        """English name terms for the Russian stems of the query."""
        terms: list[str] = []
        for stem in stems:
            for ru_stem, en_terms in _RU_EN_NAME_TERMS.items():
                if stem.startswith(ru_stem):
                    terms.extend(term for term in en_terms if term not in terms)
        return terms

    @staticmethod
    def _layer(schema_name: str | None) -> str | None:
        first = (schema_name or "").lower().split("_", 1)[0]
        return "staging" if first in _STAGING_SCHEMA_PREFIXES else None

    @staticmethod
    def _word_stems(query: str) -> list[str]:
        """Alphabetic tokens minus stop words, Russian ones cut to a matching stem."""
        stems: list[str] = []
        for token in _WORD_TOKEN.findall(query.lower()):
            if token in _STOP_WORDS:
                continue
            stem = token
            if re.search(r"[а-яё]", token):
                for ending in _RU_ENDINGS:
                    if token.endswith(ending) and len(token) - len(ending) >= _MIN_STEM:
                        stem = token[: -len(ending)]
                        break
            if stem not in stems:
                stems.append(stem)
        return stems

    @staticmethod
    def _decode_comment(value: Any) -> str | None:
        """ClickHouse comments synced from OpenMetadata sometimes arrive hex-encoded."""
        if value is None:
            return None
        text = str(value).strip()
        if _HEX_TEXT.match(text):
            try:
                return bytes.fromhex(text).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return text
        return text or None

    @staticmethod
    def _compact_description(description: str | None) -> str | None:
        if not description:
            return None
        clean = " ".join(description.split())
        if len(clean) <= _DESCRIPTION_LIMIT:
            return clean
        return clean[: _DESCRIPTION_LIMIT - 1].rstrip() + "…"


def _sql_literal(value: str) -> str:
    """Quote a token for a LIKE pattern; `_` stays a wildcard on purpose (`ft_order` still matches)."""
    return value.replace("\\", "\\\\").replace("'", "''").replace("%", "")
