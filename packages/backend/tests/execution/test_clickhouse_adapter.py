from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.domain.execution.models import EngineRunRequest
from rhetorica_backend.infra.engine_clickhouse.adapter import ClickHouseAdapter


@dataclass
class _FakeQuerySource:
    query_id: str
    column_names: list[str]


class _FakeRowBlockStream:
    def __init__(self, source: _FakeQuerySource) -> None:
        self.source = source

    def __enter__(self) -> _FakeRowBlockStream:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def __iter__(self):
        yield [(1,)]


class _FakeClient:
    def __init__(self) -> None:
        self.last_settings: dict[str, str | int | float | bool] | None = None
        self.last_sql: str | None = None
        self.closed = False

    def query_row_block_stream(
        self,
        sql: str,
        settings: dict[str, str | int | float | bool],
    ) -> _FakeRowBlockStream:
        self.last_sql = sql
        self.last_settings = settings
        return _FakeRowBlockStream(
            _FakeQuerySource(
                query_id=str(settings.get("query_id", "")),
                column_names=["value"],
            )
        )

    def close(self) -> None:
        self.closed = True

    def close_connections(self) -> None:
        self.closed = True

    def command(self, _sql: str) -> None:
        return None


class _CaptureConsumer:
    def __init__(self) -> None:
        self.columns: list[str] = []
        self.rows: list[list[Any]] = []

    def start(self, columns) -> None:
        self.columns = list(columns)

    def write_rows(self, rows) -> None:
        self.rows.extend([list(row) for row in rows])


def test_clickhouse_adapter_filters_runtime_only_settings(monkeypatch) -> None:
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

    consumer = _CaptureConsumer()
    result = active.fetch_streaming(consumer=consumer, preview_row_limit=1)
    assert fake_client.last_settings is not None
    assert "query_timeout_seconds" not in fake_client.last_settings
    assert "sql_timeout_ms" not in fake_client.last_settings
    assert fake_client.last_settings["max_threads"] == 2
    assert fake_client.last_settings["max_execution_time"] == 12
    assert fake_client.last_settings["query_id"] == "exec-1"
    assert result.rows == [[1]]
    assert consumer.columns == ["value"]
    assert consumer.rows == [[1]]
