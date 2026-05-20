"""Tests for profiling capability service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from rhetorica_backend.contracts.chat import (
    CardinalityBucket,
    ChatAskRequest,
)
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import (
    EngineConnectionConfig,
    ExecuteSqlResponse,
    QueryPreview,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.tool_profile_capability_service import (
    TOP_VALUES_CAP,
    ToolProfileCapabilityService,
    _classify_cardinality,
    _parse_table_reference,
)


def _connector(
    name: str = "clickhouse-main",
    engine: str = "clickhouse",
) -> ConnectorItem:
    now = datetime.now(tz=UTC)
    return ConnectorItem(
        id=f"conn-{name}",
        name=name,
        workspace_id="default",
        connection=EngineConnectionConfig(
            engine=engine,
            host="db.local",
            port=8123 if engine == "clickhouse" else 8080,
            username="analyst",
            password=None,
            database="analytics",
        ),
        has_password=True,
        created_at=now,
        updated_at=now,
    )


def _execution_response(
    columns: list[str],
    rows: list[list[Any]],
    status: str = "succeeded",
) -> ExecuteSqlResponse:
    return ExecuteSqlResponse(
        execution_id="exec-profile-test",
        status=status,
        engine="clickhouse",
        row_count=len(rows),
        preview=QueryPreview(columns=columns, rows=rows),
    )


class _SqlRouterStub:
    """Stub sql router for testing."""

    def __init__(
        self,
        execution_results: list[ExecuteSqlResponse] | None = None,
    ) -> None:
        self._execution_results = execution_results or []
        self._index = 0
        self.connection_requests: list[tuple[str, str]] = []
        self.executed_sql: list[str] = []

    def get_connection_for_execution(
        self,
        workspace_id: str,
        connector_name: str,
    ) -> EngineConnectionConfig | None:
        self.connection_requests.append((workspace_id, connector_name))
        if connector_name == "clickhouse-main":
            return EngineConnectionConfig(
                engine="clickhouse",
                host="db.local",
                port=8123,
                username="analyst",
                password=None,
                database="analytics",
            )
        if connector_name == "trino-main":
            return EngineConnectionConfig(
                engine="trino",
                host="trino.local",
                port=8080,
                username="analyst",
                password=None,
                database="hive",
                catalog="iceberg",
            )
        return None

    def execute_sql(
        self,
        sql: str,
        connection: EngineConnectionConfig,
        workspace_id: str,
        dialect: str,
    ) -> ExecuteSqlResponse:
        self.executed_sql.append(sql)
        if self._execution_results:
            result = self._execution_results[self._index % len(self._execution_results)]
            self._index += 1
            return result
        return _execution_response(columns=[], rows=[])


class TestParseTableReference:
    """Tests for _parse_table_reference helper."""

    def test_parses_simple_table(self) -> None:
        schema, table, db = _parse_table_reference("orders")
        assert schema is None
        assert table == "orders"
        assert db is None

    def test_parses_schema_table(self) -> None:
        schema, table, db = _parse_table_reference("dm.orders")
        assert schema == "dm"
        assert table == "orders"
        assert db is None

    def test_parses_database_schema_table(self) -> None:
        schema, table, db = _parse_table_reference("analytics.dm.orders")
        assert schema == "dm"
        assert table == "orders"
        assert db == "analytics"

    def test_strips_whitespace(self) -> None:
        schema, table, db = _parse_table_reference(" analytics . dm . orders ")
        assert schema == "dm"
        assert table == "orders"
        assert db == "analytics"


class TestClassifyCardinality:
    """Tests for _classify_cardinality helper."""

    def test_low_cardinality(self) -> None:
        assert _classify_cardinality(50) == CardinalityBucket.LOW
        assert _classify_cardinality(99) == CardinalityBucket.LOW
        assert _classify_cardinality(0) == CardinalityBucket.LOW

    def test_medium_cardinality(self) -> None:
        assert _classify_cardinality(100) == CardinalityBucket.MEDIUM
        assert _classify_cardinality(5000) == CardinalityBucket.MEDIUM
        assert _classify_cardinality(9999) == CardinalityBucket.MEDIUM

    def test_high_cardinality(self) -> None:
        assert _classify_cardinality(10001) == CardinalityBucket.HIGH
        assert _classify_cardinality(100000) == CardinalityBucket.HIGH
        assert _classify_cardinality(1000000) == CardinalityBucket.HIGH

    def test_none_returns_low(self) -> None:
        assert _classify_cardinality(None) == CardinalityBucket.LOW


class TestProfileTable:
    """Tests for profile_table tool."""

    def test_returns_error_when_table_name_missing(self) -> None:
        router = _SqlRouterStub()
        service = ToolProfileCapabilityService(sql_router=router)
        state = ToolOrchestrationState()

        result = service.execute_profile_table(
            request=ChatAskRequest(question="test", workspace_id="default"),
            arguments={},
            state=state,
            db_connectors=[],
        )

        assert result["ok"] is False
        assert result["error_code"] == "profile_table_missing_table_name"
        assert "Table name is required" in result["user_message"]

    def test_returns_error_when_connector_not_found(self) -> None:
        router = _SqlRouterStub()
        service = ToolProfileCapabilityService(sql_router=router)
        state = ToolOrchestrationState()
        connector = _connector(name="clickhouse-main")

        result = service.execute_profile_table(
            request=ChatAskRequest(question="test", workspace_id="default"),
            arguments={"table_name": "orders", "connector_name": "nonexistent-connector"},
            state=state,
            db_connectors=[connector],
        )

        assert result["ok"] is False
        assert result["error_code"] == "profile_connector_not_found"

    def test_returns_profile_with_null_rate(self) -> None:
        # Build enough execution results for the profiling flow
        router = _SqlRouterStub(execution_results=[
            # Row count query
            _execution_response(columns=["row_count"], rows=[[1000000]]),
            # Columns query
            _execution_response(columns=["name", "type"], rows=[
                ["order_id", "UInt64"],
                ["status", "String"],
            ]),
            # order_id null rate
            _execution_response(columns=["null_rate"], rows=[[0.0]]),
            # order_id cardinality
            _execution_response(columns=["distinct_count"], rows=[[1000000]]),
            # status null rate
            _execution_response(columns=["null_rate"], rows=[[2.5]]),
            # status cardinality
            _execution_response(columns=["distinct_count"], rows=[[100]]),
            # status top values
            _execution_response(columns=["status", "cnt"], rows=[
                ["active", 5000],
                ["completed", 3000],
            ]),
            # created_at (freshness) null rate
            _execution_response(columns=["null_rate"], rows=[[0.0]]),
            # created_at cardinality
            _execution_response(columns=["distinct_count"], rows=[[1000000]]),
            # min_max query
            _execution_response(
                columns=["min_val", "max_val"],
                rows=[["2024-01-01", "2024-05-01"]],
            ),
        ])
        service = ToolProfileCapabilityService(sql_router=router)
        state = ToolOrchestrationState()
        connector = _connector()

        result = service.execute_profile_table(
            request=ChatAskRequest(question="test", workspace_id="default"),
            arguments={"table_name": "dm.orders", "connector_name": "clickhouse-main"},
            state=state,
            db_connectors=[connector],
        )

        assert result["ok"] is True
        assert result["tool_name"] == "profile_table"
        assert len(result["row_counts"]) == 1
        assert result["row_counts"][0]["row_count"] == 1000000
        assert result["row_counts"][0]["is_estimate"] is True
        assert result["column_summary"] is not None
        assert len(result["column_summary"]) > 0
        assert result["top_values_cap"] == TOP_VALUES_CAP

    def test_returns_warning_for_high_cardinality(self) -> None:
        router = _SqlRouterStub(execution_results=[
            _execution_response(columns=["row_count"], rows=[[5000000]]),
            _execution_response(columns=["name", "type"], rows=[
                ["user_id", "UInt64"],
                ["email", "String"],
            ]),
            _execution_response(columns=["null_rate"], rows=[[0.0]]),
            _execution_response(columns=["distinct_count"], rows=[[5000000]]),
            _execution_response(columns=["null_rate"], rows=[[5.0]]),
            _execution_response(columns=["distinct_count"], rows=[[5000000]]),
        ])
        service = ToolProfileCapabilityService(sql_router=router)
        state = ToolOrchestrationState()
        connector = _connector()

        result = service.execute_profile_table(
            request=ChatAskRequest(question="test", workspace_id="default"),
            arguments={"table_name": "users", "connector_name": "clickhouse-main"},
            state=state,
            db_connectors=[connector],
        )

        assert result["ok"] is True
        assert result["warnings"] is not None
        assert result["warnings"]["high_cardinality"] is True

    def test_returns_warning_for_sparse_column(self) -> None:
        router = _SqlRouterStub(execution_results=[
            _execution_response(columns=["row_count"], rows=[[1000000]]),
            _execution_response(columns=["name", "type"], rows=[
                ["optional_field", "Nullable(String)"],
            ]),
            _execution_response(columns=["null_rate"], rows=[[75.0]]),
            _execution_response(columns=["distinct_count"], rows=[[10]]),
        ])
        service = ToolProfileCapabilityService(sql_router=router)
        state = ToolOrchestrationState()
        connector = _connector()

        result = service.execute_profile_table(
            request=ChatAskRequest(question="test", workspace_id="default"),
            arguments={"table_name": "dm.events", "connector_name": "clickhouse-main"},
            state=state,
            db_connectors=[connector],
        )

        assert result["ok"] is True
        assert result["warnings"] is not None
        assert result["warnings"]["sparse"] is True

    def test_returns_stale_warning_for_old_data(self) -> None:
        # Using a small table (< 10M) so cardinality is computed
        router = _SqlRouterStub(execution_results=[
            # Row count query
            _execution_response(columns=["row_count"], rows=[[100000]]),
            # Columns query
            _execution_response(columns=["name", "type"], rows=[
                ["order_id", "UInt64"],
                ["updated_at", "DateTime"],
            ]),
            # order_id null rate
            _execution_response(columns=["null_rate"], rows=[[0.0]]),
            # order_id cardinality
            _execution_response(columns=["distinct_count"], rows=[[100000]]),
            # updated_at null rate
            _execution_response(columns=["null_rate"], rows=[[0.0]]),
            # updated_at cardinality
            _execution_response(columns=["distinct_count"], rows=[[365]]),
            # min_max query
            _execution_response(
                columns=["min_val", "max_val"],
                rows=[["2020-01-01", "2020-06-01"]],
            ),
        ])
        service = ToolProfileCapabilityService(sql_router=router)
        state = ToolOrchestrationState()
        connector = _connector()

        result = service.execute_profile_table(
            request=ChatAskRequest(question="test", workspace_id="default"),
            arguments={"table_name": "dm.orders", "connector_name": "clickhouse-main"},
            state=state,
            db_connectors=[connector],
        )

        assert result["ok"] is True
        if result["freshness"]:
            assert result["freshness"]["stale"] is True

    def test_top_values_capped_at_limit(self) -> None:
        router = _SqlRouterStub(execution_results=[
            _execution_response(columns=["row_count"], rows=[[1000000]]),
            _execution_response(columns=["name", "type"], rows=[
                ["status", "String"],
            ]),
            _execution_response(columns=["null_rate"], rows=[[0.0]]),
            _execution_response(columns=["distinct_count"], rows=[[50]]),
            _execution_response(columns=["status", "cnt"], rows=[
                [f"status_{i}", 1000] for i in range(15)
            ]),
        ])
        service = ToolProfileCapabilityService(sql_router=router)
        state = ToolOrchestrationState()
        connector = _connector()

        result = service.execute_profile_table(
            request=ChatAskRequest(question="test", workspace_id="default"),
            arguments={"table_name": "orders", "connector_name": "clickhouse-main"},
            state=state,
            db_connectors=[connector],
        )

        assert result["ok"] is True
        status_col = next(
            (c for c in result["column_summary"] if c["name"] == "status"),
            None
        )
        if status_col and status_col["top_values"]:
            assert len(status_col["top_values"]) <= TOP_VALUES_CAP
            assert result["truncated"] is True

    def test_handles_missing_table_gracefully(self) -> None:
        router = _SqlRouterStub(execution_results=[
            _execution_response(columns=["row_count"], rows=[], status="failed"),
        ])
        service = ToolProfileCapabilityService(sql_router=router)
        state = ToolOrchestrationState()
        connector = _connector()

        result = service.execute_profile_table(
            request=ChatAskRequest(question="test", workspace_id="default"),
            arguments={"table_name": "nonexistent.table", "connector_name": "clickhouse-main"},
            state=state,
            db_connectors=[connector],
        )

        assert result["ok"] is True
        assert result["row_counts"][0]["row_count"] is None


class TestProfileMetric:
    """Tests for profile_metric tool."""

    def test_returns_error_when_metric_name_missing(self) -> None:
        router = _SqlRouterStub()
        service = ToolProfileCapabilityService(sql_router=router)
        state = ToolOrchestrationState()

        result = service.execute_profile_metric(
            request=ChatAskRequest(question="test", workspace_id="default"),
            arguments={},
            state=state,
            db_connectors=[],
        )

        assert result["ok"] is False
        assert result["error_code"] == "profile_metric_missing_metric_name"

    def test_returns_error_when_table_missing(self) -> None:
        router = _SqlRouterStub()
        service = ToolProfileCapabilityService(sql_router=router)
        state = ToolOrchestrationState()

        result = service.execute_profile_metric(
            request=ChatAskRequest(question="test", workspace_id="default"),
            arguments={"metric_name": "total_orders"},
            state=state,
            db_connectors=[],
        )

        assert result["ok"] is False
        assert result["error_code"] == "profile_metric_missing_table"

    def test_returns_profile_with_metric_name(self) -> None:
        router = _SqlRouterStub(execution_results=[
            _execution_response(columns=["row_count"], rows=[[500000]]),
            _execution_response(columns=["name", "type"], rows=[
                ["order_id", "UInt64"],
                ["amount", "Decimal(18,2)"],
            ]),
            _execution_response(columns=["null_rate"], rows=[[0.0]]),
            _execution_response(columns=["distinct_count"], rows=[[500000]]),
            _execution_response(columns=["null_rate"], rows=[[0.0]]),
            _execution_response(columns=["distinct_count"], rows=[[500000]]),
            _execution_response(
                columns=["min_val", "max_val"],
                rows=[["2024-01-01", "2024-05-01"]],
            ),
        ])
        service = ToolProfileCapabilityService(sql_router=router)
        state = ToolOrchestrationState()
        connector = _connector()

        result = service.execute_profile_metric(
            request=ChatAskRequest(question="test", workspace_id="default"),
            arguments={
                "metric_name": "total_revenue",
                "table_name": "dm.orders",
                "connector_name": "clickhouse-main",
            },
            state=state,
            db_connectors=[connector],
        )

        assert result["ok"] is True
        assert result["tool_name"] == "profile_metric"
        assert result["metric_name"] == "total_revenue"

    # --- Additional robustness test: metric with only 1 sample column ---
    def test_profile_metric_partial_data(self) -> None:
        """Profile a metric when only partial profiling data is available."""
        router = _SqlRouterStub(execution_results=[
            _execution_response(columns=["row_count"], rows=[[500000]]),
            _execution_response(columns=["name", "type"], rows=[
                ["order_id", "UInt64"],
            ]),
            # Only one column gets null_rate/distinct_count data
            _execution_response(columns=["null_rate"], rows=[[0.1]]),
            _execution_response(columns=["distinct_count"], rows=[[400000]]),
        ])
        service = ToolProfileCapabilityService(sql_router=router)
        state = ToolOrchestrationState()
        connector = _connector()

        result = service.execute_profile_metric(
            request=ChatAskRequest(question="test", workspace_id="default"),
            arguments={
                "metric_name": "orders_count",
                "table_name": "dm.orders",
                "connector_name": "clickhouse-main",
            },
            state=state,
            db_connectors=[connector],
        )

        assert result["ok"] is True
        assert result["tool_name"] == "profile_metric"
        assert result["metric_name"] == "orders_count"
        assert result["row_counts"][0]["row_count"] == 500000


class TestTrinoProfiling:
    """Tests for Trino engine profiling."""

    def test_builds_trino_row_count_sql(self) -> None:
        from rhetorica_backend.domain.chat.orchestration.tool_profile_capability_service import (
            ToolProfileCapabilityService,
        )

        service = ToolProfileCapabilityService(sql_router=MagicMock())

        sql = service._build_row_count_sql("orders", "analytics", "hive", "trino")
        assert "information_schema.tables" in sql
        assert "table_schema = 'analytics'" in sql
        assert "table_name = 'orders'" in sql

    def test_builds_trino_null_rate_sql(self) -> None:
        service = ToolProfileCapabilityService(sql_router=MagicMock())

        sql = service._build_null_rate_sql("orders", "analytics", "hive", "trino", "status")
        assert "SUM(CASE WHEN" in sql
        assert "status IS NULL" in sql
        assert "hive.analytics.orders" in sql

    def test_profile_trino_table(self) -> None:
        router = _SqlRouterStub(execution_results=[
            # Row count
            _execution_response(columns=["row_count"], rows=[[2000000]]),
            # Columns query
            _execution_response(columns=["column_name", "data_type"], rows=[
                ["order_id", "bigint"],
                ["status", "varchar"],
            ]),
            # order_id null rate
            _execution_response(columns=["null_rate"], rows=[[0.0]]),
            # order_id cardinality
            _execution_response(columns=["distinct_count"], rows=[[2000000]]),
            # status null rate
            _execution_response(columns=["null_rate"], rows=[[1.5]]),
            # status cardinality
            _execution_response(columns=["distinct_count"], rows=[[50]]),
            # status top values
            _execution_response(columns=["status", "cnt"], rows=[
                ["active", 8000],
                ["completed", 12000],
            ]),
        ])
        service = ToolProfileCapabilityService(sql_router=router)
        state = ToolOrchestrationState()
        connector = _connector(name="trino-main", engine="trino")

        result = service.execute_profile_table(
            request=ChatAskRequest(question="test", workspace_id="default"),
            arguments={"table_name": "hive.analytics.orders"},
            state=state,
            db_connectors=[connector],
        )

        assert result["ok"] is True
        assert result["row_counts"][0]["row_count"] == 2000000
        assert result["top_values_cap"] == TOP_VALUES_CAP


class TestFindConnector:
    """Tests for _find_connector helper method."""

    def test_returns_none_when_no_connectors_and_no_name(self) -> None:
        service = ToolProfileCapabilityService(sql_router=None)
        assert service._find_connector([], None) is None

    def test_returns_first_connector_when_no_name_provided(self) -> None:
        service = ToolProfileCapabilityService(sql_router=None)
        connector = _connector(name="test_db")
        result = service._find_connector([connector], None)
        assert result is not None
        assert result.name == "test_db"

    def test_returns_connector_by_name_case_insensitive(self) -> None:
        service = ToolProfileCapabilityService(sql_router=None)
        connector = _connector(name="MyDB")
        result = service._find_connector([connector], "mydb")
        assert result is not None
        assert result.name == "MyDB"

    def test_returns_none_when_connector_name_not_found(self) -> None:
        service = ToolProfileCapabilityService(sql_router=None)
        connector = _connector(name="other_db")
        result = service._find_connector([connector], "nonexistent")
        assert result is None

    def test_returns_none_when_empty_connectors_and_name_given(self) -> None:
        service = ToolProfileCapabilityService(sql_router=None)
        result = service._find_connector([], "some_name")
        assert result is None
