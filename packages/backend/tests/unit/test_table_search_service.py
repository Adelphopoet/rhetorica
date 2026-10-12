"""Tests for the OpenMetadata-first table search cascade exposed to external MCP clients.

Grounded in the 2026-09-03 production probe with a clean Claude Code client: the model
called `search_metadata` for "orders", got sandbox/Teradata/tpch noise on top, and gave up
on OpenMetadata in favour of `system.tables`. The service below is what should have
happened inside one tool call.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from rhetorica_backend.contracts.execution import ExecuteSqlResponse, QueryPreview
from rhetorica_backend.domain.metadata.table_search_service import (
    MetadataServiceDiscoveryError,
    TableSearchService,
)


def _db_connector(name: str, engine: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, connection=SimpleNamespace(engine=engine))


def _omd_catalog_item() -> SimpleNamespace:
    return SimpleNamespace(
        connector_name="openmetadata-mcp",
        remote_tool_name="search_metadata",
        exposed_tool_name="openmetadata__search_metadata",
        input_schema={
            "type": "object",
            "properties": {
                name: {} for name in ("query", "entityType", "size", "queryFilter", "cursor")
            },
        },
        is_active=True,
    )


def _mdm_catalog_item() -> SimpleNamespace:
    return SimpleNamespace(
        connector_name="mdm",
        remote_tool_name="search_metadata",
        exposed_tool_name="mdm__search_metadata",
        input_schema={
            "type": "object",
            "properties": {"query": {}, "entity_types": {}, "limit": {}},
            "additionalProperties": False,
        },
        is_active=True,
    )


def _omd_hit(
    fqn: str,
    *,
    service_type: str = "Clickhouse",
    description: str | None = "Витрина заказов FoodTech",
    column_names: list[str] | None = None,
) -> dict:
    service_name, _, rest = fqn.partition(".")
    database, schema, _table = rest.split(".", 2)
    return {
        "fullyQualifiedName": fqn,
        "entityType": "table",
        "serviceType": service_type,
        "service": {"type": "databaseService", "name": service_name},
        "database": {"type": "database", "name": database},
        "databaseSchema": {"type": "databaseSchema", "name": schema},
        "description": description,
        "columnNames": column_names or ["order_id", "order_create_dttm"],
        "owners": [{"type": "user", "name": "durnikov_gv"}],
        "tier": None,
        "tags": [],
    }


def _omd_payload(hits: list[dict], *, next_cursor: str | None = None) -> dict:
    return {
        "ok": True,
        "connector_name": "openmetadata-mcp",
        "remote_tool_name": "search_metadata",
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "results": hits,
                            "returnedCount": len(hits),
                            "totalFound": len(hits),
                            "hasMore": next_cursor is not None,
                            "nextCursor": next_cursor,
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            "isError": False,
        },
    }


def _omd_failure(message: str = "OpenMetadata is unavailable") -> dict:
    return {
        "ok": False,
        "error_code": "mcp_tool_call_failed",
        "user_message": message,
    }


def _omd_service(name: str, service_type: str = "Clickhouse") -> dict:
    return {
        "name": name,
        "fullyQualifiedName": name,
        "entityType": "databaseService",
        "serviceType": service_type,
        "description": f"Service {name}",
    }


def _service(
    *,
    omd_payloads: list[dict],
    db_connectors: list[SimpleNamespace],
    sql_router: MagicMock | None = None,
) -> tuple[TableSearchService, MagicMock, MagicMock]:
    execution_service = MagicMock()
    execution_service.execute.side_effect = omd_payloads
    mcp_repository = MagicMock()
    mcp_repository.list_active_tools.return_value = [_omd_catalog_item()]
    db_repository = MagicMock()
    db_repository.list.return_value = db_connectors
    router = sql_router or MagicMock()
    service = TableSearchService(
        mcp_tool_execution_service=execution_service,
        mcp_connector_repository=mcp_repository,
        db_connector_repository=db_repository,
        sql_router=router,
    )
    return service, execution_service, router


def test_search_scopes_omd_by_workspace_engines() -> None:
    service, execution_service, _ = _service(
        omd_payloads=[_omd_payload([_omd_hit("Clickhouse-Sales.default.dm.ft_order")])],
        db_connectors=[_db_connector("clickhouse", "clickhouse"), _db_connector("gp", "greenplum")],
    )

    result = service.search(workspace_id="default", query="заказы фудтех")

    execution_service.execute.assert_called_once()
    call = execution_service.execute.call_args.kwargs
    assert call["workspace_id"] == "default"
    assert call["exposed_tool_name"] == "openmetadata__search_metadata"
    arguments = call["arguments"]
    assert arguments["query"] == "заказы фудтех"
    assert arguments["entityType"] == "table"
    service._db_connectors.list.assert_called_once_with(workspace_id="default", limit=None)
    service._mcp_connectors.list_active_tools.assert_called_once_with(
        workspace_id="default", limit=None
    )
    query_filter = json.loads(arguments["queryFilter"])
    assert {"Clickhouse", "Greenplum"} <= set(_terms(query_filter, "serviceType"))

    assert result.found_by == "omd_scoped"
    assert [stage.name for stage in result.tried] == ["omd_scoped", "runtime"]
    assert result.tried[0].found == 1
    candidate = result.tables[0]
    assert candidate["fqn"] == "Clickhouse-Sales.default.dm.ft_order"
    assert candidate["engine"] == "clickhouse"
    assert candidate["origin"] == "openmetadata"
    assert candidate["service"] == "Clickhouse-Sales"
    assert candidate["connector"] is None
    assert candidate["connectors"] == []
    assert candidate["database"] == "default"
    assert candidate["schema"] == "dm"
    assert candidate["description"] == "Витрина заказов FoodTech"
    assert candidate["found_by"] == "omd_scoped"


def _terms(query_filter: dict, field: str) -> list[str]:
    """Collect every value of `term`/`terms` clauses for `field` in an OpenSearch bool query."""
    values: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"term", "terms"} and isinstance(value, dict) and field in value:
                    raw = value[field]
                    values.extend(raw if isinstance(raw, list) else [raw])
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(query_filter)
    return values


def _sql_response(rows: list[list], columns: list[str]) -> ExecuteSqlResponse:
    return ExecuteSqlResponse(
        execution_id="exec",
        status="succeeded",
        engine="clickhouse",
        row_count=len(rows),
        normalized_sql="SELECT 1",
        preview=QueryPreview(columns=columns, rows=rows),
        warnings=[],
        violations=[],
    )


def test_search_cascades_to_runtime_when_omd_is_empty() -> None:
    sql_router = MagicMock()
    sql_router.get_connection_for_execution.return_value = SimpleNamespace(
        engine="clickhouse", database="default", catalog=None
    )
    sql_router.execute_sql.side_effect = [
        _sql_response([["dm", "ft_order", "Витрина заказов FoodTech"]], ["database", "name", "comment"]),
        _sql_response([["order_id", "String"], ["order_create_dttm", "DateTime"]], ["name", "type"]),
    ]
    service, execution_service, _ = _service(
        omd_payloads=[_omd_payload([]), _omd_payload([]), _omd_payload([])],
        db_connectors=[_db_connector("clickhouse", "clickhouse")],
        sql_router=sql_router,
    )

    result = service.search(workspace_id="default", query="заказы ft_order фудтех")

    assert [stage.name for stage in result.tried] == [
        "omd_scoped",
        "omd_unscoped",
        "omd_columns",
        "runtime",
    ]
    assert [stage.found for stage in result.tried] == [0, 0, 0, 1]
    assert result.found_by == "runtime"

    omd_calls = [call.kwargs["arguments"] for call in execution_service.execute.call_args_list]
    assert "queryFilter" in omd_calls[0]
    assert "queryFilter" not in omd_calls[1]
    assert "columns.name" in omd_calls[2]["query"]

    tables_sql = sql_router.execute_sql.call_args_list[0].kwargs["sql"]
    assert "system.tables" in tables_sql
    assert "lower(name) LIKE '%ft_order%'" in tables_sql
    assert "comment ILIKE '%заказ%'" in tables_sql

    candidate = result.tables[0]
    assert candidate["fqn"] == "dm.ft_order"
    assert candidate["description"] == "Витрина заказов FoodTech"
    assert candidate["columns"] == ["order_id", "order_create_dttm"]
    assert candidate["connectors"] == ["clickhouse"]
    assert candidate["engine"] == "clickhouse"
    assert candidate["found_by"] == "runtime"


def test_search_marks_sandbox_tables_suspicious_and_ranks_them_last() -> None:
    # Production probe: the top hit for "orders" was a sandbox table; the FoodTech mart came later.
    service, _, _ = _service(
        omd_payloads=[
            _omd_payload([
                _omd_hit(
                    "clickhouse-example.default.sandbox.daily_order_counts",
                    description=None,
                ),
                _omd_hit("Clickhouse-Sales.default.dm.ft_order"),
            ])
        ],
        db_connectors=[_db_connector("clickhouse", "clickhouse")],
    )

    result = service.search(workspace_id="default", query="orders")

    assert [table["fqn"] for table in result.tables] == [
        "Clickhouse-Sales.default.dm.ft_order",
        "clickhouse-example.default.sandbox.daily_order_counts",
    ]
    assert [table["suspicious"] for table in result.tables] == [False, True]
    assert result.tried[0].found == 2


def test_search_compacts_description_and_columns_and_scopes_database() -> None:
    long_description = "слово " * 200
    many_columns = [f"col_{index}" for index in range(40)]
    service, execution_service, _ = _service(
        omd_payloads=[
            _omd_payload([
                _omd_hit(
                    "Clickhouse-Sales.default.dm.ft_order",
                    description=long_description,
                    column_names=many_columns,
                )
            ])
        ],
        db_connectors=[_db_connector("clickhouse", "clickhouse")],
    )

    result = service.search(workspace_id="default", query="заказы", database="dm")

    query_filter = json.loads(execution_service.execute.call_args.kwargs["arguments"]["queryFilter"])
    assert _terms(query_filter, "databaseSchema.name.keyword") == ["dm"]
    candidate = result.tables[0]
    assert len(candidate["description"]) <= 300
    assert candidate["description"].endswith("…")
    assert candidate["columns"] == many_columns[:10]


def test_search_merges_runtime_hits_and_ranks_described_matches_first() -> None:
    # Stand probe 2026-09-03: scoped OpenMetadata search for "заказы фудтех" returned eight
    # undescribed unit-economics tables (dataset...) and the cascade stopped there, while
    # system.tables.comment of dm.ft_order says "Витрина заказов FoodTech".
    sql_router = MagicMock()
    sql_router.get_connection_for_execution.return_value = SimpleNamespace(
        engine="clickhouse", database="default", catalog=None
    )
    sql_router.execute_sql.side_effect = [
        _sql_response([["dm", "ft_order", "Витрина заказов FoodTech"]], ["database", "name", "comment"]),
        _sql_response([["order_id", "String"]], ["name", "type"]),
    ]
    service, _, _ = _service(
        omd_payloads=[
            _omd_payload([
                _omd_hit("Clickhouse-Sales.default.ft_commerce.dataset", description=None),
                _omd_hit("Clickhouse-Sales.default.ft_ops.dataset", description=None),
            ])
        ],
        db_connectors=[_db_connector("clickhouse", "clickhouse")],
        sql_router=sql_router,
    )

    result = service.search(workspace_id="default", query="заказы фудтех")

    assert [stage.name for stage in result.tried] == ["omd_scoped", "runtime"]
    assert [stage.found for stage in result.tried] == [2, 1]
    assert result.found_by == "omd_scoped+runtime"
    assert [table["fqn"] for table in result.tables] == [
        "dm.ft_order",
        "Clickhouse-Sales.default.ft_commerce.dataset",
        "Clickhouse-Sales.default.ft_ops.dataset",
    ]
    assert result.tables[0]["found_by"] == "runtime"

    tables_sql = sql_router.execute_sql.call_args_list[0].kwargs["sql"]
    # Russian words are matched by stem so "заказы" also hits "заказов" in comments.
    assert "comment ILIKE '%заказ%'" in tables_sql
    assert "comment ILIKE '%фудтех%'" in tables_sql


def test_search_keeps_runtime_and_omd_hits_separate_without_explicit_mapping() -> None:
    sql_router = MagicMock()
    sql_router.get_connection_for_execution.return_value = SimpleNamespace(
        engine="clickhouse", database="default", catalog=None
    )
    sql_router.execute_sql.side_effect = [
        _sql_response([["dm", "ft_order", "Витрина заказов FoodTech"]], ["database", "name", "comment"]),
        _sql_response([["order_id", "String"]], ["name", "type"]),
    ]
    service, _, _ = _service(
        omd_payloads=[_omd_payload([_omd_hit("Clickhouse-Sales.default.dm.ft_order")])],
        db_connectors=[_db_connector("clickhouse", "clickhouse")],
        sql_router=sql_router,
    )

    result = service.search(workspace_id="default", query="заказы")

    assert {table["fqn"] for table in result.tables} == {
        "Clickhouse-Sales.default.dm.ft_order",
        "dm.ft_order",
    }
    assert {table["origin"] for table in result.tables} == {"openmetadata", "runtime"}


def test_search_keeps_same_table_from_different_omd_services_separate() -> None:
    service, _, _ = _service(
        omd_payloads=[
            _omd_payload([
                _omd_hit("Clickhouse-Sales.default.dm.ft_order"),
                _omd_hit("clickhouse-example.default.dm.ft_order"),
            ])
        ],
        db_connectors=[_db_connector("clickhouse", "clickhouse")],
    )

    result = service.search(workspace_id="default", query="orders")

    assert {table["service"] for table in result.tables} == {
        "Clickhouse-Sales",
        "clickhouse-example",
    }
    assert all(table["connectors"] == [] for table in result.tables)


def test_search_scopes_exact_metadata_service_without_widening() -> None:
    service, execution_service, _ = _service(
        omd_payloads=[_omd_payload([]), _omd_payload([])],
        db_connectors=[_db_connector("clickhouse", "clickhouse")],
    )

    result = service.search(
        workspace_id="default",
        query="orders order_id",
        service_name="Clickhouse-Sales",
    )

    calls = [call.kwargs["arguments"] for call in execution_service.execute.call_args_list]
    assert len(calls) == 2
    assert all(
        _terms(json.loads(call["queryFilter"]), "service.name.keyword")
        == ["Clickhouse-Sales"]
        for call in calls
    )
    assert [stage.name for stage in result.tried] == ["omd_scoped", "omd_columns", "runtime"]


def test_search_reports_openmetadata_and_runtime_failures() -> None:
    sql_router = MagicMock()
    sql_router.get_connection_for_execution.return_value = SimpleNamespace(
        engine="clickhouse", database="default", catalog=None
    )
    sql_router.execute_sql.return_value = ExecuteSqlResponse(
        execution_id="exec",
        status="failed",
        engine="clickhouse",
        error_message="connection refused",
    )
    service, _, _ = _service(
        omd_payloads=[_omd_failure()] * 3,
        db_connectors=[_db_connector("clickhouse", "clickhouse")],
        sql_router=sql_router,
    )

    result = service.search(workspace_id="default", query="orders order_id")

    assert [stage.status for stage in result.tried] == ["failed"] * 4
    assert result.tried[0].error == "OpenMetadata is unavailable"
    assert result.tried[-1].error == "Catalog query failed for connectors: clickhouse."
    assert result.tables == []


def test_list_metadata_services_reads_all_pages_and_deduplicates() -> None:
    service, execution_service, _ = _service(
        omd_payloads=[
            _omd_payload([_omd_service("Clickhouse-Sales")], next_cursor="page-2"),
            _omd_payload([
                _omd_service("Clickhouse-Sales"),
                _omd_service("greenplum-analytics-prod", "Greenplum"),
            ]),
        ],
        db_connectors=[],
    )

    services = service.list_metadata_services(workspace_id="default")

    assert [item["name"] for item in services] == [
        "Clickhouse-Sales",
        "greenplum-analytics-prod",
    ]
    calls = [call.kwargs["arguments"] for call in execution_service.execute.call_args_list]
    assert calls[0] == {"query": "*", "entityType": "databaseService", "size": 50}
    assert calls[1]["cursor"] == "page-2"


def test_list_metadata_services_ignores_incompatible_search_metadata_collision() -> None:
    service, execution_service, _ = _service(
        omd_payloads=[_omd_payload([_omd_service("Clickhouse-Sales")])],
        db_connectors=[],
    )
    service._mcp_connectors.list_active_tools.return_value = [
        _mdm_catalog_item(),
        _omd_catalog_item(),
    ]

    services = service.list_metadata_services(workspace_id="default")

    assert [item["name"] for item in services] == ["Clickhouse-Sales"]
    assert execution_service.execute.call_args.kwargs["exposed_tool_name"] == (
        "openmetadata__search_metadata"
    )


def test_list_metadata_services_rejects_incompatible_search_metadata_tool() -> None:
    service, execution_service, _ = _service(omd_payloads=[], db_connectors=[])
    service._mcp_connectors.list_active_tools.return_value = [_mdm_catalog_item()]

    try:
        service.list_metadata_services(workspace_id="default")
    except MetadataServiceDiscoveryError as exc:
        assert exc.code == "openmetadata_search_unavailable"
    else:
        raise AssertionError("an incompatible search_metadata tool must not be called")
    execution_service.execute.assert_not_called()


def test_list_metadata_services_reports_remote_failure() -> None:
    service, _, _ = _service(
        omd_payloads=[_omd_failure("Token expired")],
        db_connectors=[],
    )

    try:
        service.list_metadata_services(workspace_id="default")
    except MetadataServiceDiscoveryError as exc:
        assert exc.code == "mcp_tool_call_failed"
        assert exc.message == "Token expired"
    else:
        raise AssertionError("service discovery failure must not look like an empty catalog")


def test_search_runtime_probes_wide_and_ranks_exact_name_match_first() -> None:
    # Stand probe: LIMIT 8 ORDER BY name cut the catalog alphabetically before ranking, so
    # delivery_cpo_* came first and dm.ft_order (empty comment) landed sixth for "ft_order".
    sql_router = MagicMock()
    sql_router.get_connection_for_execution.return_value = SimpleNamespace(
        engine="clickhouse", database="default", catalog=None
    )
    sql_router.execute_sql.side_effect = [
        _sql_response(
            [
                ["dm", "delivery_cpo_partners", "Доставка заказа через партнерские сервисы"],
                ["dm", "ft_order_art", "Товары в заказах FoodTech"],
                ["dm", "ft_order", ""],
            ],
            ["database", "name", "comment"],
        ),
        _sql_response([["order_id", "String"]], ["name", "type"]),
        _sql_response([["order_id", "String"]], ["name", "type"]),
        _sql_response([["order_id", "String"]], ["name", "type"]),
    ]
    service, _, _ = _service(
        omd_payloads=[_omd_payload([]), _omd_payload([]), _omd_payload([])],
        db_connectors=[_db_connector("clickhouse", "clickhouse")],
        sql_router=sql_router,
    )

    result = service.search(workspace_id="default", query="ft_order", size=8)

    tables_sql = sql_router.execute_sql.call_args_list[0].kwargs["sql"]
    assert tables_sql.endswith("LIMIT 200")
    assert [table["fqn"] for table in result.tables] == [
        "dm.ft_order",
        "dm.ft_order_art",
        "dm.delivery_cpo_partners",
    ]


def test_search_translates_business_terms_for_name_probe_and_prefers_shorter_names() -> None:
    # Stand probe for "сколько заказов было в фудтехе 1 мая 2026": comments say "заказ" but table
    # names say "order", and equal scores fell back to alphabetical order (delivery_cpo_* first).
    sql_router = MagicMock()
    sql_router.get_connection_for_execution.return_value = SimpleNamespace(
        engine="clickhouse", database="default", catalog=None
    )
    sql_router.execute_sql.side_effect = [
        _sql_response(
            [
                ["dm", "delivery_cpo_partners", "Доставка заказа через партнерские сервисы"],
                ["dm", "ft_order_agg_whs_partner_frmt_metrics_wow", "Агрегированная витрина заказов"],
                ["dm", "orders_daily_report", "Ежедневный отчёт по заказам FoodTech"],
            ],
            ["database", "name", "comment"],
        ),
        _sql_response([["order_id", "String"]], ["name", "type"]),
        _sql_response([["order_id", "String"]], ["name", "type"]),
        _sql_response([["order_id", "String"]], ["name", "type"]),
    ]
    service, _, _ = _service(
        omd_payloads=[_omd_payload([]), _omd_payload([]), _omd_payload([])],
        db_connectors=[_db_connector("clickhouse", "clickhouse")],
        sql_router=sql_router,
    )

    result = service.search(workspace_id="default", query="сколько заказов было в фудтехе 1 мая 2026")

    tables_sql = sql_router.execute_sql.call_args_list[0].kwargs["sql"]
    assert "lower(name) LIKE '%order%'" in tables_sql
    assert "lower(name) LIKE '%foodtech%'" in tables_sql
    assert "comment ILIKE '%сколько%'" not in tables_sql
    assert [table["fqn"] for table in result.tables] == [
        "dm.orders_daily_report",
        "dm.ft_order_agg_whs_partner_frmt_metrics_wow",
        "dm.delivery_cpo_partners",
    ]


def test_search_runtime_orders_by_name_length_and_reads_columns_after_ranking() -> None:
    # Stand probe: a 40-row alphabetical cut filled the page with adtech.*_orders and never
    # reached dm.orders_daily_report; column lookups then went to the wrong tables.
    sql_router = MagicMock()
    sql_router.get_connection_for_execution.return_value = SimpleNamespace(
        engine="clickhouse", database="default", catalog=None
    )
    sql_router.execute_sql.side_effect = [
        _sql_response(
            [
                ["adtech", "ads_orders", ""],
                ["dm", "orders_daily_report", "Ежедневный отчёт по заказам FoodTech"],
            ],
            ["database", "name", "comment"],
        ),
        _sql_response([["report_date", "Date"]], ["name", "type"]),
        _sql_response([["ad_id", "String"]], ["name", "type"]),
    ]
    service, _, _ = _service(
        omd_payloads=[_omd_payload([]), _omd_payload([]), _omd_payload([])],
        db_connectors=[_db_connector("clickhouse", "clickhouse")],
        sql_router=sql_router,
    )

    result = service.search(workspace_id="default", query="заказы фудтех", size=8)

    tables_sql = sql_router.execute_sql.call_args_list[0].kwargs["sql"]
    assert tables_sql.endswith("ORDER BY length(name), database, name LIMIT 200")
    assert [table["fqn"] for table in result.tables] == ["dm.orders_daily_report", "adtech.ads_orders"]
    first_columns_sql = sql_router.execute_sql.call_args_list[1].kwargs["sql"]
    assert "table = 'orders_daily_report'" in first_columns_sql
    assert result.tables[0]["columns"] == ["report_date"]


def test_search_translated_terms_do_not_win_exact_match_and_staging_layers_rank_lower() -> None:
    # Stand probe: dm.order (no comment) took first place for "заказы фудтех" because the
    # translated term "order" scored as an exact name; stg/imp_prod copies tied with dm marts.
    sql_router = MagicMock()
    sql_router.get_connection_for_execution.return_value = SimpleNamespace(
        engine="clickhouse", database="default", catalog=None
    )
    sql_router.execute_sql.side_effect = [
        _sql_response(
            [
                ["dm", "order", ""],
                ["dm", "ft_order", ""],
                ["stg", "ft_order", "Витрина заказов FoodTech"],
                ["dm", "ft_order_cheque", "Витрина заказов/чеков FoodTech"],
            ],
            ["database", "name", "comment"],
        ),
    ] + [_sql_response([["order_id", "String"]], ["name", "type"])] * 4
    service, _, _ = _service(
        omd_payloads=[_omd_payload([]), _omd_payload([]), _omd_payload([])],
        db_connectors=[_db_connector("clickhouse", "clickhouse")],
        sql_router=sql_router,
    )

    result = service.search(workspace_id="default", query="заказы фудтех", size=8)

    assert [table["fqn"] for table in result.tables] == [
        "dm.ft_order_cheque",
        "stg.ft_order",
        "dm.order",
        "dm.ft_order",
    ]
    assert result.tables[1]["layer"] == "staging"
    assert result.tables[0]["layer"] is None
