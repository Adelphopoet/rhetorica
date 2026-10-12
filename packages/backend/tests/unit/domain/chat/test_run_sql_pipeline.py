from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import (
    EngineConnectionConfig,
    ExecuteSqlResponse,
    QueryPreview,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.sql.run_sql_pipeline import RunSqlPipeline
from rhetorica_backend.domain.chat.sql.semantic_preflight import (
    SqlPreflightResult,
    SqlStatementInspection,
)


def test_successful_sql_execution_does_not_require_legacy_metadata_usage_hook() -> None:
    now = datetime.now(tz=UTC)
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="clickhouse",
        port=9000,
        username="default",
        database="analytics",
    )
    connector = ConnectorItem(
        id="connector-1",
        name="clickhouse",
        workspace_id="default",
        connection=connection,
        has_password=False,
        created_at=now,
        updated_at=now,
    )
    sql_router = MagicMock()
    sql_router.select_connector.return_value = connector
    sql_router.get_connection_for_execution.return_value = connection
    sql_router.execute_sql.return_value = ExecuteSqlResponse(
        execution_id="execution-1",
        status="succeeded",
        engine="clickhouse",
        normalized_sql="SELECT sum(net_revenue) FROM dm.orders_daily_report",
        row_count=1,
        preview=QueryPreview(columns=["net_revenue"], rows=[[3592200000]]),
    )
    preflight = MagicMock()
    preflight.check.return_value = SqlPreflightResult(
        status="pass",
        referenced_tables=["dm.orders_daily_report"],
        referenced_columns=["net_revenue"],
    )
    optimizer = MagicMock()
    optimizer.should_optimize.return_value = False
    support = SimpleNamespace(
        _requested_connector_name=lambda arguments: arguments.get("connector_name"),
        _prefer_context_connector=lambda **kwargs: kwargs["selected_connector"],
        _unavailable_context_connector_error=lambda **_kwargs: None,
        _normalize_sql_identifiers=lambda **kwargs: kwargs["sql"],
        _sync_execution_zone=lambda **_kwargs: None,
        _hydrate_preflight_table_schemas=lambda **_kwargs: SqlStatementInspection(
            parsed=True,
            referenced_tables=["dm.orders_daily_report"],
            referenced_columns=["net_revenue"],
        ),
        _preflight_metadata_context=lambda **_kwargs: (
            ["dm.orders_daily_report"],
            {"dm.orders_daily_report": {"net_revenue"}},
        ),
        _preflight_connector_name=lambda **kwargs: kwargs["selected_connector"].name,
        _can_bypass_preflight_failure=lambda **_kwargs: False,
        _hydrate_context_from_execution=lambda **_kwargs: None,
    )
    pipeline = RunSqlPipeline(
        sql_router=sql_router,
        semantic_preflight_service=preflight,
        sql_optimization_service=optimizer,
        support=support,
    )

    result = pipeline.execute(
        request=ChatAskRequest(question="Give me ecom net revenue"),
        arguments={
            "sql": "SELECT sum(net_revenue) FROM dm.orders_daily_report",
            "connector_name": "clickhouse",
        },
        db_connectors=[connector],
        state=ToolOrchestrationState(),
    )

    assert result["ok"] is True
    assert result["execution"]["status"] == "succeeded"
    sql_router.execute_sql.assert_called_once()
