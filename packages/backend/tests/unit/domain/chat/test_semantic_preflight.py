"""Tests for semantic SQL preflight."""

from rhetorica_backend.domain.chat.sql.semantic_preflight import (
    SemanticSqlPreflightService,
)


def test_preflight_allows_clickhouse_system_tables_without_metadata_context() -> None:
    service = SemanticSqlPreflightService()

    result = service.check(
        sql=(
            "SELECT name FROM system.tables "
            "WHERE database = 'default' AND name LIKE 'ft_main%'"
        ),
        dialect="clickhouse",
        engine="clickhouse",
        context_tables=[],
        metadata_table_columns={},
        requested_connector_name=None,
        selected_connector_name=None,
    )

    assert result.status == "pass"
    assert result.errors == []


def test_preflight_allows_show_databases_without_metadata_context() -> None:
    service = SemanticSqlPreflightService()

    result = service.check(
        sql="SHOW DATABASES",
        dialect="clickhouse",
        engine="clickhouse",
        context_tables=[],
        metadata_table_columns={},
        requested_connector_name=None,
        selected_connector_name=None,
    )

    assert result.status == "pass"
    assert result.errors == []


def test_preflight_suggests_similar_columns_for_missing_column() -> None:
    service = SemanticSqlPreflightService()

    result = service.check(
        sql=(
            "SELECT toDate(order_created_dttm) AS order_date, count() AS orders "
            "FROM dm.ft_order "
            "GROUP BY toDate(order_created_dttm)"
        ),
        dialect="clickhouse",
        engine="clickhouse",
        context_tables=["Sample-ClickHouse.default.dm.ft_order"],
        metadata_table_columns={
            "Sample-ClickHouse.default.dm.ft_order": {
                "order_create_dttm",
                "order_update_dttm",
                "order_finish_dttm",
            }
        },
        requested_connector_name="clickhouse",
        selected_connector_name="clickhouse",
    )

    assert result.status == "fail"
    missing_column = next(error for error in result.errors if error.code == "missing_column")
    assert "Similar columns: order_create_dttm" in missing_column.message
    assert missing_column.identifier == "order_created_dttm"
    assert missing_column.table == "Sample-ClickHouse.default.dm.ft_order"
    assert "order_create_dttm" in missing_column.suggestions


def test_preflight_allows_group_by_select_alias() -> None:
    service = SemanticSqlPreflightService()

    result = service.check(
        sql=(
            "SELECT toDate(order_create_dttm) AS order_date, count() AS orders "
            "FROM dm.ft_order "
            "GROUP BY order_date"
        ),
        dialect="clickhouse",
        engine="clickhouse",
        context_tables=["Sample-ClickHouse.default.dm.ft_order"],
        metadata_table_columns={
            "Sample-ClickHouse.default.dm.ft_order": {
                "order_create_dttm",
            }
        },
        requested_connector_name="clickhouse",
        selected_connector_name="clickhouse",
    )

    assert result.status == "pass"
    assert result.errors == []


def test_preflight_allows_cte_join_with_physical_tables_only() -> None:
    service = SemanticSqlPreflightService()

    result = service.check(
        sql=(
            "WITH orders AS ("
            "SELECT user_id, count() AS orders_count "
            "FROM dm.ft_order "
            "GROUP BY user_id"
            ") "
            "SELECT u.segment, o.orders_count "
            "FROM dm.dim_user AS u "
            "JOIN orders AS o ON u.user_id = o.user_id"
        ),
        dialect="clickhouse",
        engine="clickhouse",
        context_tables=[
            "Sample-ClickHouse.default.dm.ft_order",
            "Sample-ClickHouse.default.dm.dim_user",
        ],
        metadata_table_columns={
            "Sample-ClickHouse.default.dm.ft_order": {
                "user_id",
            },
            "Sample-ClickHouse.default.dm.dim_user": {
                "user_id",
                "segment",
            },
        },
        requested_connector_name="clickhouse",
        selected_connector_name="clickhouse",
    )

    assert result.status == "pass"
    assert result.errors == []
    assert result.referenced_tables == ["dm.ft_order", "dm.dim_user"]


def test_preflight_allows_clickhouse_cte_analytics_query_with_physical_tables_only() -> None:
    service = SemanticSqlPreflightService()

    result = service.check(
        sql=(
            "WITH nonholiday_daily AS ("
            "SELECT report_date, toDayOfWeek(report_date) AS dow, "
            "sum(gmv) AS gmv, sum(orders) AS orders "
            "FROM dm.orders_daily_report "
            "WHERE report_date BETWEEN '2025-01-01' AND '2026-03-31' "
            "GROUP BY report_date"
            "), "
            "dow_avg AS ("
            "SELECT dow, avg(gmv) AS avg_gmv "
            "FROM nonholiday_daily "
            "GROUP BY dow"
            "), "
            "holiday_gmv AS ("
            "SELECT report_date, toDayOfWeek(report_date) AS dow, "
            "sum(gmv) AS gmv, sum(orders) AS orders "
            "FROM dm.orders_daily_report "
            "WHERE report_date IN (toDate('2025-01-01'), toDate('2026-01-01')) "
            "GROUP BY report_date"
            ") "
            "SELECT toString(h.report_date) AS date, "
            "formatDateTime(h.report_date, '%a') AS day_of_week, "
            "round(h.gmv / 1e6, 1) AS gmv_mln, "
            "round(100.0 * (h.gmv - dow.avg_gmv) / dow.avg_gmv, 1) AS deviation_pct "
            "FROM holiday_gmv h "
            "JOIN dow_avg dow ON dow.dow = h.dow "
            "ORDER BY h.report_date"
        ),
        dialect="clickhouse",
        engine="clickhouse",
        context_tables=["Sample-ClickHouse.default.dm.orders_daily_report"],
        metadata_table_columns={
            "Sample-ClickHouse.default.dm.orders_daily_report": {
                "report_date",
                "gmv",
                "orders",
            }
        },
        requested_connector_name="clickhouse",
        selected_connector_name="clickhouse",
    )

    assert result.status == "pass"
    assert result.errors == []
    assert result.referenced_tables == ["dm.orders_daily_report"]


