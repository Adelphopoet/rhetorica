from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.domain.execution.models import EngineRunRequest
from rhetorica_backend.infra.engine_clickhouse.adapter import ClickHouseAdapter


@dataclass
class _FakeQueryResult:
    query_id: str
    column_names: list[str]
    result_rows: list[list[Any]]
    row_count: int


class _FakeClient:
    def __init__(self) -> None:
        self.last_settings: dict[str, str | int | float | bool] | None = None
        self.last_sql: str | None = None
        self.closed = False

    def query(self, sql: str, settings: dict[str, str | int | float | bool]) -> _FakeQueryResult:
        self.last_sql = sql
        self.last_settings = settings
        return _FakeQueryResult(
            query_id=str(settings.get("query_id", "")),
            column_names=["value"],
            result_rows=[[1]],
            row_count=1,
        )

    def close(self) -> None:
        self.closed = True

    def close_connections(self) -> None:
        self.closed = True

    def command(self, _sql: str) -> None:
        return None


def test_clickhouse_adapter_does_not_pass_metadata_mapping_to_engine(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "rhetorica_backend.infra.engine_clickhouse.adapter.clickhouse_connect.get_client",
        lambda **_kwargs: fake_client,
    )

    adapter = ClickHouseAdapter()
    active = adapter.start(
        connection=EngineConnectionConfig(
            engine="clickhouse",
            host="localhost",
            port=8123,
            username="default",
            database="default",
            extra_settings={
                "metadata_service": "Sample-ClickHouse",
                "metadata_database": "default",
                "query_timeout_seconds": 900,
                "sql_timeout_ms": 300_000,
                "max_threads": 2,
            },
        ),
        request=EngineRunRequest(
            execution_id="exec-1",
            sql="SELECT 1",
            timeout_ms=12_000,
            row_limit=100,
        ),
    )

    active.fetch()
    assert fake_client.last_settings is not None
    assert "metadata_service" not in fake_client.last_settings
    assert "metadata_database" not in fake_client.last_settings
    assert "query_timeout_seconds" not in fake_client.last_settings
    assert "sql_timeout_ms" not in fake_client.last_settings
    assert fake_client.last_settings["max_threads"] == 2
    assert fake_client.last_settings["max_execution_time"] == 12
    assert fake_client.last_settings["query_id"] == "exec-1"
