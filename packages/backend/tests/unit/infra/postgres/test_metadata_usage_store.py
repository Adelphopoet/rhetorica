from __future__ import annotations

from rhetorica_backend.infra.postgres.metadata_usage_store import PostgresMetadataUsageStore
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


def test_postgres_metadata_usage_store_persists_positive_and_negative_feedback(tmp_path) -> None:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/usage.db")
    runtime.create_schema()
    store = PostgresMetadataUsageStore(runtime)

    store.record_successful_usage(
        workspace_id="ws-1",
        connector_name="om",
        table_fqns=["Sample-ClickHouse.analytics.dm.sales_orders"],
    )
    store.record_negative_feedback(
        workspace_id="ws-1",
        connector_name="om",
        table_fqns=["Sample-ClickHouse.analytics.dm.bad_orders"],
    )

    reloaded = PostgresMetadataUsageStore(runtime)
    usage = reloaded.usage_map(workspace_id="ws-1", connector_name="om")

    assert usage["Sample-ClickHouse.analytics.dm.sales_orders"] > 0
    assert usage["Sample-ClickHouse.analytics.dm.bad_orders"] < 0