def test_preflight_reports_parse_failure_as_failed_sql_not_uncertain_metadata() -> None:
    service = SemanticSqlPreflightService()

    result = service.check(
        sql="SELECT 1 AS deviation_%",
        dialect="clickhouse",
        engine="clickhouse",
        context_tables=[],
        metadata_table_columns={},
        requested_connector_name=None,
        selected_connector_name=None,
    )

    assert result.status == "fail"
    assert result.errors[0].code == "sql_parse_failed"
    assert "Unable to parse SQL for semantic preflight" in result.errors[0].message


def test_preflight_blocks_cross_database_references() -> None:
    service = SemanticSqlPreflightService()

    result = service.check(
        sql=(
            "SELECT o.order_id, c.segment "
            "FROM dm.ft_order AS o "
            "JOIN mart.dim_customer AS c ON o.customer_id = c.customer_id"
        ),
        dialect="clickhouse",
        engine="clickhouse",
        context_tables=[
            "Sample-ClickHouse.default.dm.ft_order",
            "Sample-ClickHouse.analytics.mart.dim_customer",
        ],
        metadata_table_columns={
            "Sample-ClickHouse.default.dm.ft_order": {
                "order_id",
                "customer_id",
            },
            "Sample-ClickHouse.analytics.mart.dim_customer": {
                "customer_id",
                "segment",
            },
        },
        requested_connector_name="clickhouse",
        selected_connector_name="clickhouse",
    )

    assert result.status == "fail"
    assert any(error.code == "cross_database_reference" for error in result.errors)


def test_preflight_uses_fqn_columns_for_schema_qualified_alias_table() -> None:
    service = SemanticSqlPreflightService()

    result = service.check(
        sql=(
            "SELECT toDate(order_create_dttm) AS order_date, count() AS order_count "
            "FROM dm.ft_order "
            "WHERE order_create_dttm >= '2026-04-01' "
            "AND order_create_dttm < '2026-05-01' "
            "GROUP BY toDate(order_create_dttm) "
            "ORDER BY order_date"
        ),
        dialect="clickhouse",
        engine="clickhouse",
        context_tables=[
            "Sample-ClickHouse.default.dm.ft_order",
            "Sample-ClickHouse.default.imp_prod.ft_order",
            "dm.ft_order",
        ],
        metadata_table_columns={
            "Sample-ClickHouse.default.dm.ft_order": {
                "order_create_dttm",
                "order_id",
            },
            "Sample-ClickHouse.default.imp_prod.ft_order": {
                "order_id",
            },
        },
        requested_connector_name="clickhouse",
        selected_connector_name="clickhouse",
    )

    assert result.status == "pass"
    assert result.errors == []


def test_preflight_blocks_clickhouse_system_tables_for_trino() -> None:
    service = SemanticSqlPreflightService()

    result = service.check(
        sql="SELECT database, name FROM system.tables WHERE name = 'ft_order'",
        dialect="trino",
        engine="trino",
        context_tables=[],
        metadata_table_columns={},
        requested_connector_name="trino-adhoc",
        selected_connector_name="trino-adhoc",
    )

    assert result.status == "fail"
    assert any(error.code == "unsupported_syntax_for_engine" for error in result.errors)
    assert any("system.tables" in error.message for error in result.errors)


def test_preflight_blocks_clickhouse_prewhere_for_trino() -> None:
    service = SemanticSqlPreflightService()

    result = service.check(
        sql="SELECT order_id FROM iceberg.ft_order PREWHERE order_id > 0",
        dialect="trino",
        engine="trino",
        context_tables=["DWH-iceberrg.iceberg.iceberg.ft_order"],
        metadata_table_columns={
            "DWH-iceberrg.iceberg.iceberg.ft_order": {"order_id"}
        },
        requested_connector_name="trino-adhoc",
        selected_connector_name="trino-adhoc",
    )

    assert result.status == "fail"
    assert any(error.code == "unsupported_syntax_for_engine" for error in result.errors)
    assert any("PREWHERE" in error.message for error in result.errors)


def test_preflight_prefers_longest_suffix_match_for_trino_tables() -> None:
    service = SemanticSqlPreflightService()

    result = service.check(
        sql="SELECT order_source FROM iceberg.dm_ft.ft_order",
        dialect="trino",
        engine="trino",
        context_tables=[
            "Sample-ClickHouse.default.dm.ft_order",
            "trino_omni.iceberg.dm_ft.ft_order",
        ],
        metadata_table_columns={
            "Sample-ClickHouse.default.dm.ft_order": {"order_id"},
            "trino_omni.iceberg.dm_ft.ft_order": {"order_source"},
        },
        requested_connector_name="trino-adhoc",
        selected_connector_name="trino-adhoc",
    )

    assert result.status == "pass"
    assert result.errors == []
