from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.domain.execution.models import EngineRunRequest
from rhetorica_backend.infra.engine_greenplum.adapter import GreenplumAdapter


class _FakeCursor:
    def __init__(self) -> None:
        self.description = [SimpleNamespace(name="value")]
        self.executed_sql: list[str] = []
        self.closed = False
        self._fetched = False

    def execute(self, sql: str) -> None:
        self.executed_sql.append(sql)

    def fetchmany(self, _size: int) -> list[tuple[int]]:
        if self._fetched:
            return []
        self._fetched = True
        return [(1,)]

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.cancelled = False
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def cancel(self) -> None:
        self.cancelled = True

    def close(self) -> None:
        self.closed = True


class _CaptureConsumer:
    def __init__(self) -> None:
        self.columns: list[str] = []
        self.rows: list[list[Any]] = []

    def start(self, columns) -> None:
        self.columns = list(columns)

    def write_rows(self, rows) -> None:
        self.rows.extend([list(row) for row in rows])


def test_greenplum_adapter_uses_psycopg_and_strips_metadata_settings(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    cursor = _FakeCursor()
    connection = _FakeConnection(cursor)

    def _fake_connect(**kwargs: Any) -> _FakeConnection:
        captured.update(kwargs)
        return connection

    monkeypatch.setattr(
        "rhetorica_backend.infra.engine_greenplum.adapter.psycopg.connect",
        _fake_connect,
    )

    adapter = GreenplumAdapter()
    active = adapter.start(
        connection=EngineConnectionConfig(
            engine="greenplum",
            host="greenplum.local",
            port=6432,
            username="analyst",
            password="secret",
            database="dwh",
            secure=True,
            verify=False,
            extra_settings={
                "connect_timeout": 30,
                "application_name": "rhetorica-tests",
            },
        ),
        request=EngineRunRequest(
            execution_id="exec-greenplum-1",
            sql="SELECT 1",
            timeout_ms=12_000,
            row_limit=100,
        ),
    )

    consumer = _CaptureConsumer()
    result = active.fetch_streaming(consumer=consumer, preview_row_limit=1)

    assert captured["host"] == "greenplum.local"
    assert captured["dbname"] == "dwh"
    assert captured["sslmode"] == "require"
    assert captured["connect_timeout"] == 30
    assert captured["application_name"] == "rhetorica-tests"
    assert cursor.executed_sql == ["SET statement_timeout = 12000", "SELECT 1"]
    assert result.columns == ["value"]
    assert result.rows == [[1]]
    assert consumer.columns == ["value"]
    assert consumer.rows == [[1]]


def test_greenplum_adapter_cancel_and_close_delegate_to_connection(monkeypatch) -> None:
    cursor = _FakeCursor()
    connection = _FakeConnection(cursor)

    monkeypatch.setattr(
        "rhetorica_backend.infra.engine_greenplum.adapter.psycopg.connect",
        lambda **_kwargs: connection,
    )

    adapter = GreenplumAdapter()
    active = adapter.start(
        connection=EngineConnectionConfig(
            engine="greenplum",
            host="greenplum.local",
            port=6432,
            username="analyst",
            database="dwh",
        ),
        request=EngineRunRequest(
            execution_id="exec-greenplum-2",
            sql="SELECT 1",
            timeout_ms=5_000,
            row_limit=100,
        ),
    )

    active.cancel()
    active.close()

    assert connection.cancelled is True
    assert cursor.closed is True
    assert connection.closed is True
