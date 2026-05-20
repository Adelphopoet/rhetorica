from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.domain.chat.metadata import TableContext
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.tool_execution_connector_scope_policy import (
    ToolExecutionConnectorScopePolicy,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_context_hydration_helpers import (
    ToolExecutionContextHydrationHelpers,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_metadata_context_service import (
    ToolExecutionMetadataContextService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_metadata_runtime_service import (
    ToolExecutionMetadataRuntimeService,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_runtime_connector_policy import (
    ToolExecutionRuntimeConnectorPolicy,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_runtime_sql_policy import (
    ToolExecutionRuntimeSqlPolicy,
)


def _connector(
    *,
    name: str,
    service: str,
    database: str,
    schema: str,
) -> ConnectorItem:
    now = datetime.now(tz=UTC)
    return ConnectorItem(
        id=f"id-{name}",
        name=name,
        workspace_id="ws-1",
        connection=EngineConnectionConfig(
            engine="clickhouse",
            host="localhost",
            port=9000,
            username="default",
            database=database,
            extra_settings={
                "metadata_service": service,
                "metadata_database": database,
                "metadata_schema": schema,
            },
        ),
        has_password=False,
        created_at=now,
        updated_at=now,
    )


def _runtime_services() -> tuple[
    ToolExecutionConnectorScopePolicy,
    ToolExecutionMetadataContextService,
    ToolExecutionRuntimeSqlPolicy,
]:
    connector_scope_policy = ToolExecutionConnectorScopePolicy()
    metadata_context_service = ToolExecutionMetadataContextService(
        connector_scope_policy=connector_scope_policy
    )
    runtime_sql_policy = ToolExecutionRuntimeSqlPolicy(
        connector_scope_policy=connector_scope_policy
    )
    return connector_scope_policy, metadata_context_service, runtime_sql_policy


def test_runtime_connector_policy_prefers_connector_from_context_table_scope() -> None:
    connector_scope_policy, _, _ = _runtime_services()
    policy = ToolExecutionRuntimeConnectorPolicy(
        connector_scope_policy=connector_scope_policy
    )
    warehouse = _connector(
        name="warehouse",
        service="Sample-ClickHouse",
        database="analytics",
        schema="dm",
    )
    mart = _connector(
        name="mart",
        service="Sample-ClickHouse",
        database="analytics_mart",
        schema="dm",
    )
    state = ToolOrchestrationState(
        context_tables=["Sample-ClickHouse.analytics_mart.dm.sales_orders"]
    )

    selected = policy.prefer_context_connector(
        request=ChatAskRequest(question="Revenue?", workspace_id="ws-1", run_sql=True),
        requested_connector_name=None,
        sql="SELECT * FROM dm.sales_orders",
        db_connectors=[warehouse, mart],
        state=state,
        selected_connector=warehouse,
    )

    assert selected is mart
    assert state.warnings == ["sql_connector_overridden_from_metadata_context"]


def test_metadata_runtime_service_repairs_missing_context_from_runtime_lookup() -> None:
    connector_scope_policy, metadata_context_service, runtime_sql_policy = (
        _runtime_services()
    )
    connector = _connector(
        name="warehouse",
        service="Sample-ClickHouse",
        database="analytics",
        schema="dm",
    )
    metadata_connection = SimpleNamespace(
        mcp_tables_url=None,
        service_name="Sample-ClickHouse",
        database_name="analytics",
        schema_name="dm",
    )
    metadata_resolver = SimpleNamespace(
        resolve_connection=lambda **_kwargs: metadata_connection
    )
    runtime_table = TableContext(
        source="runtime",
        fqn="Sample-ClickHouse.analytics.dm.sales_orders",
        columns=["order_id"],
        service="Sample-ClickHouse",
        database="analytics",
        schema="dm",
    )
    service = ToolExecutionMetadataRuntimeService(
        metadata_resolver=metadata_resolver,
        metadata_table_loader=SimpleNamespace(fetch_tables=lambda **_kwargs: ([], [])),
        metadata_catalog_search_service=SimpleNamespace(
            search_catalog=lambda **_kwargs: ([], [])
        ),
        metadata_ranking_service=SimpleNamespace(
            filter_tables=lambda **_kwargs: []
        ),
        connector_scope_policy=connector_scope_policy,
        metadata_context_service=metadata_context_service,
        runtime_sql_policy=runtime_sql_policy,
        context_hydration_helpers=ToolExecutionContextHydrationHelpers(),
        lookup_runtime_tables=lambda **_kwargs: [runtime_table],
    )
    state = ToolOrchestrationState()

    repaired = service.attempt_metadata_context_repair(
        request=ChatAskRequest(
            question="Revenue?",
            workspace_id="ws-1",
            metadata_connector_name="metadata",
            run_sql=True,
        ),
        state=state,
        selected_connector=connector,
        connection=connector.connection,
        missing_tables=["sales_orders"],
    )

    assert repaired is True
    assert state.context_tables == ["Sample-ClickHouse.analytics.dm.sales_orders"]
    assert state.metadata_table_columns == {
        "sample-clickhouse.analytics.dm.sales_orders": {"order_id"}
    }
    assert state.metadata_sources == {"openmetadata", "runtime"}
    assert state.selected_database_connector == "warehouse"
