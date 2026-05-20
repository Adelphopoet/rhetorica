"""Tests for tool executor."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from rhetorica_backend.contracts.admin_integrations import (
    ConfluenceConnectionConfig,
    JiraConnectionConfig,
)
from rhetorica_backend.contracts.chat import (
    ChatAskRequest,
    OptimizationTrace,
    SemanticGrounding,
)
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import (
    EngineConnectionConfig,
    ExecuteSqlResponse,
    QueryPreview,
)
from rhetorica_backend.domain.chat.metadata.metadata_service import TableContext
from rhetorica_backend.domain.chat.orchestration.analysis_artifacts import (
    AnalysisCheckpointLifecycle,
)
from rhetorica_backend.domain.chat.orchestration.state import (
    ExecutionZone,
    ToolOrchestrationState,
)
from rhetorica_backend.domain.chat.orchestration.tool_executor import ToolExecutor
from rhetorica_backend.domain.chat.sql.optimization import SqlOptimizationOutcome
from rhetorica_backend.domain.chat.sql.run_sql_pipeline import RunSqlPipeline


def _connector(name: str = "clickhouse-main") -> ConnectorItem:
    now = datetime.now(tz=UTC)
    return ConnectorItem(
        id="conn-1",
        name=name,
        workspace_id="default",
        connection=EngineConnectionConfig(
            engine="clickhouse",
            host="db.local",
            port=8123,
            username="analyst",
            password=None,
            database="analytics",
        ),
        has_password=True,
        created_at=now,
        updated_at=now,
    )


def _connector_with_mapping(
    *,
    name: str,
    engine: str,
    metadata_service: str,
    metadata_database: str,
    metadata_schema: str,
) -> ConnectorItem:
    now = datetime.now(tz=UTC)
    return ConnectorItem(
        id=f"conn-{name}",
        name=name,
        workspace_id="default",
        connection=EngineConnectionConfig(
            engine=engine,
            host="db.local",
            port=8123,
            username="analyst",
            password=None,
            database=metadata_database,
            extra_settings={
                "metadata_service": metadata_service,
                "metadata_database": metadata_database,
                "metadata_schema": metadata_schema,
            },
        ),
        has_password=True,
        created_at=now,
        updated_at=now,
    )


def _execution_result(
    *,
    engine: str = "clickhouse",
    columns: list[str] | None = None,
    rows: list[list[object]] | None = None,
) -> ExecuteSqlResponse:
    return ExecuteSqlResponse(
        execution_id="exec-1",
        status="succeeded",
        engine=engine,
        row_count=1,
        preview=QueryPreview(
            columns=columns or [],
            rows=rows or [],
        ),
    )


class _SqlRouterStub:
    def __init__(
        self,
        selected_connector: ConnectorItem | None,
        connection: EngineConnectionConfig | None,
        execution_result: ExecuteSqlResponse | list[ExecuteSqlResponse] | None,
        connections_by_name: dict[str, EngineConnectionConfig] | None = None,
    ) -> None:
        self._selected_connector = selected_connector
        self._connection = connection
        self._execution_result = execution_result
        self._connections_by_name = connections_by_name or {}
        self.connection_requests: list[tuple[str, str]] = []
        self.sql_calls: list[tuple[str, str]] = []

    @staticmethod
    def is_non_analytic_question(_question: str) -> bool:
        return False

    def select_connector(
        self,
        request: ChatAskRequest,
        llm_payload: dict,
        db_connectors: list[ConnectorItem],
    ) -> ConnectorItem | None:
        return self._selected_connector

    def get_connection_for_execution(
        self,
        workspace_id: str,
        connector_name: str,
    ) -> EngineConnectionConfig | None:
        self.connection_requests.append((workspace_id, connector_name))
        if connector_name in self._connections_by_name:
            return self._connections_by_name[connector_name]
        return self._connection

    def execute_sql(
        self,
        sql: str,
        connection: EngineConnectionConfig,
        workspace_id: str,
        dialect: str,
    ) -> ExecuteSqlResponse:
        assert sql
        assert workspace_id
        assert connection.engine == dialect
        self.sql_calls.append((dialect, sql))
        assert self._execution_result is not None
        if isinstance(self._execution_result, list):
            assert self._execution_result
            return self._execution_result.pop(0)
        return self._execution_result


class _RagRetrieverStub:
    def __init__(self, results: list[dict], warnings: list[str] | None = None) -> None:
        self._results = results
        self._warnings = warnings or []

    def search(
        self,
        workspace_id: str,
        query: str,
        limit: int,
        rag_connector_name: str | None = None,
    ) -> tuple[list[dict], list[str]]:
        assert workspace_id
        assert query
        assert limit >= 1
        _ = rag_connector_name
        return self._results, self._warnings


class _ConfluenceRepoStub:
    def __init__(self, connection: ConfluenceConnectionConfig | None) -> None:
        self._connection = connection
        self.calls: list[tuple[str, str | None]] = []

    def get_connection(
        self, workspace_id: str, name: str | None = None
    ) -> ConfluenceConnectionConfig | None:
        self.calls.append((workspace_id, name))
        return self._connection


class _JiraRepoStub:
    def __init__(self, connection: JiraConnectionConfig | None) -> None:
        self._connection = connection
        self.calls: list[tuple[str, str | None]] = []

    def get_connection(
        self, workspace_id: str, name: str | None = None
    ) -> JiraConnectionConfig | None:
        self.calls.append((workspace_id, name))
        return self._connection


class _JiraServiceStub:
    def __init__(
        self,
        *,
        search_results: list[dict[str, object]] | None = None,
        issue_result: dict[str, object] | None = None,
        users_by_query: dict[tuple[str, str | None], list[dict[str, object]]] | None = None,
        create_meta: dict[str, object] | None = None,
        create_result: dict[str, object] | None = None,
    ) -> None:
        self._search_results = search_results or []
        self._issue_result = issue_result or {}
        self._users_by_query = users_by_query or {}
        self._create_meta = create_meta or {}
        self._create_result = create_result or {}
        self.search_calls: list[dict[str, object]] = []
        self.get_issue_calls: list[str] = []
        self.create_calls: list[dict[str, object]] = []

    def search_issues(
        self,
        connection: JiraConnectionConfig,
        *,
        jql: str,
        limit: int,
        fields: list[str] | None = None,
    ) -> list[dict[str, object]]:
        _ = connection, fields
        self.search_calls.append({"jql": jql, "limit": limit})
        return list(self._search_results)

    def get_issue(
        self,
        connection: JiraConnectionConfig,
        *,
        issue_key: str,
        fields: list[str] | None = None,
    ) -> dict[str, object]:
        _ = connection, fields
        self.get_issue_calls.append(issue_key)
        return dict(self._issue_result)

    def search_users(
        self,
        connection: JiraConnectionConfig,
        *,
        query: str,
        limit: int,
    ) -> list[dict[str, object]]:
        _ = connection, limit
        return list(self._users_by_query.get((query, None), []))

    def search_assignable_users(
        self,
        connection: JiraConnectionConfig,
        *,
        project_key: str,
        query: str,
        limit: int,
    ) -> list[dict[str, object]]:
        _ = connection, limit
        return list(self._users_by_query.get((query, project_key), []))

    def get_create_meta(
        self,
        connection: JiraConnectionConfig,
        *,
        project_key: str,
    ) -> dict[str, object]:
        _ = connection, project_key
        return dict(self._create_meta)

    def create_issue(
        self,
        connection: JiraConnectionConfig,
        *,
        fields: dict[str, object],
    ) -> dict[str, object]:
        _ = connection
        self.create_calls.append(dict(fields))
        return dict(self._create_result)


class _MetadataResolverStub:
    def __init__(self, connection: object | None) -> None:
        self._connection = connection

    def resolve_connection(
        self,
        workspace_id: str,
        requested_name: str | None,
        fallback_name: str | None,
        warnings: list[str],
    ) -> object | None:
        _ = workspace_id, requested_name, fallback_name, warnings
        return self._connection


class _MetadataResolverByNameStub:
    def __init__(
        self,
        *,
        default_connection: object | None = None,
        connections_by_name: dict[str, object] | None = None,
    ) -> None:
        self._default_connection = default_connection
        self._connections_by_name = connections_by_name or {}
        self.calls: list[dict[str, object | None]] = []

    def resolve_connection(
        self,
        workspace_id: str,
        requested_name: str | None,
        fallback_name: str | None,
        warnings: list[str],
    ) -> object | None:
        _ = warnings
        self.calls.append({
            "workspace_id": workspace_id,
            "requested_name": requested_name,
            "fallback_name": fallback_name,
        })
        if requested_name is not None:
            connection = self._connections_by_name.get(requested_name)
            if connection is not None:
                return connection
        if fallback_name and fallback_name != requested_name:
            fallback = self._connections_by_name.get(fallback_name)
            if fallback is not None:
                return fallback
        return self._default_connection


class _MetadataFetcherStub:
    def __init__(
        self,
        *,
        search_results: list[dict] | None = None,
        tables: list[TableContext] | None = None,
        entity_result: dict | None = None,
        entity_results: dict[str, dict] | None = None,
        lineage_result: dict | None = None,
    ) -> None:
        self._search_results = search_results or []
        self._tables = tables or []
        self._entity_result = entity_result
        self._entity_results = {
            key.strip().lower(): value for key, value in (entity_results or {}).items()
        }
        self._lineage_result = lineage_result
        self.search_calls: list[dict[str, object]] = []
        self.fetch_calls: list[dict[str, object]] = []
        self.entity_calls: list[dict[str, object]] = []

    def search_catalog(
        self,
        connection: object,
        query: str,
        entity_type: str | None = None,
        limit: int = 10,
        offset: int = 0,
        *,
        service: str | None = None,
        database: str | None = None,
        schema: str | None = None,
    ) -> tuple[list[dict], list[str]]:
        self.search_calls.append({
            "connection": connection,
            "query": query,
            "entity_type": entity_type,
            "limit": limit,
            "offset": offset,
            "service": service,
            "database": database,
            "schema": schema,
        })
        return self._search_results, []

    def fetch_tables(
        self,
        connection: object,
        workspace_id: str | None = None,
        connector_name: str | None = None,
    ) -> tuple[list[TableContext], list[str]]:
        self.fetch_calls.append({
            "connection": connection,
            "workspace_id": workspace_id,
            "connector_name": connector_name,
        })
        return self._tables, []

    def filter_tables(
        self,
        tables: list[TableContext],
        search: str | None,
        service: str | None,
        database: str | None,
        schema: str | None,
        *,
        workspace_id: str | None = None,
        connector_name: str | None = None,
        top_n: int = 20,
        preferred_service: str | None = None,
        preferred_database: str | None = None,
        preferred_schema: str | None = None,
    ) -> list[TableContext]:
        _ = (
            service,
            database,
            schema,
            workspace_id,
            connector_name,
            preferred_service,
            preferred_database,
            preferred_schema,
        )
        if not search:
            return tables[:top_n]
        normalized = search.lower()
        return [
            table for table in tables if table.fqn.lower().split(".")[-1].startswith(normalized)
        ][:top_n]

    def get_entity_details(
        self,
        connection: object,
        entity_type: str,
        fqn: str,
    ) -> tuple[dict | None, list[str]]:
        self.entity_calls.append({
            "connection": connection,
            "entity_type": entity_type,
            "fqn": fqn,
        })
        _ = connection, entity_type
        result = self._entity_results.get(fqn.strip().lower(), self._entity_result)
        return result, []

    def get_entity_lineage(
        self,
        connection: object,
        entity_type: str,
        fqn: str,
        upstream_depth: int = 2,
        downstream_depth: int = 2,
    ) -> tuple[dict | None, list[str]]:
        _ = connection, entity_type, fqn, upstream_depth, downstream_depth
        return self._lineage_result, []


class _SqlOptimizationServiceStub:
    def should_optimize(self, **_kwargs) -> bool:
        return True

    def optimize(self, **kwargs) -> SqlOptimizationOutcome:
        sql = str(kwargs["sql"])
        return SqlOptimizationOutcome(
            sql_for_execution=sql,
            trace=OptimizationTrace(
                optimizer_used=False,
                input_sql=sql,
                optimized_sql_draft=sql,
                applied=False,
            ),
        )


def test_run_sql_returns_missing_when_execution_connection_is_unavailable() -> None:
    connector = _connector()
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=None,
        execution_result=None,
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
        sql_optimization_service=_SqlOptimizationServiceStub(),
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "SELECT 1"},
        request=ChatAskRequest(question="Покажи продажи", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is False
    assert result["error"] == "database_connector_missing"
    assert result["error_code"] == "database_connector_missing"
    assert result["error_category"] == "RECOVERABLE"
    assert result["can_continue"] is True
    assert state.warnings == ["database_connector_missing"]
    assert router.connection_requests == [("default", connector.name)]


def test_search_metadata_catalog_falls_back_to_table_lookup_for_prefix_query() -> None:
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
    )
    metadata_fetcher = _MetadataFetcherStub(
        search_results=[],
        tables=[
            TableContext(
                source="openmetadata",
                fqn="Sample-ClickHouse.default.dm.ft_main_whs_partner_date",
                columns=["order_create_date", "whs_code"],
                service="Sample-ClickHouse",
                database="default",
                schema="dm",
            )
        ],
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(None, None, None),
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="search_metadata_catalog",
        arguments={"query": "ft_main", "entity_type": "table", "limit": 5},
        request=ChatAskRequest(question="есть витрина с именем начинающимся с ft_main?"),
        db_connectors=[],
        state=state,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["results"][0]["fullyQualifiedName"] == (
        "Sample-ClickHouse.default.dm.ft_main_whs_partner_date"
    )
    assert state.context_tables == ["Sample-ClickHouse.default.dm.ft_main_whs_partner_date"]
    assert state.metadata_table_columns == {
        "sample-clickhouse.default.dm.ft_main_whs_partner_date": {
            "order_create_date",
            "whs_code",
        }
    }


def test_search_metadata_catalog_ignores_unavailable_table_results_before_runtime_lookup() -> None:
    connector = _connector(name="clickhouse")
    connector.connection.extra_settings = {
        "metadata_service": "Sample-ClickHouse",
        "metadata_database": "default",
    }
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        service_name="Sample-ClickHouse",
        database_name="default",
        schema_name=None,
    )
    runtime_connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="default",
        extra_settings={
            "metadata_service": "Sample-ClickHouse",
            "metadata_database": "default",
        },
    )
    metadata_fetcher = _MetadataFetcherStub(
        search_results=[
            {
                "entityType": "table",
                "fullyQualifiedName": "greenplum-analytics-prod.default.dm.ft_order",
                "service": "greenplum-analytics-prod",
                "database": "default",
                "databaseSchema": "dm",
                "columnNames": ["order_id"],
            }
        ],
        tables=[],
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=runtime_connection,
        execution_result=[
            _execution_result(
                columns=["database", "name"],
                rows=[["dm", "ft_order"]],
            ),
            _execution_result(
                columns=["name", "type"],
                rows=[["order_id", "UInt64"], ["order_create_dttm", "DateTime"]],
            ),
        ],
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=router,
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="search_metadata_catalog",
        arguments={"query": "ft_order", "entity_type": "table", "limit": 5},
        request=ChatAskRequest(question="что за таблица ft_order", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["results"][0]["fullyQualifiedName"] == "Sample-ClickHouse.default.dm.ft_order"
    assert state.context_tables == ["Sample-ClickHouse.default.dm.ft_order"]
    assert "metadata_runtime_lookup_used" in state.warnings


def test_search_metadata_catalog_passes_explicit_scope_filters() -> None:
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
    )
    metadata_fetcher = _MetadataFetcherStub(
        search_results=[
            {
                "entityType": "table",
                "fullyQualifiedName": "Sample-ClickHouse.default.dict.city",
                "databaseSchema": "dict",
                "columnNames": ["city_id", "city_name"],
            }
        ]
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(None, None, None),
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="search_metadata_catalog",
        arguments={
            "query": "city",
            "entity_type": "table",
            "schema": "dict",
            "limit": 5,
        },
        request=ChatAskRequest(question="что такое dict.city", workspace_id="default"),
        db_connectors=[],
        state=state,
    )

    assert result["ok"] is True
    assert result["results"][0]["fullyQualifiedName"] == "Sample-ClickHouse.default.dict.city"
    assert metadata_fetcher.search_calls == [
        {
            "connection": connection,
            "query": "city",
            "entity_type": "table",
            "limit": 5,
            "offset": 0,
            "service": None,
            "database": None,
            "schema": "dict",
        }
    ]


def test_search_metadata_catalog_reports_unconfirmed_store_revenue_gap() -> None:
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
    )
    metadata_fetcher = _MetadataFetcherStub(
        search_results=[
            {
                "entityType": "table",
                "fullyQualifiedName": "Sample-ClickHouse.default.dm.ft_revenue_city",
                "databaseSchema": "dm",
                "columnNames": ["city", "net_revenue"],
            },
            {
                "entityType": "table",
                "fullyQualifiedName": "Sample-ClickHouse.default.dm.warehouse",
                "databaseSchema": "dm",
                "columnNames": ["whs_code", "city"],
            },
        ]
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(None, None, None),
    )

    result = executor.execute_tool(
        tool_name="search_metadata_catalog",
        arguments={"query": "магазин revenue выручка", "entity_type": "table", "limit": 10},
        request=ChatAskRequest(question="Какая выручка у магазинов в Туле?"),
        db_connectors=[],
        state=ToolOrchestrationState(),
    )

    assert result["ok"] is True
    assert result["discovery"]["new_evidence"] is True
    assert result["discovery"]["repeat_same_search_will_add_evidence"] is False
    assert result["discovery"]["evidence_gaps"] == ["store_level_revenue_source_not_confirmed"]
    assert "ask_clarification" in result["discovery"]["next_allowed_actions"]


def test_search_metadata_catalog_defaults_identifier_lookup_limit_to_20() -> None:
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
    )
    metadata_fetcher = _MetadataFetcherStub(search_results=[])
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(None, None, None),
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="search_metadata_catalog",
        arguments={"query": "art_price", "entity_type": "table"},
        request=ChatAskRequest(question="что такое art_price", workspace_id="default"),
        db_connectors=[],
        state=state,
    )

    assert result["ok"] is True
    assert metadata_fetcher.search_calls[0]["limit"] == 20


def test_search_metadata_catalog_respects_explicit_smaller_limit() -> None:
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
    )
    metadata_fetcher = _MetadataFetcherStub(search_results=[])
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(None, None, None),
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="search_metadata_catalog",
        arguments={"query": "art_price", "entity_type": "table", "limit": 10},
        request=ChatAskRequest(question="что такое art_price", workspace_id="default"),
        db_connectors=[],
        state=state,
    )

    assert result["ok"] is True
    assert metadata_fetcher.search_calls[0]["limit"] == 10


def test_fetch_metadata_tables_falls_back_to_catalog_when_snapshot_misses_table() -> None:
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
    )
    metadata_fetcher = _MetadataFetcherStub(
        tables=[],
        search_results=[
            {
                "entityType": "table",
                "fullyQualifiedName": "Sample-ClickHouse.default.dm.ft_main_whs_partner_date",
                "databaseSchema": "dm",
                "columnNames": ["order_create_date", "whs_code"],
            }
        ],
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(None, None, None),
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="fetch_metadata_tables",
        arguments={"search": "ft_main", "limit": 5},
        request=ChatAskRequest(question="есть витрина с именем начинающимся с ft_main?"),
        db_connectors=[],
        state=state,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["tables"][0]["fqn"] == "Sample-ClickHouse.default.dm.ft_main_whs_partner_date"
    assert state.context_tables == ["Sample-ClickHouse.default.dm.ft_main_whs_partner_date"]


def test_fetch_metadata_tables_falls_back_to_runtime_lookup_when_metadata_misses_exact_table() -> (
    None
):
    connector = _connector(name="clickhouse")
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        service_name="Sample-ClickHouse",
        database_name="default",
        schema_name=None,
    )
    runtime_connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="default",
        extra_settings={
            "metadata_service": "Sample-ClickHouse",
            "metadata_database": "default",
        },
    )
    metadata_fetcher = _MetadataFetcherStub(tables=[], search_results=[])
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=runtime_connection,
        execution_result=[
            _execution_result(
                columns=["database", "name"],
                rows=[["dm", "ft_order"]],
            ),
            _execution_result(
                columns=["name", "type"],
                rows=[["order_id", "String"], ["sale_amt", "Decimal(18,2)"]],
            ),
        ],
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=router,
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="fetch_metadata_tables",
        arguments={"search": "ft_order", "limit": 5},
        request=ChatAskRequest(question="что за таблица ft_order", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["tables"][0]["fqn"] == "Sample-ClickHouse.default.dm.ft_order"
    assert result["tables"][0]["columns"][0]["name"] == "order_id"
    assert state.context_tables == ["Sample-ClickHouse.default.dm.ft_order"]
    assert state.metadata_table_columns == {
        "sample-clickhouse.default.dm.ft_order": {"order_id", "sale_amt"}
    }
    assert "metadata_runtime_lookup_used" in state.warnings


def test_fetch_metadata_tables_treats_db_connector_name_as_scope_not_metadata_connector() -> None:
    connector = _connector_with_mapping(
        name="clickhouse",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="default",
        metadata_schema="dm",
    )
    metadata_connection = SimpleNamespace(
        mcp_tables_url=None,
        api_token=None,
        verify_tls=True,
        service_name=None,
        database_name=None,
        schema_name=None,
    )
    resolver = _MetadataResolverByNameStub(
        default_connection=metadata_connection,
        connections_by_name={"openmetadata": metadata_connection},
    )
    metadata_fetcher = _MetadataFetcherStub(
        tables=[
            TableContext(
                source="openmetadata",
                fqn="Sample-ClickHouse.default.dm.ft_order",
                columns=["order_id", "order_source"],
                service="Sample-ClickHouse",
                database="default",
                schema="dm",
            )
        ]
    )
    executor = ToolExecutor(
        metadata_resolver=resolver,
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(None, None, None),
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="fetch_metadata_tables",
        arguments={"connector_name": "clickhouse", "search": "ft_order", "limit": 5},
        request=ChatAskRequest(
            question="что за таблица ft_order",
            workspace_id="default",
            metadata_connector_name="openmetadata",
        ),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert resolver.calls[0]["requested_name"] == "openmetadata"
    assert metadata_fetcher.fetch_calls[0]["connector_name"] == "openmetadata"
    effective_connection = metadata_fetcher.fetch_calls[0]["connection"]
    assert effective_connection.service_name == "Sample-ClickHouse"
    assert effective_connection.database_name == "default"
    assert effective_connection.schema_name == "dm"
    assert "metadata_connector_missing" not in state.warnings


def test_search_then_fetch_metadata_preserves_governed_art_price_grounding_over_runtime_fallback() -> (
    None
):
    governed_fqn = "greenplum-analytics-prod.dwh.dm.art_price"
    connector = _connector_with_mapping(
        name="clickhouse",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="default",
        metadata_schema="sandbox",
    )
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        service_name="Sample-ClickHouse",
        database_name="default",
        schema_name=None,
    )
    runtime_connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="default",
        extra_settings={
            "metadata_service": "Sample-ClickHouse",
            "metadata_database": "default",
            "metadata_schema": "sandbox",
        },
    )
    metadata_fetcher = _MetadataFetcherStub(
        search_results=[
            {
                "entityType": "table",
                "fullyQualifiedName": governed_fqn,
                "service": "greenplum-analytics-prod",
                "database": "dwh",
                "databaseSchema": "dm",
                "columnNames": ["art_id", "price_amt"],
                "owners": ["pricing"],
                "tier": "Tier.Tier1",
                "description": "Governed art price mart",
                "relevance_score": 96.0,
                "matched_on": ["name_exact", "business_schema"],
            },
            {
                "entityType": "table",
                "fullyQualifiedName": "DWH-iceberg.default.dm_ft.art_price",
                "service": "DWH-iceberg",
                "database": "default",
                "databaseSchema": "dm_ft",
                "columnNames": ["art_id", "price_amt"],
                "description": "Iceberg art price table",
                "relevance_score": 91.0,
                "matched_on": ["name_exact"],
            },
        ],
        tables=[],
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=runtime_connection,
        execution_result=[
            _execution_result(
                columns=["database", "name"],
                rows=[["sandbox", "sat_whs_art_price_type"]],
            ),
            _execution_result(
                columns=["name", "type"],
                rows=[["art_id", "UInt64"], ["price_type", "String"]],
            ),
            _execution_result(
                columns=["database", "name"],
                rows=[["sandbox", "sat_whs_art_price_type"]],
            ),
            _execution_result(
                columns=["name", "type"],
                rows=[["art_id", "UInt64"], ["price_type", "String"]],
            ),
        ],
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=router,
    )
    state = ToolOrchestrationState()

    search_result = executor.execute_tool(
        tool_name="search_metadata_catalog",
        arguments={"query": "art_price", "entity_type": "table", "limit": 5},
        request=ChatAskRequest(question="что такое art_price", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert search_result["ok"] is True
    assert search_result["results"][0]["fullyQualifiedName"] == governed_fqn
    assert state.semantic_grounding is not None
    # Multiple candidates without explicit selection: chosen_table_fqn is None
    # but candidates are ranked with governed one first
    assert state.semantic_grounding.chosen_table_fqn is None
    assert state.semantic_grounding.candidates[0].fqn == governed_fqn
    assert state.semantic_grounding.candidates[0].suspicious is False
    assert state.semantic_grounding.explicit_choice is False

    fetch_result = executor.execute_tool(
        tool_name="fetch_metadata_tables",
        arguments={"search": "dm.art_price", "limit": 5},
        request=ChatAskRequest(question="что такое art_price", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert fetch_result["ok"] is True
    assert fetch_result["tables"][0]["fqn"] == governed_fqn
    assert state.semantic_grounding is not None
    # Still None since no explicit selection was made
    assert state.semantic_grounding.chosen_table_fqn is None
    assert state.semantic_grounding.candidates[0].fqn == governed_fqn
    assert state.warnings.count("metadata_runtime_lookup_used") == 2


def test_fetch_metadata_tables_returns_full_columns_for_small_identifier_lookup() -> None:
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
    )
    metadata_fetcher = _MetadataFetcherStub(
        tables=[
            TableContext(
                source="openmetadata",
                fqn="Sample-ClickHouse.default.dm.ft_order_unit_economics_v",
                columns=[*[f"col_{index}" for index in range(20)], "cm2_amt", "load_dttm"],
                service="Sample-ClickHouse",
                database="default",
                schema="dm",
            )
        ]
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(None, None, None),
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="fetch_metadata_tables",
        arguments={"search": "ft_order_unit_economics_v", "limit": 5},
        request=ChatAskRequest(question="есть ли cm2_amt в ft_order_unit_economics_v"),
        db_connectors=[],
        state=state,
    )

    assert result["ok"] is True
    assert [column["name"] for column in result["tables"][0]["columns"]][-2:] == [
        "cm2_amt",
        "load_dttm",
    ]


def test_fetch_metadata_tables_ignores_unavailable_results_before_runtime_lookup() -> None:
    connector = _connector(name="clickhouse")
    connector.connection.extra_settings = {
        "metadata_service": "Sample-ClickHouse",
        "metadata_database": "default",
    }
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        service_name="Sample-ClickHouse",
        database_name="default",
        schema_name=None,
    )
    runtime_connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="default",
        extra_settings={
            "metadata_service": "Sample-ClickHouse",
            "metadata_database": "default",
        },
    )
    metadata_fetcher = _MetadataFetcherStub(
        tables=[
            TableContext(
                source="openmetadata",
                fqn="greenplum-analytics-prod.default.dm.ft_order",
                columns=["order_id"],
                service="greenplum-analytics-prod",
                database="default",
                schema="dm",
            )
        ],
        search_results=[],
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=runtime_connection,
        execution_result=[
            _execution_result(
                columns=["database", "name"],
                rows=[["dm", "ft_order"]],
            ),
            _execution_result(
                columns=["name", "type"],
                rows=[["order_id", "UInt64"], ["order_create_dttm", "DateTime"]],
            ),
        ],
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=router,
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="fetch_metadata_tables",
        arguments={"search": "ft_order", "limit": 5},
        request=ChatAskRequest(question="что за таблица ft_order", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["tables"][0]["fqn"] == "Sample-ClickHouse.default.dm.ft_order"
    assert state.context_tables == ["Sample-ClickHouse.default.dm.ft_order"]
    assert "metadata_runtime_lookup_used" in state.warnings


def test_fetch_metadata_tables_does_not_reintroduce_unavailable_semantic_table() -> None:
    connector = _connector_with_mapping(
        name="trino-adhoc",
        engine="trino",
        metadata_service="DWH-iceberrg",
        metadata_database="iceberg",
        metadata_schema="dm_ft",
    )
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        service_name="DWH-iceberrg",
        database_name="iceberg",
        schema_name="dm_ft",
    )
    metadata_fetcher = _MetadataFetcherStub(
        tables=[
            TableContext(
                source="openmetadata",
                fqn="trino_omni.iceberg.dm_ft.ft_order",
                columns=["order_id"],
                service="trino_omni",
                database="iceberg",
                schema="dm_ft",
            ),
            TableContext(
                source="openmetadata",
                fqn="DWH-iceberrg.iceberg.dm_ft.ft_order",
                columns=["order_id"],
                service="DWH-iceberrg",
                database="iceberg",
                schema="dm_ft",
            ),
        ]
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(
            connector, connector.connection, _execution_result(engine="trino")
        ),
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="fetch_metadata_tables",
        arguments={"search": "ft_order", "limit": 5},
        request=ChatAskRequest(question="что за таблица ft_order", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["tables"][0]["fqn"] == "DWH-iceberrg.iceberg.dm_ft.ft_order"
    assert state.context_tables == ["DWH-iceberrg.iceberg.dm_ft.ft_order"]


def test_fetch_metadata_tables_unscopes_default_metadata_connection_for_multi_connector_workspace() -> (
    None
):
    clickhouse_connector = _connector_with_mapping(
        name="clickhouse",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="default",
        metadata_schema="dm",
    )
    trino_connector = _connector_with_mapping(
        name="trino-adhoc",
        engine="trino",
        metadata_service="trino_omni",
        metadata_database="iceberg",
        metadata_schema="dm_ft",
    )
    metadata_connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        service_name="Sample-ClickHouse",
        database_name="default",
        schema_name="dm",
    )
    metadata_fetcher = _MetadataFetcherStub(
        tables=[
            TableContext(
                source="openmetadata",
                fqn="trino_omni.iceberg.dm_ft.ft_order",
                columns=["order_id"],
                service="trino_omni",
                database="iceberg",
                schema="dm_ft",
            )
        ]
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(metadata_connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(
            None,
            None,
            _execution_result(engine="trino"),
        ),
    )

    result = executor.execute_tool(
        tool_name="fetch_metadata_tables",
        arguments={"search": "ft_order", "limit": 5},
        request=ChatAskRequest(question="что за таблица ft_order", workspace_id="default"),
        db_connectors=[clickhouse_connector, trino_connector],
        state=ToolOrchestrationState(),
    )

    assert result["ok"] is True
    assert metadata_fetcher.fetch_calls[0]["connection"].service_name is None
    assert result["tables"][0]["fqn"] == "trino_omni.iceberg.dm_ft.ft_order"


def test_search_metadata_catalog_scopes_default_metadata_connection_to_selected_connector() -> None:
    clickhouse_connector = _connector_with_mapping(
        name="clickhouse",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="default",
        metadata_schema="dm",
    )
    trino_connector = _connector_with_mapping(
        name="trino-adhoc",
        engine="trino",
        metadata_service="trino_omni",
        metadata_database="iceberg",
        metadata_schema="dm_ft",
    )
    metadata_connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        service_name="Sample-ClickHouse",
        database_name="default",
        schema_name="dm",
    )
    metadata_fetcher = _MetadataFetcherStub(
        search_results=[
            {
                "entityType": "table",
                "fullyQualifiedName": "trino_omni.iceberg.dm_ft.ft_order",
                "columnNames": ["order_id"],
                "service": "trino_omni",
                "database": "iceberg",
                "databaseSchema": "dm_ft",
            }
        ]
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(metadata_connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(None, None, _execution_result(engine="trino")),
    )
    state = ToolOrchestrationState(selected_database_connector="trino-adhoc")

    result = executor.execute_tool(
        tool_name="search_metadata_catalog",
        arguments={"query": "ft_order", "entity_type": "table"},
        request=ChatAskRequest(question="найди ft_order", workspace_id="default"),
        db_connectors=[clickhouse_connector, trino_connector],
        state=state,
    )

    assert result["ok"] is True
    assert metadata_fetcher.search_calls[0]["connection"].service_name == "trino_omni"
    assert metadata_fetcher.search_calls[0]["connection"].database_name == "iceberg"
    assert metadata_fetcher.search_calls[0]["connection"].schema_name == "dm_ft"


def test_fetch_metadata_tables_without_search_does_not_replace_existing_context() -> None:
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
    )
    metadata_fetcher = _MetadataFetcherStub(
        tables=[
            TableContext(
                source="openmetadata",
                fqn="Sample-ClickHouse.default.dm.ft_main_whs_partner_date",
                columns=["order_create_date", "whs_code"],
                service="Sample-ClickHouse",
                database="default",
                schema="dm",
            )
        ],
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(None, None, None),
    )
    state = ToolOrchestrationState()
    state.context_tables = ["Sample-ClickHouse.default.dm.ft_order"]
    state.metadata_table_columns = {"sample-clickhouse.default.dm.ft_order": {"order_id", "gmv"}}

    result = executor.execute_tool(
        tool_name="fetch_metadata_tables",
        arguments={"limit": 100},
        request=ChatAskRequest(question="покажи order tables", workspace_id="default"),
        db_connectors=[],
        state=state,
    )

    assert result["ok"] is True
    assert state.context_tables == ["Sample-ClickHouse.default.dm.ft_order"]
    assert state.metadata_table_columns == {
        "sample-clickhouse.default.dm.ft_order": {"order_id", "gmv"}
    }


def test_fetch_metadata_tables_runtime_lookup_falls_back_to_partial_match() -> None:
    connector = _connector(name="clickhouse")
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        service_name="Sample-ClickHouse",
        database_name="default",
        schema_name=None,
    )
    runtime_connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="default",
        extra_settings={
            "metadata_service": "Sample-ClickHouse",
            "metadata_database": "default",
        },
    )
    metadata_fetcher = _MetadataFetcherStub(tables=[], search_results=[])
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=runtime_connection,
        execution_result=[
            _execution_result(columns=["database", "name"], rows=[]),
            _execution_result(
                columns=["database", "name"],
                rows=[["dm", "prvrd_dau_main_metrics"]],
            ),
            _execution_result(
                columns=["name", "type"],
                rows=[["ptn_dt", "Date"], ["customer_id", "String"]],
            ),
        ],
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=router,
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="fetch_metadata_tables",
        arguments={"search": "dau", "limit": 5},
        request=ChatAskRequest(question="нужен dau", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["tables"][0]["fqn"] == "Sample-ClickHouse.default.dm.prvrd_dau_main_metrics"
    assert state.context_tables == ["Sample-ClickHouse.default.dm.prvrd_dau_main_metrics"]
    assert state.metadata_table_columns == {
        "sample-clickhouse.default.dm.prvrd_dau_main_metrics": {"ptn_dt", "customer_id"}
    }
    assert "metadata_runtime_lookup_used" in state.warnings


def test_fetch_metadata_tables_keeps_existing_governed_grounding_when_runtime_match_is_suspicious() -> (
    None
):
    governed_fqn = "greenplum-analytics-prod.dwh.dm.art_price"
    runtime_fqn = "Sample-ClickHouse.default.sandbox.sat_whs_art_price_type"
    connector = _connector_with_mapping(
        name="clickhouse",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="default",
        metadata_schema="sandbox",
    )
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        service_name="Sample-ClickHouse",
        database_name="default",
        schema_name=None,
    )
    runtime_connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="default",
        extra_settings={
            "metadata_service": "Sample-ClickHouse",
            "metadata_database": "default",
            "metadata_schema": "sandbox",
        },
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=runtime_connection,
        execution_result=[
            _execution_result(
                columns=["database", "name"],
                rows=[["sandbox", "sat_whs_art_price_type"]],
            ),
            _execution_result(
                columns=["name", "type"],
                rows=[["art_id", "UInt64"], ["price_type", "String"]],
            ),
        ],
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=_MetadataFetcherStub(tables=[], search_results=[]),
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.semantic_grounding = SemanticGrounding(
        chosen_table_fqn=governed_fqn,
        candidates=[
            {
                "fqn": governed_fqn,
                "service": "greenplum-analytics-prod",
                "database": "dwh",
                "schema": "dm",
                "relevance_score": 96.0,
                "matched_on": ["name_exact", "business_schema"],
                "reason_codes": [
                    "governance_strength",
                    "business_layer_preference",
                ],
                "owners": ["pricing"],
                "usage_count": 12,
                "suspicious": False,
            }
        ],
        selection_rationale="Selected governed art_price table.",
    )

    result = executor.execute_tool(
        tool_name="fetch_metadata_tables",
        arguments={"search": "art_price", "limit": 5},
        request=ChatAskRequest(question="что такое art_price", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["tables"][0]["fqn"] == runtime_fqn
    assert state.semantic_grounding is not None
    assert state.semantic_grounding.chosen_table_fqn == governed_fqn
    assert state.semantic_grounding.candidates[0].fqn == governed_fqn
    assert state.semantic_grounding.candidates[0].relevance_score == 96.0


def test_search_metadata_catalog_hydrates_context_for_uppercase_entity_type() -> None:
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
    )
    metadata_fetcher = _MetadataFetcherStub(
        search_results=[
            {
                "entityType": "TABLE",
                "fullyQualifiedName": "Sample-ClickHouse.default.dm.ft_main_whs_partner_date",
                "columnNames": ["order_create_date", "whs_code"],
            }
        ]
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(None, None, None),
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="search_metadata_catalog",
        arguments={"query": "ft_main_whs_partner_date", "limit": 5},
        request=ChatAskRequest(question="дай таблицу ft_main_whs_partner_date"),
        db_connectors=[],
        state=state,
    )

    assert result["ok"] is True
    assert state.context_tables == ["Sample-ClickHouse.default.dm.ft_main_whs_partner_date"]


def test_search_metadata_catalog_does_not_hydrate_unavailable_table_into_sql_context() -> None:
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
    )
    metadata_fetcher = _MetadataFetcherStub(
        search_results=[
            {
                "entityType": "table",
                "fullyQualifiedName": "greenplum-analytics-prod.default.sandbox.ft_main_whs_partner_date",
                "columnNames": ["order_create_date", "whs_code"],
            }
        ]
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(None, None, None),
    )
    state = ToolOrchestrationState()
    clickhouse = _connector_with_mapping(
        name="clickhouse",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="default",
        metadata_schema="dm",
    )

    result = executor.execute_tool(
        tool_name="search_metadata_catalog",
        arguments={"query": "ft_main_whs_partner_date", "limit": 5},
        request=ChatAskRequest(question="дай таблицу ft_main_whs_partner_date"),
        db_connectors=[clickhouse],
        state=state,
    )

    assert result["ok"] is True
    assert state.context_tables == []


def test_search_metadata_catalog_hydrates_only_strong_table_candidates() -> None:
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
    )
    metadata_fetcher = _MetadataFetcherStub(
        search_results=[
            {
                "entityType": "table",
                "fullyQualifiedName": "Sample-ClickHouse.default.dm.ft_order",
                "columnNames": ["order_id"],
                "relevance_score": 88.0,
            },
            {
                "entityType": "table",
                "fullyQualifiedName": "Sample-ClickHouse.default.dm.ft_order_bitrix",
                "columnNames": ["order_id"],
                "relevance_score": 22.0,
            },
        ]
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(None, None, None),
    )
    state = ToolOrchestrationState()
    state.context_tables = [f"legacy.table_{index}" for index in range(10)]

    result = executor.execute_tool(
        tool_name="search_metadata_catalog",
        arguments={"query": "orders", "limit": 5},
        request=ChatAskRequest(question="найди таблицу заказов"),
        db_connectors=[],
        state=state,
    )

    assert result["ok"] is True
    assert state.context_tables[0] == "Sample-ClickHouse.default.dm.ft_order"
    assert "Sample-ClickHouse.default.dm.ft_order_bitrix" not in state.context_tables
    assert len(state.context_tables) == 8
    assert state.semantic_grounding is not None
    # Multiple candidates without explicit selection: chosen_table_fqn is None
    # but candidates are ranked with governed one first
    assert state.semantic_grounding.chosen_table_fqn is None
    assert state.semantic_grounding.candidates[0].fqn == "Sample-ClickHouse.default.dm.ft_order"
    assert state.semantic_grounding.explicit_choice is False


def test_get_metadata_entity_details_hydrates_table_context() -> None:
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
    )
    metadata_fetcher = _MetadataFetcherStub(
        entity_result={
            "entity": {
                "fullyQualifiedName": "Sample-ClickHouse.default.dm.ft_main_whs_partner_date",
                "columns": [
                    {"name": "order_create_date", "dataType": "DATE"},
                    {"name": "whs_code", "dataType": "STRING"},
                ],
                "service": {"name": "Sample-ClickHouse"},
                "database": {"name": "default"},
                "databaseSchema": {"name": "dm"},
            }
        }
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(None, None, None),
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="get_metadata_entity_details",
        arguments={
            "entity_type": "table",
            "fqn": "Sample-ClickHouse.default.dm.ft_main_whs_partner_date",
        },
        request=ChatAskRequest(question="dm.ft_main_whs_partner_date такое полное имя"),
        db_connectors=[],
        state=state,
    )

    assert result["ok"] is True
    assert state.context_tables == ["Sample-ClickHouse.default.dm.ft_main_whs_partner_date"]
    assert state.metadata_table_columns == {
        "sample-clickhouse.default.dm.ft_main_whs_partner_date": {
            "order_create_date",
            "whs_code",
        }
    }
    snapshot = state.metadata_table_schemas["sample-clickhouse.default.dm.ft_main_whs_partner_date"]
    assert snapshot.complete is True
    assert snapshot.source == "entity_details"
    assert state.semantic_grounding is not None
    assert (
        state.semantic_grounding.chosen_table_fqn
        == "Sample-ClickHouse.default.dm.ft_main_whs_partner_date"
    )


def test_get_metadata_entity_lineage_updates_semantic_grounding_summary() -> None:
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
    )
    metadata_fetcher = _MetadataFetcherStub(
        lineage_result={
            "lineage": {
                "upstreamEntities": [{"id": "up-1"}],
                "downstreamEntities": [{"id": "down-1"}, {"id": "down-2"}],
            }
        }
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(connection),
        metadata_fetcher=metadata_fetcher,
        sql_router=_SqlRouterStub(None, None, None),
    )
    state = ToolOrchestrationState()
    state.semantic_grounding = SemanticGrounding(
        chosen_table_fqn="Sample-ClickHouse.default.dm.ft_order",
        candidates=[],
        selection_rationale="Selected `Sample-ClickHouse.default.dm.ft_order` because it has strong governance signals.",
    )

    result = executor.execute_tool(
        tool_name="get_metadata_entity_lineage",
        arguments={
            "entity_type": "table",
            "fqn": "Sample-ClickHouse.default.dm.ft_order",
        },
        request=ChatAskRequest(question="Откуда берётся ft_order"),
        db_connectors=[],
        state=state,
    )

    assert result["ok"] is True
    assert state.semantic_grounding is not None
    assert (
        state.semantic_grounding.lineage_summary
        == "Lineage fetched for `Sample-ClickHouse.default.dm.ft_order`: 1 upstream, 2 downstream."
    )


def test_run_sql_executes_and_stores_execution_result() -> None:
    connector = _connector()
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="analytics",
    )
    execution = _execution_result()
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=execution,
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["analytics.metrics.daily"]
    state.metadata_table_columns = {
        "analytics.metrics.daily": {"day", "gmv"},
    }

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": " SELECT gmv FROM analytics.metrics.daily "},
        request=ChatAskRequest(question="Покажи продажи", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["generated_sql"] == "SELECT gmv FROM analytics.metrics.daily"
    assert result["selected_database_connector"] == connector.name
    assert result["preflight_status"] == "pass"
    assert result["prompt_row_limit"] == 100
    assert state.execution == execution
    assert state.selected_database_connector == connector.name
    assert state.generated_sql == "SELECT gmv FROM analytics.metrics.daily"
    assert state.preflight_pass == 1


def test_run_sql_carries_requested_prompt_row_limit_to_prompt_serializer() -> None:
    connector = _connector()
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="analytics",
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=_execution_result(),
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["analytics.metrics.daily"]
    state.metadata_table_columns = {
        "analytics.metrics.daily": {"day", "gmv"},
    }

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": "SELECT gmv FROM analytics.metrics.daily",
            "prompt_row_limit": 250,
        },
        request=ChatAskRequest(question="Покажи продажи", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["prompt_row_limit"] == 250


def test_run_sql_requires_user_approval_before_execution_when_requested() -> None:
    connector = _connector()
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="analytics",
    )
    execution = _execution_result()
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=execution,
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    lifecycle = AnalysisCheckpointLifecycle()
    state = ToolOrchestrationState()
    state.context_tables = ["analytics.metrics.daily"]
    state.metadata_table_columns = {
        "analytics.metrics.daily": {"day", "gmv"},
    }
    state.checkpoint = lifecycle.mark_step_running(
        checkpoint=lifecycle.start(run_id="run-1", plan_id="plan-1"),
        step_id="baseline",
    )

    gated = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": "SELECT gmv FROM analytics.metrics.daily",
            "requires_approval": True,
            "approval_reason": "Large warehouse scan.",
        },
        request=ChatAskRequest(question="Покажи продажи", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert gated["ok"] is False
    assert gated["error_code"] == "sql_approval_required"
    assert router.sql_calls == []
    assert state.checkpoint is not None
    assert state.checkpoint.status == "awaiting_user"
    pending = state.checkpoint.budget_state["pending_approval"]
    assert pending["reason"] == "Large warehouse scan."
    token = pending["approval_token"]

    state.checkpoint = lifecycle.approve(checkpoint=state.checkpoint)
    executed = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": "SELECT gmv FROM analytics.metrics.daily",
            "approval_token": token,
        },
        request=ChatAskRequest(question="Покажи продажи", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert executed["ok"] is True
    assert router.sql_calls == [("clickhouse", "SELECT gmv FROM analytics.metrics.daily")]
    assert state.checkpoint.budget_state["consumed_approval_token"] == token


def test_run_sql_returns_optimizer_summary_and_hints() -> None:
    class _OptimizerWithFindingsStub:
        def should_optimize(self, **_kwargs) -> bool:
            return True

        def optimize(self, **kwargs) -> SqlOptimizationOutcome:
            sql = str(kwargs["sql"])
            return SqlOptimizationOutcome(
                sql_for_execution=sql,
                trace=OptimizationTrace.model_validate({
                    "optimizer_used": True,
                    "optimizer_provider": "clickhouse_rule_advisor",
                    "input_sql": sql,
                    "optimized_sql_draft": sql,
                    "applied": False,
                    "rules_triggered": ["query-join-filter-before"],
                    "findings": [
                        {
                            "rule_id": "query-join-filter-before",
                            "title": "Date filter can be mirrored before the JOIN",
                            "summary": "Filter both sides of the join by the same business date.",
                            "severity": "warning",
                            "applies_to": [
                                "dm.ft_order",
                                "dm.ft_order_unit_economics_v",
                            ],
                            "citations": [],
                        }
                    ],
                }),
            )

    connector = _connector()
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="analytics",
    )
    execution = _execution_result()
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=execution,
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
        sql_optimization_service=_OptimizerWithFindingsStub(),
    )
    state = ToolOrchestrationState()
    state.context_tables = ["dm.ft_order", "dm.ft_order_unit_economics_v"]
    state.metadata_table_columns = {
        "dm.ft_order": {"ecom_order_pk", "customer_id", "create_date"},
        "dm.ft_order_unit_economics_v": {"ecom_order_pk", "cm2_amt", "create_date"},
    }

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": (
                "SELECT o.customer_id, SUM(ue.cm2_amt) AS total_cm2 "
                "FROM dm.ft_order AS o "
                "JOIN dm.ft_order_unit_economics_v AS ue ON o.ecom_order_pk = ue.ecom_order_pk "
                "WHERE ue.create_date = '2026-04-01' "
                "GROUP BY o.customer_id"
            )
        },
        request=ChatAskRequest(
            question="Найди самого маржинального пользователя",
            workspace_id="default",
        ),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["optimizer_summary"] == (
        "Advisory only: 1 finding. Date filter can be mirrored before the JOIN."
    )
    assert result["optimizer_hints"] == [
        "[warning] Date filter can be mirrored before the JOIN: Filter both sides of the join by the same business date."
    ]


def test_run_sql_skips_optimizer_when_no_matching_skill_scope() -> None:
    class _SkipOptimizerStub:
        def __init__(self) -> None:
            self.optimize_calls = 0

        def should_optimize(self, **_kwargs) -> bool:
            return False

        def optimize(self, **kwargs) -> SqlOptimizationOutcome:
            _ = kwargs
            self.optimize_calls += 1
            raise AssertionError("optimizer must be skipped")

    connector = _connector(name="trino-adhoc")
    connector = connector.model_copy(
        update={
            "connection": connector.connection.model_copy(
                update={
                    "engine": "trino",
                    "catalog": "iceberg",
                    "schema": "dm_ft",
                }
            )
        }
    )
    connection = connector.connection
    execution = _execution_result(engine="trino", columns=["value"], rows=[[1]])
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=execution,
    )
    optimizer = _SkipOptimizerStub()
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
        sql_optimization_service=optimizer,
    )
    starts: list[dict[str, object]] = []
    ends: list[tuple[OptimizationTrace, str]] = []
    executor.set_sql_optimizer_event_handlers(
        on_start=starts.append,
        on_end=lambda trace, summary: ends.append((trace, summary)),
    )
    state = ToolOrchestrationState()
    state.context_tables = ["dm_ft.art_price"]
    state.metadata_table_columns = {"dm_ft.art_price": {"art", "price"}}

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "SELECT price FROM dm_ft.art_price LIMIT 1"},
        request=ChatAskRequest(question="Покажи цену", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert optimizer.optimize_calls == 0
    assert starts == []
    assert ends == []
    assert "sql_optimization_advisor" not in state.capability_state.used_tools


def test_run_sql_clears_stale_sql_execution_failed_warning_after_success() -> None:
    state = ToolOrchestrationState()
    state.warnings = [
        "sql_execution_failed",
        "sql_preflight_failed",
        "context_token_estimator_fallback",
    ]

    RunSqlPipeline._clear_resolved_success_warnings(state)

    assert state.warnings == ["context_token_estimator_fallback"]


def test_run_sql_preflight_fail_returns_recoverable_error() -> None:
    connector = _connector()
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="analytics",
    )
    execution = _execution_result()
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=execution,
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["analytics.metrics.daily"]
    state.metadata_table_columns = {"analytics.metrics.daily": {"day", "gmv"}}

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "SELECT generate_series(gmv) FROM analytics.metrics.daily"},
        request=ChatAskRequest(question="Покажи выручку", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is False
    assert result["error"] == "sql_preflight_failed"
    assert result["preflight_status"] == "fail"
    assert state.preflight_fail == 1
    assert "sql_preflight_failed" in state.warnings


def test_run_sql_parse_failure_returns_preflight_failed_not_uncertain_metadata() -> None:
    connector = _connector()
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="analytics",
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=_execution_result(),
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "SELECT 1 AS deviation_%"},
        request=ChatAskRequest(question="проверь запрос", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is False
    assert result["error_code"] == "sql_preflight_failed"
    assert result["preflight_status"] == "fail"
    assert result["preflight_errors"][0]["code"] == "sql_parse_failed"
    assert state.preflight_fail == 1
    assert state.preflight_uncertain == 0
    assert "sql_preflight_failed" in state.warnings
    assert "sql_preflight_uncertain" not in state.warnings
    assert router.sql_calls == []


def test_run_sql_autorepairs_single_missing_column_suggestion() -> None:
    connector = _connector()
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="analytics",
    )
    execution = _execution_result(columns=["orders_count"], rows=[[42]])
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=execution,
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["dm.ft_order"]
    state.metadata_table_columns = {
        "dm.ft_order": {"order_id", "order_create_dttm"},
    }

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": (
                "SELECT count(order_id) AS orders_count "
                "FROM dm.ft_order "
                "WHERE order_created_dttm >= now() - INTERVAL 6 DAY"
            )
        },
        request=ChatAskRequest(
            question="Покажи количество заказов по дням за последние 7 дней.",
            workspace_id="default",
        ),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["preflight_status"] == "pass"
    assert "order_create_dttm" in result["generated_sql"]
    assert "order_created_dttm" not in result["generated_sql"]
    assert "sql_preflight_identifier_repaired" in state.warnings
    assert state.preflight_pass == 1


def test_run_sql_bypasses_missing_table_when_metadata_columns_are_empty() -> None:
    connector = _connector()
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="analytics",
    )
    execution = _execution_result()
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=execution,
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["analytics.metrics.other_daily"]

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "SELECT gmv FROM analytics.metrics.daily"},
        request=ChatAskRequest(question="Покажи выручку", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["preflight_status"] == "pass"
    assert state.execution == execution
    assert state.preflight_uncertain == 1
    assert "sql_preflight_missing_table_bypassed" in state.warnings


def test_run_sql_bypasses_missing_table_even_with_unrelated_metadata_context() -> None:
    connector = _connector(name="clickhouse")
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="sandbox",
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=_execution_result(columns=["id"], rows=[[1]]),
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["analytics.metrics.daily"]
    state.metadata_table_columns = {"analytics.metrics.daily": {"gmv", "day"}}

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "SELECT id FROM sandbox.tech_table LIMIT 1"},
        request=ChatAskRequest(question="Покажи одну строку", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["preflight_status"] == "pass"
    assert state.execution is not None
    assert "sql_preflight_missing_table_bypassed" in state.warnings


def test_run_sql_bypasses_missing_column_schema_error_after_repair_miss() -> None:
    connector = _connector(name="clickhouse")
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="sandbox",
    )
    execution = _execution_result(columns=["new_metric"], rows=[[42]])
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=[
            _execution_result(columns=["database", "name"], rows=[]),
            _execution_result(columns=["database", "name"], rows=[]),
            execution,
        ],
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["analytics.metrics.daily"]
    state.metadata_table_columns = {"analytics.metrics.daily": {"day", "gmv"}}

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "SELECT new_metric FROM analytics.metrics.daily"},
        request=ChatAskRequest(question="Покажи новый показатель", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["preflight_status"] == "pass"
    assert state.execution == execution
    assert state.preflight_uncertain == 1
    assert "sql_preflight_missing_column_bypassed" in state.warnings
    assert "sql_preflight_failed" not in state.warnings


def test_run_sql_bypasses_uncertain_when_metadata_context_is_missing() -> None:
    connector = _connector(name="clickhouse")
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="sandbox",
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=_execution_result(columns=["id"], rows=[[1]]),
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "SELECT id FROM sandbox.tech_table LIMIT 1"},
        request=ChatAskRequest(question="Покажи одну строку", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["preflight_status"] == "pass"
    assert state.execution is not None
    assert "sql_preflight_uncertain_bypassed" in state.warnings


def test_run_sql_normalizes_metadata_service_prefix_before_preflight() -> None:
    connector = _connector(name="clickhouse")
    connector.connection.extra_settings = {
        "metadata_service": "Sample-ClickHouse",
        "metadata_database": "default",
    }
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="analytics",
        extra_settings={
            "metadata_service": "Sample-ClickHouse",
            "metadata_database": "default",
        },
    )
    execution = _execution_result()
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=execution,
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["analytics.metrics.daily"]
    state.metadata_table_columns = {
        "analytics.metrics.daily": {"day", "gmv"},
    }

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": "SELECT gmv FROM Sample-ClickHouse.default.analytics.metrics.daily",
            "connector_name": "Sample-ClickHouse",
        },
        request=ChatAskRequest(question="Покажи gmv за 2026 по дням", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["generated_sql"] == "SELECT gmv FROM analytics.metrics.daily"
    assert result["preflight_status"] == "pass"
    assert state.generated_sql == "SELECT gmv FROM analytics.metrics.daily"


def test_run_sql_hydrates_context_from_system_tables_introspection() -> None:
    connector = _connector(name="clickhouse")
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="default",
    )
    execution = _execution_result(
        columns=["name"],
        rows=[
            ["ft_main_whs_partner_art_date"],
            ["ft_main_whs_partner_date"],
        ],
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=execution,
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["analytics.metrics.daily"]

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": "SELECT name FROM system.tables WHERE name LIKE 'ft_main%' LIMIT 20",
        },
        request=ChatAskRequest(
            question="есть витрина с именем начинающимся с ft_main?", workspace_id="default"
        ),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert "ft_main_whs_partner_art_date" in state.context_tables
    assert "ft_main_whs_partner_date" in state.context_tables


def test_run_sql_hydrates_context_from_describe_and_allows_followup_select() -> None:
    connector = _connector(name="clickhouse")
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="sandbox",
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=_execution_result(
            columns=["name", "type"],
            rows=[["art_id", "UInt64"], ["price_type", "String"]],
        ),
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.metadata_table_columns = {"analytics.metrics.daily": {"gmv"}}

    describe_result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "DESCRIBE sandbox.sat_whs_art_price_type"},
        request=ChatAskRequest(question="что за таблица", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert describe_result["ok"] is True
    assert "sandbox.sat_whs_art_price_type" in state.context_tables
    assert state.metadata_table_columns["sandbox.sat_whs_art_price_type"] == {
        "art_id",
        "price_type",
    }
    assert state.metadata_table_schemas["sandbox.sat_whs_art_price_type"].complete is True
    assert (
        state.metadata_table_schemas["sandbox.sat_whs_art_price_type"].source == "runtime_describe"
    )

    router._execution_result = _execution_result(columns=["art_id"], rows=[[1]])

    select_result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "SELECT * FROM sandbox.sat_whs_art_price_type LIMIT 5"},
        request=ChatAskRequest(question="покажи строки", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert select_result["ok"] is True
    assert select_result["selected_database_connector"] == "clickhouse"


def test_run_sql_hydrates_context_from_single_table_probe_select() -> None:
    connector = _connector(name="clickhouse")
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="dm",
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=_execution_result(
            columns=["ecom_order_pk", "cm2_amt", "create_date"],
            rows=[[1, 123.4, "2026-04-01"]],
        ),
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["dm.ft_order_unit_economics_v"]

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "SELECT * FROM dm.ft_order_unit_economics_v LIMIT 1"},
        request=ChatAskRequest(question="покажи одну строку", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert "dm.ft_order_unit_economics_v" in state.context_tables
    assert state.metadata_table_columns["dm.ft_order_unit_economics_v"] == {
        "ecom_order_pk",
        "cm2_amt",
        "create_date",
    }
    assert state.metadata_table_schemas["dm.ft_order_unit_economics_v"].complete is False
    assert state.metadata_table_schemas["dm.ft_order_unit_economics_v"].source == "runtime_probe"


def test_run_sql_hydrates_full_schema_before_preflight_from_entity_details() -> None:
    connector = _connector_with_mapping(
        name="clickhouse",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="default",
        metadata_schema="dm",
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connector.connection,
        execution_result=_execution_result(columns=["customer_id"], rows=[[123]]),
    )
    metadata_fetcher = _MetadataFetcherStub(
        entity_results={
            "Sample-ClickHouse.default.dm.ft_order": {
                "entity": {
                    "fullyQualifiedName": "Sample-ClickHouse.default.dm.ft_order",
                    "columns": [
                        {"name": "order_id", "dataTypeDisplay": "UInt64"},
                        {"name": "customer_id", "dataTypeDisplay": "UInt64"},
                        {"name": "order_create_dttm", "dataTypeDisplay": "DateTime"},
                    ],
                    "service": {"name": "Sample-ClickHouse"},
                    "database": {"name": "default"},
                    "databaseSchema": {"name": "dm"},
                }
            }
        }
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(SimpleNamespace()),
        metadata_fetcher=metadata_fetcher,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["Sample-ClickHouse.default.dm.ft_order"]
    state.metadata_table_columns = {"sample-clickhouse.default.dm.ft_order": {"order_id"}}

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "SELECT customer_id FROM dm.ft_order LIMIT 1"},
        request=ChatAskRequest(question="Покажи клиента", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["preflight_status"] == "pass"
    assert len(metadata_fetcher.entity_calls) == 1
    entity_call = metadata_fetcher.entity_calls[0]
    assert entity_call["entity_type"] == "table"
    assert entity_call["fqn"] == "Sample-ClickHouse.default.dm.ft_order"
    scoped_connection = entity_call["connection"]
    assert getattr(scoped_connection, "service_name", None) == "Sample-ClickHouse"
    assert getattr(scoped_connection, "database_name", None) == "default"
    assert getattr(scoped_connection, "schema_name", None) == "dm"
    snapshot = state.metadata_table_schemas["sample-clickhouse.default.dm.ft_order"]
    assert snapshot.complete is True
    assert snapshot.source == "entity_details"
    assert snapshot.columns == {
        "order_id",
        "customer_id",
        "order_create_dttm",
    }


def test_run_sql_rewrites_trino_show_create_to_show_columns() -> None:
    connector = _connector_with_mapping(
        name="trino-adhoc",
        engine="trino",
        metadata_service="DWH-iceberrg",
        metadata_database="iceberg",
        metadata_schema="iceberg",
    )
    connection = EngineConnectionConfig(
        engine="trino",
        host="trino.local",
        port=443,
        username="analyst",
        password=None,
        catalog="iceberg",
        schema="iceberg",
        extra_settings=connector.connection.extra_settings,
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=_execution_result(
            engine="trino",
            columns=["Column", "Type"],
            rows=[["order_id", "bigint"], ["order_create_ts", "timestamp"]],
        ),
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "SHOW CREATE TABLE iceberg.ft_order"},
        request=ChatAskRequest(question="покажи структуру таблицы", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["executed_sql"] == "SHOW COLUMNS FROM iceberg.ft_order"
    assert router.sql_calls[-1] == ("trino", "SHOW COLUMNS FROM iceberg.ft_order")
    assert state.metadata_table_columns["iceberg.ft_order"] == {
        "order_id",
        "order_create_ts",
    }


def test_run_sql_keeps_trino_catalog_prefix_when_it_matches_connection_catalog() -> None:
    connector = _connector_with_mapping(
        name="trino-adhoc",
        engine="trino",
        metadata_service="DWH-iceberrg",
        metadata_database="iceberg",
        metadata_schema="dm_ft",
    )
    connection = EngineConnectionConfig(
        engine="trino",
        host="trino.local",
        port=443,
        username="analyst",
        password=None,
        catalog="iceberg",
        schema="dm_ft",
        extra_settings=connector.connection.extra_settings,
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=_execution_result(
            engine="trino",
            columns=["order_id"],
            rows=[[1]],
        ),
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["DWH-iceberrg.iceberg.dm_ft.ft_order"]
    state.metadata_table_columns = {"dwh-iceberrg.iceberg.dm_ft.ft_order": {"order_id"}}

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "SELECT order_id FROM DWH-iceberrg.iceberg.dm_ft.ft_order LIMIT 1"},
        request=ChatAskRequest(question="Покажи один заказ", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["executed_sql"] == "SELECT order_id FROM iceberg.dm_ft.ft_order LIMIT 1"
    assert router.sql_calls[-1] == (
        "trino",
        "SELECT order_id FROM iceberg.dm_ft.ft_order LIMIT 1",
    )


def test_run_sql_strips_unavailable_trino_metadata_service_prefix() -> None:
    connector = _connector_with_mapping(
        name="trino-adhoc",
        engine="trino",
        metadata_service="DWH-iceberrg",
        metadata_database="iceberg",
        metadata_schema="dm_ft",
    )
    connection = EngineConnectionConfig(
        engine="trino",
        host="trino.local",
        port=443,
        username="analyst",
        password=None,
        catalog="iceberg",
        schema="dm_ft",
        extra_settings=connector.connection.extra_settings,
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=_execution_result(
            engine="trino",
            columns=["order_id"],
            rows=[[1]],
        ),
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["trino_omni.iceberg.dm_ft.ft_order"]
    state.metadata_table_columns = {"trino_omni.iceberg.dm_ft.ft_order": {"order_id"}}

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": 'SELECT order_id FROM "trino_omni"."iceberg"."dm_ft".ft_order LIMIT 1'},
        request=ChatAskRequest(question="Покажи один заказ", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["executed_sql"] == "SELECT order_id FROM iceberg.dm_ft.ft_order LIMIT 1"
    assert router.sql_calls[-1] == (
        "trino",
        "SELECT order_id FROM iceberg.dm_ft.ft_order LIMIT 1",
    )


def test_run_sql_scopes_preflight_to_selected_trino_connector() -> None:
    clickhouse_connector = _connector_with_mapping(
        name="clickhouse-main",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="default",
        metadata_schema="dm",
    )
    trino_connector = _connector_with_mapping(
        name="trino-adhoc",
        engine="trino",
        metadata_service="trino_omni",
        metadata_database="iceberg",
        metadata_schema="dm_ft",
    )
    trino_connection = EngineConnectionConfig(
        engine="trino",
        host="trino.local",
        port=443,
        username="analyst",
        password=None,
        catalog="iceberg",
        schema="dm_ft",
        extra_settings=trino_connector.connection.extra_settings,
    )
    router = _SqlRouterStub(
        selected_connector=trino_connector,
        connection=trino_connection,
        execution_result=_execution_result(
            engine="trino",
            columns=["order_source"],
            rows=[["web"]],
        ),
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = [
        "Sample-ClickHouse.default.dm.ft_order",
        "trino_omni.iceberg.dm_ft.ft_order",
        "trino_omni.iceberg.dm_ft.ft_order_art",
    ]
    state.metadata_table_columns = {
        "sample-clickhouse.default.dm.ft_order": {"order_id"},
        "trino_omni.iceberg.dm_ft.ft_order": {"order_id", "order_source"},
        "trino_omni.iceberg.dm_ft.ft_order_art": {"order_id"},
    }

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": (
                "SELECT o.order_source "
                "FROM trino_omni.iceberg.dm_ft.ft_order AS o "
                "LEFT JOIN trino_omni.iceberg.dm_ft.ft_order_art AS a "
                "ON a.order_id = o.order_id "
                "WHERE a.order_id IS NULL"
            )
        },
        request=ChatAskRequest(
            question="найди order_source без матча в ft_order_art через trino",
            workspace_id="default",
        ),
        db_connectors=[clickhouse_connector, trino_connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["selected_database_connector"] == "trino-adhoc"
    assert result["executed_sql"] == (
        "SELECT o.order_source FROM iceberg.dm_ft.ft_order AS o "
        "LEFT JOIN iceberg.dm_ft.ft_order_art AS a ON a.order_id = o.order_id "
        "WHERE a.order_id IS NULL"
    )
    assert router.sql_calls[-1] == (
        "trino",
        "SELECT o.order_source FROM iceberg.dm_ft.ft_order AS o "
        "LEFT JOIN iceberg.dm_ft.ft_order_art AS a ON a.order_id = o.order_id "
        "WHERE a.order_id IS NULL",
    )


def test_run_sql_hydrates_trino_catalog_schema_ref_from_omd_full_schema() -> None:
    connector = _connector_with_mapping(
        name="trino-adhoc",
        engine="trino",
        metadata_service="trino_omni",
        metadata_database="iceberg",
        metadata_schema="dm_ft",
    )
    connection = EngineConnectionConfig(
        engine="trino",
        host="trino.local",
        port=443,
        username="analyst",
        password=None,
        catalog="iceberg",
        schema="dm_ft",
        extra_settings=connector.connection.extra_settings,
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=_execution_result(
            engine="trino",
            columns=["order_source", "order_count"],
            rows=[["web", 10]],
        ),
    )
    metadata_fetcher = _MetadataFetcherStub(
        entity_results={
            "trino_omni.iceberg.dm_ft.ft_order": {
                "entity": {
                    "entityType": "table",
                    "fullyQualifiedName": "trino_omni.iceberg.dm_ft.ft_order",
                    "columns": [
                        {"name": "ecom_order_pk", "dataType": "varchar"},
                        {"name": "order_create_dttm", "dataType": "timestamp"},
                        {"name": "order_source", "dataType": "varchar"},
                    ],
                }
            },
            "trino_omni.iceberg.dm_ft.ft_order_art": {
                "entity": {
                    "entityType": "table",
                    "fullyQualifiedName": "trino_omni.iceberg.dm_ft.ft_order_art",
                    "columns": [
                        {"name": "order_create_dttm", "dataType": "timestamp"},
                        {"name": "order_source", "dataType": "varchar"},
                    ],
                }
            },
        }
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(SimpleNamespace()),
        metadata_fetcher=metadata_fetcher,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = [
        "trino_omni.iceberg.dm_ft.ft_order",
        "trino_omni.iceberg.dm_ft.ft_order_art",
        "iceberg.dm_ft.ft_order",
        "iceberg.dm_ft.ft_order_art",
    ]
    state.metadata_table_columns = {
        "trino_omni.iceberg.dm_ft.ft_order": {"order_id", "order_source"},
        "trino_omni.iceberg.dm_ft.ft_order_art": {"order_id", "order_source"},
        "iceberg.dm_ft.ft_order": {"order_id", "order_source"},
        "iceberg.dm_ft.ft_order_art": {"order_id", "order_source"},
    }

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": (
                "WITH ft_order_sources AS ("
                "SELECT order_source, ecom_order_pk "
                "FROM iceberg.dm_ft.ft_order "
                "WHERE order_create_dttm >= TIMESTAMP '2026-05-01 00:00:00' "
                "AND order_create_dttm < TIMESTAMP '2026-06-01 00:00:00'"
                "), ft_order_art_sources AS ("
                "SELECT DISTINCT order_source "
                "FROM iceberg.dm_ft.ft_order_art "
                "WHERE order_create_dttm >= TIMESTAMP '2026-05-01 00:00:00' "
                "AND order_create_dttm < TIMESTAMP '2026-06-01 00:00:00'"
                ") "
                "SELECT o.order_source, COUNT(DISTINCT o.ecom_order_pk) AS order_count "
                "FROM ft_order_sources o "
                "LEFT JOIN ft_order_art_sources a ON o.order_source = a.order_source "
                "WHERE a.order_source IS NULL "
                "GROUP BY o.order_source"
            )
        },
        request=ChatAskRequest(
            question="найди order_source в ft_order, которых нет в ft_order_art",
            workspace_id="default",
        ),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["preflight_status"] == "pass"
    assert "sql_preflight_failed" not in state.warnings
    assert [call["fqn"] for call in metadata_fetcher.entity_calls] == [
        "trino_omni.iceberg.dm_ft.ft_order",
        "trino_omni.iceberg.dm_ft.ft_order_art",
    ]
    assert "order_create_dttm" in state.metadata_table_columns["trino_omni.iceberg.dm_ft.ft_order"]
    assert "order_create_dttm" in state.metadata_table_columns["iceberg.dm_ft.ft_order"]
    assert not any(
        dialect == "trino" and "information_schema.columns" in sql.lower()
        for dialect, sql in router.sql_calls
    )


def test_run_sql_still_strips_clickhouse_metadata_database_prefix() -> None:
    connector = _connector_with_mapping(
        name="clickhouse",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="default",
        metadata_schema="dm",
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connector.connection,
        execution_result=_execution_result(
            columns=["order_id"],
            rows=[[1]],
        ),
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["Sample-ClickHouse.default.dm.ft_order"]
    state.metadata_table_columns = {"sample-clickhouse.default.dm.ft_order": {"order_id"}}

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "SELECT order_id FROM default.dm.ft_order LIMIT 1"},
        request=ChatAskRequest(question="Покажи один заказ", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["executed_sql"] == "SELECT order_id FROM dm.ft_order LIMIT 1"
    assert router.sql_calls[-1] == (
        "clickhouse",
        "SELECT order_id FROM dm.ft_order LIMIT 1",
    )


def test_run_sql_repairs_missing_table_context_from_metadata_exact_match() -> None:
    connector = _connector_with_mapping(
        name="clickhouse",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="default",
        metadata_schema="dm",
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connector.connection,
        execution_result=_execution_result(columns=["orders_count"], rows=[[42]]),
    )
    metadata_fetcher = _MetadataFetcherStub(
        tables=[
            TableContext(
                source="openmetadata",
                fqn="Sample-ClickHouse.default.dm.ft_order",
                columns=["order_id", "order_create_dttm"],
                service="Sample-ClickHouse",
                database="default",
                schema="dm",
            )
        ]
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(SimpleNamespace()),
        metadata_fetcher=metadata_fetcher,
        sql_router=router,
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": (
                "SELECT count(order_id) AS orders_count "
                "FROM dm.ft_order "
                "WHERE order_create_dttm >= now() - INTERVAL 6 DAY"
            )
        },
        request=ChatAskRequest(
            question="Покажи количество заказов по дням за последние 7 дней.",
            workspace_id="default",
        ),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert "sql_preflight_context_repaired" in state.warnings
    assert "Sample-ClickHouse.default.dm.ft_order" in state.context_tables
    assert state.metadata_table_columns["sample-clickhouse.default.dm.ft_order"] == {
        "order_id",
        "order_create_dttm",
    }


def test_run_sql_repairs_missing_table_context_from_catalog_when_fetch_tables_miss() -> None:
    connector = _connector_with_mapping(
        name="clickhouse",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="default",
        metadata_schema="dm",
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connector.connection,
        execution_result=_execution_result(columns=["orders_count"], rows=[[42]]),
    )
    metadata_fetcher = _MetadataFetcherStub(
        tables=[],
        search_results=[
            {
                "entityType": "table",
                "fullyQualifiedName": "Sample-ClickHouse.default.dm.ft_order",
                "columnNames": ["order_id", "order_create_dttm"],
                "service": "Sample-ClickHouse",
                "database": "default",
                "databaseSchema": "dm",
            }
        ],
    )
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(
            SimpleNamespace(mcp_tables_url="https://metadata.example/mcp")
        ),
        metadata_fetcher=metadata_fetcher,
        sql_router=router,
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": (
                "SELECT count(order_id) AS orders_count "
                "FROM dm.ft_order "
                "WHERE order_create_dttm >= now() - INTERVAL 6 DAY"
            )
        },
        request=ChatAskRequest(
            question="Покажи количество заказов по дням за последние 7 дней.",
            workspace_id="default",
            metadata_connector_name="openmetadata",
        ),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert "sql_preflight_context_repaired" in state.warnings
    assert "Sample-ClickHouse.default.dm.ft_order" in state.context_tables
    assert state.metadata_table_columns["sample-clickhouse.default.dm.ft_order"] == {
        "order_id",
        "order_create_dttm",
    }


def test_run_sql_repairs_missing_table_context_from_trino_runtime_lookup() -> None:
    connector = _connector_with_mapping(
        name="trino-adhoc",
        engine="trino",
        metadata_service="DWH-iceberrg",
        metadata_database="iceberg",
        metadata_schema="iceberg",
    )
    connection = EngineConnectionConfig(
        engine="trino",
        host="trino.local",
        port=443,
        username="analyst",
        password=None,
        catalog="iceberg",
        schema="iceberg",
        extra_settings=connector.connection.extra_settings,
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=[
            _execution_result(
                engine="trino",
                columns=["table_schema", "table_name"],
                rows=[["iceberg", "ft_order"]],
            ),
            _execution_result(
                engine="trino",
                columns=["column_name", "data_type"],
                rows=[["order_id", "bigint"], ["order_create_ts", "timestamp"]],
            ),
            _execution_result(
                engine="trino",
                columns=["orders_count"],
                rows=[[42]],
            ),
        ],
    )
    metadata_fetcher = _MetadataFetcherStub(tables=[])
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(SimpleNamespace()),
        metadata_fetcher=metadata_fetcher,
        sql_router=router,
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": (
                "SELECT count(order_id) AS orders_count "
                "FROM iceberg.ft_order "
                "WHERE order_create_ts >= current_timestamp - INTERVAL '7' DAY"
            )
        },
        request=ChatAskRequest(
            question="Покажи количество заказов за 7 дней.",
            workspace_id="default",
        ),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert "sql_preflight_context_repaired" in state.warnings
    assert "DWH-iceberrg.iceberg.iceberg.ft_order" in state.context_tables
    assert state.metadata_table_columns["dwh-iceberrg.iceberg.iceberg.ft_order"] == {
        "order_id",
        "order_create_ts",
    }
    assert any(
        dialect == "trino" and "FROM information_schema.tables" in sql
        for dialect, sql in router.sql_calls
    )
    assert any(
        dialect == "trino" and "FROM information_schema.columns" in sql
        for dialect, sql in router.sql_calls
    )


def test_run_sql_repairs_missing_column_context_from_trino_runtime_lookup() -> None:
    connector = _connector_with_mapping(
        name="trino-adhoc",
        engine="trino",
        metadata_service="trino_omni",
        metadata_database="iceberg",
        metadata_schema="dm_ft",
    )
    connection = EngineConnectionConfig(
        engine="trino",
        host="trino.local",
        port=443,
        username="analyst",
        password=None,
        catalog="iceberg",
        schema="dm_ft",
        extra_settings=connector.connection.extra_settings,
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=[
            _execution_result(
                engine="trino",
                columns=["table_schema", "table_name"],
                rows=[["dm_ft", "ft_order"]],
            ),
            _execution_result(
                engine="trino",
                columns=["column_name", "data_type"],
                rows=[
                    ["order_id", "varchar"],
                    ["order_source", "varchar"],
                    ["delivery_type", "varchar"],
                ],
            ),
            _execution_result(
                engine="trino",
                columns=["order_id", "delivery_type"],
                rows=[["eR-1", "DELIVERY_EXPRESS"]],
            ),
        ],
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["trino_omni.iceberg.dm_ft.ft_order"]
    state.metadata_table_columns = {
        "trino_omni.iceberg.dm_ft.ft_order": {
            "coupon_name",
            "dct_amt",
            "dct_amt_exc_nds",
            "ecom_order_pk",
            "customer_id",
            "order_id",
            "order_source",
            "order_status_name",
            "partner_name",
            "refund_dct_amt",
            "refund_sale_amt",
            "refund_sale_amt_exc_nds",
            "sale_amt",
            "sale_amt_exc_nds",
            "status_reason_desc",
            "whs_code",
            "whs_fed_region",
            "whs_frmt",
            "whs_region",
            "whs_subfrmt",
        }
    }

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": ("SELECT order_id, delivery_type FROM iceberg.dm_ft.ft_order LIMIT 1")},
        request=ChatAskRequest(
            question="Покажи один заказ с delivery_type из trino",
            workspace_id="default",
        ),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["selected_database_connector"] == "trino-adhoc"
    assert "sql_preflight_context_repaired" in state.warnings
    assert "sql_preflight_failed" not in state.warnings
    assert "delivery_type" in state.metadata_table_columns["trino_omni.iceberg.dm_ft.ft_order"]
    assert any(
        dialect == "trino" and "FROM information_schema.tables" in sql
        for dialect, sql in router.sql_calls
    )
    assert any(
        dialect == "trino" and "FROM information_schema.columns" in sql
        for dialect, sql in router.sql_calls
    )


def test_run_sql_reuses_refreshed_context_after_clickhouse_runtime_repair_without_columns() -> None:
    connector = _connector_with_mapping(
        name="clickhouse",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="default",
        metadata_schema="",
    )
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="clickhouse.local",
        port=8123,
        username="analyst",
        password=None,
        database="default",
        extra_settings=connector.connection.extra_settings,
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=[
            _execution_result(
                engine="clickhouse",
                columns=["database", "name"],
                rows=[["dm", "ft_order"]],
            ),
            ExecuteSqlResponse(
                execution_id="exec-columns",
                status="failed",
                engine="clickhouse",
                error_message="columns unavailable",
            ),
            _execution_result(
                engine="clickhouse",
                columns=["order_id"],
                rows=[[1]],
            ),
        ],
    )
    metadata_fetcher = _MetadataFetcherStub(tables=[])
    executor = ToolExecutor(
        metadata_resolver=_MetadataResolverStub(SimpleNamespace()),
        metadata_fetcher=metadata_fetcher,
        sql_router=router,
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": "SELECT * FROM dm.ft_order LIMIT 5",
        },
        request=ChatAskRequest(
            question="покажи пример строк из ft_order в clickhouse",
            workspace_id="default",
        ),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert "sql_preflight_context_repaired" in state.warnings
    assert "sql_preflight_failed" not in state.warnings
    assert "Sample-ClickHouse.default.dm.ft_order" in state.context_tables
    assert any(
        dialect == "clickhouse" and "FROM system.tables" in sql for dialect, sql in router.sql_calls
    )


def test_run_sql_prefers_connector_from_metadata_context_without_explicit_choice() -> None:
    clickhouse = _connector_with_mapping(
        name="clickhouse",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="analytics",
        metadata_schema="sandbox",
    )
    greenplum = _connector_with_mapping(
        name="greenplum-analytics-prod",
        engine="trino",
        metadata_service="greenplum-analytics-prod",
        metadata_database="analytics",
        metadata_schema="sandbox",
    )
    router = _SqlRouterStub(
        selected_connector=clickhouse,
        connection=clickhouse.connection,
        execution_result=_execution_result(columns=["art_id"], rows=[[1]]),
        connections_by_name={
            clickhouse.name: clickhouse.connection,
            greenplum.name: greenplum.connection,
        },
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["greenplum-analytics-prod.analytics.sandbox.sat_whs_art_price_type"]

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": "SELECT * FROM sandbox.sat_whs_art_price_type LIMIT 5",
        },
        request=ChatAskRequest(question="покажи art_price", workspace_id="default"),
        db_connectors=[clickhouse, greenplum],
        state=state,
    )

    assert result["ok"] is True
    assert result["selected_database_connector"] == "greenplum-analytics-prod"
    assert router.connection_requests[-1] == ("default", "greenplum-analytics-prod")
    assert "sql_connector_overridden_from_metadata_context" in state.warnings


def test_run_sql_respects_explicit_connector_name_over_execution_zone() -> None:
    clickhouse = _connector(name="clickhouse")
    trino = _connector(name="trino-adhoc").model_copy(
        update={
            "id": "conn-trino",
            "connection": _connector(name="trino-adhoc").connection.model_copy(
                update={
                    "engine": "trino",
                    "host": "trino.local",
                    "port": 443,
                    "database": None,
                    "catalog": "iceberg",
                    "schema": "dm_ft",
                }
            ),
        }
    )
    router = _SqlRouterStub(
        selected_connector=clickhouse,
        connection=clickhouse.connection,
        execution_result=_execution_result(columns=["name"], rows=[["ft_order"]]),
        connections_by_name={
            clickhouse.name: clickhouse.connection,
            trino.name: trino.connection,
        },
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState(
        execution_zone=ExecutionZone(
            service="Trino-OMNI",
            database="iceberg",
            connector_name="trino-adhoc",
        )
    )

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "connector_name": "clickhouse",
            "sql": "SELECT name FROM system.tables WHERE database = 'analytics' LIMIT 5",
        },
        request=ChatAskRequest(question="дай таблицы", workspace_id="default"),
        db_connectors=[clickhouse, trino],
        state=state,
    )

    assert result["ok"] is True
    assert result["selected_database_connector"] == "clickhouse"
    assert router.connection_requests[-1] == ("default", "clickhouse")
    assert "sql_connector_overridden_from_execution_zone" not in state.warnings


def test_run_sql_blocks_unavailable_context_table_execution() -> None:
    clickhouse = _connector_with_mapping(
        name="clickhouse",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="analytics",
        metadata_schema="sandbox",
    )
    router = _SqlRouterStub(
        selected_connector=clickhouse,
        connection=clickhouse.connection,
        execution_result=_execution_result(columns=["art_id"], rows=[[1]]),
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = ["greenplum-analytics-prod.analytics.sandbox.sat_whs_art_price_type"]

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={"sql": "SELECT * FROM sandbox.sat_whs_art_price_type LIMIT 5"},
        request=ChatAskRequest(question="покажи art_price", workspace_id="default"),
        db_connectors=[clickhouse],
        state=state,
    )

    assert result["ok"] is False
    assert result["error_code"] == "database_connector_unavailable_for_context_table"
    assert result["available_database_connectors"] == ["clickhouse"]
    assert router.connection_requests == []
    assert "sql_execution_blocked_unavailable_context_connector" in state.warnings


def test_run_sql_allows_cte_join_within_single_metadata_database() -> None:
    connector = _connector_with_mapping(
        name="clickhouse",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="default",
        metadata_schema="dm",
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connector.connection,
        execution_result=_execution_result(columns=["segment", "orders_count"], rows=[["vip", 42]]),
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = [
        "Sample-ClickHouse.default.dm.ft_order",
        "Sample-ClickHouse.default.dm.dim_user",
    ]
    state.metadata_table_columns = {
        "sample-clickhouse.default.dm.ft_order": {"user_id"},
        "sample-clickhouse.default.dm.dim_user": {"user_id", "segment"},
    }

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": (
                "WITH orders AS ("
                "SELECT user_id, count() AS orders_count "
                "FROM dm.ft_order "
                "GROUP BY user_id"
                ") "
                "SELECT u.segment, o.orders_count "
                "FROM dm.dim_user AS u "
                "JOIN orders AS o ON u.user_id = o.user_id"
            )
        },
        request=ChatAskRequest(question="дай заказы по сегментам", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["preflight_status"] == "pass"
    assert router.connection_requests[-1] == ("default", "clickhouse")
    assert state.execution_zone is not None
    assert state.execution_zone.service == "Sample-ClickHouse"
    assert state.execution_zone.database == "default"
    assert state.execution_zone.connector_name == "clickhouse"


def test_run_sql_blocks_cross_database_join_before_execution() -> None:
    default_connector = _connector_with_mapping(
        name="clickhouse-default",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="default",
        metadata_schema="dm",
    )
    analytics_connector = _connector_with_mapping(
        name="clickhouse-analytics",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="analytics",
        metadata_schema="mart",
    )
    router = _SqlRouterStub(
        selected_connector=default_connector,
        connection=default_connector.connection,
        execution_result=_execution_result(columns=["segment"], rows=[["vip"]]),
        connections_by_name={
            default_connector.name: default_connector.connection,
            analytics_connector.name: analytics_connector.connection,
        },
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.context_tables = [
        "Sample-ClickHouse.default.dm.ft_order",
        "Sample-ClickHouse.analytics.mart.dim_customer",
    ]
    state.metadata_table_columns = {
        "sample-clickhouse.default.dm.ft_order": {"customer_id", "order_id"},
        "sample-clickhouse.analytics.mart.dim_customer": {"customer_id", "segment"},
    }

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": (
                "SELECT o.order_id, c.segment "
                "FROM dm.ft_order AS o "
                "JOIN mart.dim_customer AS c ON o.customer_id = c.customer_id"
            )
        },
        request=ChatAskRequest(question="дай сегмент по заказам", workspace_id="default"),
        db_connectors=[default_connector, analytics_connector],
        state=state,
    )

    assert result["ok"] is False
    assert result["error_code"] == "sql_preflight_failed"
    assert any(error["code"] == "cross_database_reference" for error in result["preflight_errors"])
    assert router.connection_requests == [("default", "clickhouse-default")]
    assert state.execution is None
    assert state.execution_zone is not None
    assert state.execution_zone.connector_name == "clickhouse-default"


def test_run_sql_success_clears_resolved_transient_sql_warnings() -> None:
    connector = _connector(name="clickhouse")
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="db.local",
        port=8123,
        username="analyst",
        password=None,
        database="analytics",
    )
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connection,
        execution_result=_execution_result(columns=["orders_count"], rows=[[42]]),
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState(
        warnings=[
            "sql_execution_blocked_unavailable_context_connector",
            "sql_preflight_failed",
            "sql_preflight_uncertain",
        ]
    )
    state.context_tables = ["dm.orders_ops_daily_report"]
    state.metadata_table_columns = {
        "dm.orders_ops_daily_report": {"report_date", "orders"},
    }

    result = executor.execute_tool(
        tool_name="run_sql",
        arguments={
            "sql": (
                "SELECT report_date, SUM(orders) AS orders_count "
                "FROM dm.orders_ops_daily_report "
                "GROUP BY report_date"
            )
        },
        request=ChatAskRequest(
            question="Покажи количество заказов по дням за последние 7 дней.",
            workspace_id="default",
        ),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["preflight_status"] == "pass"
    assert "sql_execution_blocked_unavailable_context_connector" not in state.warnings
    assert "sql_preflight_failed" not in state.warnings
    assert "sql_preflight_uncertain" not in state.warnings


def test_search_rag_insights_tool_adds_rag_source() -> None:
    connector = _connector()
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=None,
        execution_result=None,
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
        rag_retriever=_RagRetrieverStub(
            results=[
                {
                    "id": "ins-1",
                    "score": 0.9,
                    "verification_status": "verified",
                    "summary": "A concise insight",
                    "article_markdown": "# Insight\n\nText",
                    "keywords": ["retention"],
                    "context_tables": ["dm.events"],
                }
            ]
        ),
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="search_rag_insights",
        arguments={"query": "retention trend", "limit": 3},
        request=ChatAskRequest(question="retention", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["metadata_sources"] == ["rag"]
    assert state.metadata_sources == {"rag"}


def test_search_confluence_pages_tool_adds_confluence_source(monkeypatch) -> None:
    connector = _connector()
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=None,
        execution_result=None,
    )
    token_value = uuid4().hex
    confluence_repo = _ConfluenceRepoStub(
        connection=ConfluenceConnectionConfig(
            provider="confluence",
            base_url="https://confluence.example.com",
            username="reader@example.com",
            api_token=token_value,
            auth_type="bearer",
            verify_tls=True,
            space_key="DOCS",
            page_limit=10,
        )
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
        confluence_connector_repository=confluence_repo,
    )
    state = ToolOrchestrationState()

    monkeypatch.setattr(
        ToolExecutor,
        "_search_confluence_pages",
        lambda *_args, **_kwargs: (
            [
                {
                    "id": "123",
                    "title": "Retention definition",
                    "url": "https://confluence.example.com/wiki/spaces/DOCS/pages/123",
                    "excerpt": "Retention is ...",
                }
            ],
            [],
        ),
    )

    result = executor.execute_tool(
        tool_name="search_confluence_pages",
        arguments={"query": "retention", "limit": 5},
        request=ChatAskRequest(question="Что такое retention?", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["metadata_sources"] == ["confluence"]
    assert state.metadata_sources == {"confluence"}
    assert confluence_repo.calls == [("default", None)]


def test_search_confluence_pages_tool_returns_error_on_http_failure(monkeypatch) -> None:
    connector = _connector()
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=None,
        execution_result=None,
    )
    confluence_repo = _ConfluenceRepoStub(
        connection=ConfluenceConnectionConfig(
            provider="confluence",
            base_url="https://confluence.example.com",
            username="reader@example.com",
            api_token=uuid4().hex,
            auth_type="bearer",
            verify_tls=True,
            space_key="DOCS",
            page_limit=10,
        )
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
        confluence_connector_repository=confluence_repo,
    )
    state = ToolOrchestrationState()
    request = httpx.Request("GET", "https://confluence.example.com/rest/api/content/search")
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError(
        "Server error",
        request=request,
        response=response,
    )

    def raise_http_error(*_args, **_kwargs):
        raise exc

    monkeypatch.setattr(ToolExecutor, "_search_confluence_pages", raise_http_error)

    result = executor.execute_tool(
        tool_name="search_confluence_pages",
        arguments={"query": "retention", "limit": 5},
        request=ChatAskRequest(question="Check Confluence", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is False
    assert result["error_code"] == "http_500"
    assert result["error_category"] == "TRANSIENT"
    assert result["retry_recommended"] is True
    assert result["can_continue"] is True
    assert state.metadata_sources == set()
    assert "confluence_search_failed" in state.warnings


def test_search_confluence_pages_raises_http_status_errors(monkeypatch) -> None:
    connector = _connector()
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=None,
        execution_result=None,
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )

    class _FakeHttpClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, *_args, **_kwargs):
            request = httpx.Request(
                "GET",
                "https://confluence.example.com/rest/api/content/search",
            )
            return httpx.Response(500, request=request)

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.tool_docs_capability_service.httpx.Client",
        _FakeHttpClient,
    )

    with pytest.raises(httpx.HTTPStatusError):
        executor._search_confluence_pages(
            connection=ConfluenceConnectionConfig(
                provider="confluence",
                base_url="https://confluence.example.com",
                username="reader@example.com",
                api_token=uuid4().hex,
                auth_type="bearer",
                verify_tls=True,
                space_key="DOCS",
                page_limit=10,
            ),
            query="retention",
            limit=5,
            space_key="DOCS",
        )


def test_build_visualization_validates_against_successful_execution() -> None:
    connector = _connector()
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connector.connection,
        execution_result=None,
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.record_execution_outcome(
        sql="SELECT report_date, orders_count FROM dm.orders_daily_report",
        connector_name=connector.name,
        execution=_execution_result(
            columns=["report_date", "orders_count"],
            rows=[
                ["2026-05-01", 10],
                ["2026-05-02", 20],
            ],
        ),
    )

    result = executor.execute_tool(
        tool_name="build_visualization",
        arguments={
            "execution_id": "exec-1",
            "kind": "line",
            "x": "report_date",
            "y": ["orders_count"],
            "why_kind": "Нужен дневной тренд.",
        },
        request=ChatAskRequest(question="Нарисуй график", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    assert result["visualization"]["kind"] == "line"
    assert result["visualization"]["encoding"]["x"] == "report_date"
    assert result["visualization"]["encoding"]["y"] == ["orders_count"]
    assert result["visualization"]["data_source"]["execution_id"] == "exec-1"
    assert state.visualization_candidate is not None
    assert "build_visualization" in state.capability_state.used_tools


def test_build_visualization_treats_null_optional_arguments_as_defaults() -> None:
    connector = _connector()
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connector.connection,
        execution_result=None,
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()
    state.record_execution_outcome(
        sql="SELECT category, revenue FROM dm.category_revenue",
        connector_name=connector.name,
        execution=_execution_result(
            columns=["category", "revenue"],
            rows=[
                ["Beverages", 267868.18],
                ["Dairy Products", 234507.28],
            ],
        ),
    )

    result = executor.execute_tool(
        tool_name="build_visualization",
        arguments={
            "execution_id": "exec-1",
            "kind": "bar",
            "goal": None,
            "x": "category",
            "y": ["revenue"],
            "sort": "desc",
            "limit": None,
            "aggregation": None,
            "normalize": None,
            "why_kind": "Нужен рейтинг категорий.",
            "why_fields": ["category", "revenue"],
        },
        request=ChatAskRequest(question="Нарисуй график", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is True
    transform = result["visualization"]["transform"]
    assert transform["aggregation"] == "none"
    assert transform["normalize"] == "none"
    assert transform["limit"] == 50


def test_build_visualization_rejects_unknown_execution_id() -> None:
    connector = _connector()
    router = _SqlRouterStub(
        selected_connector=connector,
        connection=connector.connection,
        execution_result=None,
    )
    executor = ToolExecutor(
        metadata_resolver=None,
        metadata_fetcher=None,
        sql_router=router,
    )
    state = ToolOrchestrationState()

    result = executor.execute_tool(
        tool_name="build_visualization",
        arguments={
            "execution_id": "missing",
            "kind": "line",
            "x": "report_date",
            "y": ["orders_count"],
            "why_kind": "Нужен дневной тренд.",
        },
        request=ChatAskRequest(question="Нарисуй график", workspace_id="default"),
        db_connectors=[connector],
        state=state,
    )

    assert result["ok"] is False
    assert result["error_code"] == "visualization_execution_not_found"
    assert "visualization_execution_not_found" in state.warnings
