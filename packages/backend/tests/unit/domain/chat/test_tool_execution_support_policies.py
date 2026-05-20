from __future__ import annotations

from datetime import UTC, datetime

from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.domain.chat.orchestration.tool_execution_connector_scope_policy import (
    ToolExecutionConnectorScopePolicy,
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
    engine: str = "clickhouse",
) -> ConnectorItem:
    now = datetime.now(tz=UTC)
    return ConnectorItem(
        id=f"id-{name}",
        name=name,
        workspace_id="ws-1",
        connection=EngineConnectionConfig(
            engine=engine,
            host="localhost",
            port=443 if engine == "trino" else 9000,
            username="default",
            database=database,
            catalog=database if engine == "trino" else None,
            schema=schema or None,
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


def test_connector_scope_policy_filters_context_tables_by_available_connectors() -> None:
    policy = ToolExecutionConnectorScopePolicy()
    connector = _connector(
        name="warehouse",
        service="Sample-ClickHouse",
        database="analytics",
        schema="dm",
    )

    filtered = policy.filter_context_tables_by_available_connectors(
        table_fqns=[
            "Sample-ClickHouse.analytics.dm.sales_orders",
            "Other.analytics.dm.sales_orders",
            "ad_hoc_table",
        ],
        db_connectors=[connector],
    )

    assert filtered == [
        "Sample-ClickHouse.analytics.dm.sales_orders",
        "ad_hoc_table",
    ]


def test_connector_scope_policy_matches_trino_execution_table_refs() -> None:
    policy = ToolExecutionConnectorScopePolicy()
    connector = _connector(
        name="trino",
        service="trino_omni",
        database="iceberg",
        schema="dm_ft",
        engine="trino",
    )

    assert policy.table_fqn_matches_connector_scope(
        table_fqn="iceberg.dm_ft.ft_order",
        selected_connector=connector,
    )
    assert policy.table_fqn_matches_connector_scope(
        table_fqn="hive.dm_ft.ft_order",
        selected_connector=connector,
    )
    assert policy.table_fqn_matches_connector_scope(
        table_fqn="delta.sales.orders",
        selected_connector=connector,
    )
    assert (
        policy.metadata_table_fqn_for_execution_ref(
            table_fqn="iceberg.dm_ft.ft_order",
            selected_connector=connector,
        )
        == "trino_omni.iceberg.dm_ft.ft_order"
    )
    assert (
        policy.metadata_table_fqn_for_execution_ref(
            table_fqn="hive.raw.events",
            selected_connector=connector,
        )
        == "trino_omni.hive.raw.events"
    )
    assert policy.execution_refs_for_metadata_table(
        table_fqn="trino_omni.iceberg.dm_ft.ft_order",
        selected_connector=connector,
    ) == ["iceberg.dm_ft.ft_order"]
    assert policy.execution_refs_for_metadata_table(
        table_fqn="trino_omni.hive.raw.events",
        selected_connector=connector,
    ) == ["hive.raw.events"]


def test_connector_scope_policy_mirrors_metadata_columns_to_trino_execution_refs() -> None:
    policy = ToolExecutionConnectorScopePolicy()
    connector = _connector(
        name="trino",
        service="trino_omni",
        database="iceberg",
        schema="dm_ft",
        engine="trino",
    )

    scoped = policy.metadata_columns_for_connector_scope(
        metadata_table_columns={
            "trino_omni.iceberg.dm_ft.ft_order": {
                "order_source",
                "order_create_dttm",
            },
            "trino_omni.hive.raw.events": {"event_id", "event_ts"},
            "iceberg.dm_ft.ft_order": {"order_source"},
        },
        selected_connector=connector,
    )

    assert scoped["iceberg.dm_ft.ft_order"] == {
        "order_source",
        "order_create_dttm",
    }
    assert scoped["hive.raw.events"] == {"event_id", "event_ts"}


def test_connector_scope_policy_matches_schema_table_refs_for_non_trino() -> None:
    policy = ToolExecutionConnectorScopePolicy()
    connector = _connector(
        name="warehouse",
        service="Sample-ClickHouse",
        database="default",
        schema="dm",
    )

    assert policy.table_fqn_matches_connector_scope(
        table_fqn="dm.ft_order",
        selected_connector=connector,
    )
    assert not policy.table_fqn_matches_connector_scope(
        table_fqn="sandbox.ft_order",
        selected_connector=connector,
    )
    assert (
        policy.metadata_table_fqn_for_execution_ref(
            table_fqn="dm.ft_order",
            selected_connector=connector,
        )
        == "Sample-ClickHouse.default.dm.ft_order"
    )


def test_runtime_sql_policy_normalizes_connector_prefixes() -> None:
    connector_scope_policy = ToolExecutionConnectorScopePolicy()
    policy = ToolExecutionRuntimeSqlPolicy(
        connector_scope_policy=connector_scope_policy
    )
    connector = _connector(
        name="warehouse",
        service="Sample-ClickHouse",
        database="analytics",
        schema="dm",
    )
    connection = connector.connection.model_copy(
        update={"catalog": "analytics"}
    )

    normalized = policy.normalize_sql_identifiers(
        sql="SELECT * FROM warehouse.dm.sales_orders",
        requested_connector_name="warehouse",
        selected_connector=connector,
        connection=connection,
    )

    assert normalized == "SELECT * FROM dm.sales_orders"
