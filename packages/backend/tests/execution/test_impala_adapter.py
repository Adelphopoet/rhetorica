from __future__ import annotations

from threading import Event
from time import perf_counter
from typing import Any

import pytest
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.domain.execution.models import EngineRunRequest
from rhetorica_backend.infra.engine_impala.adapter import (
    ImpalaAdapter,
    ImpalaConnectionOptionsFactory,
)


class _FakeCursor:
    def __init__(self) -> None:
        self.query_id = "impala-query-1"
        self.description = [("value",)]
        self.executed_sql: list[str] = []
        self.closed = False
        self.cancelled = False
        self.close_calls = 0
        self.cancel_calls = 0
        self._fetched = False

    def execute(self, sql: str) -> None:
        self.executed_sql.append(sql)

    def fetchmany(self, _size: int) -> list[tuple[int]]:
        if self._fetched:
            return []
        self._fetched = True
        return [(1,)]

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True

    def cancel_operation(self) -> None:
        self.cancel_calls += 1
        self.cancelled = True


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False
        self.close_calls = 0
        self.close_called = Event()

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True
        self.close_called.set()


class _BlockingDescribeCursor(_FakeCursor):
    def __init__(self) -> None:
        super().__init__()
        self.describe_started = Event()
        self.describe_finished = Event()
        self.cancel_called = Event()
        self._release = Event()

    def execute(self, sql: str) -> None:
        self.executed_sql.append(sql)
        if sql.startswith("DESCRIBE "):
            self.describe_started.set()
            self._release.wait(timeout=5)
            self.describe_finished.set()

    def cancel_operation(self) -> None:
        super().cancel_operation()
        self.cancel_called.set()
        self._release.set()


class _BlockingCursorConnection(_FakeConnection):
    def __init__(self, cursor: _FakeCursor) -> None:
        super().__init__(cursor)
        self.cursor_started = Event()
        self._release = Event()

    def cursor(self) -> _FakeCursor:
        self.cursor_started.set()
        self._release.wait(timeout=5)
        return super().cursor()

    def release_cursor(self) -> None:
        self._release.set()


class _CaptureConsumer:
    def __init__(self) -> None:
        self.columns: list[str] = []
        self.rows: list[list[Any]] = []

    def start(self, columns) -> None:
        self.columns = list(columns)

    def write_rows(self, rows) -> None:
        self.rows.extend([list(row) for row in rows])


def test_impala_adapter_uses_ldap_ssl_and_does_not_pass_metadata_mapping(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    cursor = _FakeCursor()
    connection = _FakeConnection(cursor)

    def _fake_connect(**kwargs: Any) -> _FakeConnection:
        captured.update(kwargs)
        return connection

    monkeypatch.setattr(
        "rhetorica_backend.infra.engine_impala.adapter.impala_connect",
        _fake_connect,
    )

    adapter = ImpalaAdapter()
    active = adapter.start(
        connection=EngineConnectionConfig(
            engine="impala",
            host="192.0.2.50",
            port=21050,
            username="balov_aa",
            password="secret",
            schema="default",
            secure=True,
            verify=False,
            extra_settings={
                "auth_type": "ldap",
                "use_http_transport": False,
                "retries": 1,
            },
        ),
        request=EngineRunRequest(
            execution_id="exec-impala-1",
            sql="SELECT 1",
            timeout_ms=12_000,
            row_limit=100,
        ),
    )

    consumer = _CaptureConsumer()
    result = active.fetch_streaming(consumer=consumer, preview_row_limit=1)

    assert captured == {
        "host": "192.0.2.50",
        "port": 21050,
        "database": "default",
        "auth_mechanism": "LDAP",
        "user": "balov_aa",
        "password": "secret",
        "use_ssl": True,
        "use_http_transport": False,
        "retries": 1,
        "ca_cert": None,
        "http_path": None,
        "timeout": 12.0,
    }
    assert cursor.executed_sql == [
        "SET QUERY_TIMEOUT_S=12",
        "SET EXEC_TIME_LIMIT_S=12",
        "SELECT 1",
    ]
    assert result.columns == ["value"]
    assert result.rows == [[1]]
    assert consumer.columns == ["value"]
    assert consumer.rows == [[1]]


@pytest.mark.parametrize(
    ("configured_timeout", "expected_timeout"),
    [
        (5, 5.0),
        (0, 12.0),
        (30, 12.0),
    ],
)
def test_impala_adapter_client_timeout_is_bounded_by_execution_policy(
    monkeypatch,
    configured_timeout: int,
    expected_timeout: float,
) -> None:
    captured: dict[str, Any] = {}
    cursor = _FakeCursor()
    connection = _FakeConnection(cursor)

    def _fake_connect(**kwargs: Any) -> _FakeConnection:
        captured.update(kwargs)
        return connection

    monkeypatch.setattr(
        "rhetorica_backend.infra.engine_impala.adapter.impala_connect",
        _fake_connect,
    )

    ImpalaAdapter().start(
        connection=EngineConnectionConfig(
            engine="impala",
            host="impala.local",
            port=21050,
            username="analyst",
            extra_settings={"timeout": configured_timeout},
        ),
        request=EngineRunRequest(
            execution_id="exec-impala-timeout-wiring",
            sql="SELECT 1",
            timeout_ms=12_000,
            row_limit=100,
        ),
    )

    assert captured["timeout"] == expected_timeout


def test_impala_adapter_times_out_blocked_describe_startup(monkeypatch) -> None:
    cursor = _BlockingDescribeCursor()
    connection = _FakeConnection(cursor)
    monkeypatch.setattr(
        "rhetorica_backend.infra.engine_impala.adapter.impala_connect",
        lambda **_kwargs: connection,
    )

    started_at = perf_counter()
    with pytest.raises(TimeoutError, match="after 50 ms"):
        ImpalaAdapter().start(
            connection=EngineConnectionConfig(
                engine="impala",
                host="impala.local",
                port=21050,
                username="analyst",
            ),
            request=EngineRunRequest(
                execution_id="exec-impala-blocked-describe",
                sql="DESCRIBE base_dm_v_l2.v_strg_stock_fa_d",
                timeout_ms=50,
                row_limit=100,
            ),
        )

    assert perf_counter() - started_at < 0.5
    assert cursor.describe_started.is_set()
    assert cursor.cancel_called.wait(timeout=1)
    assert cursor.describe_finished.wait(timeout=1)
    assert connection.close_called.wait(timeout=1)
    assert cursor.cancel_calls == 1
    assert cursor.close_calls == 1
    assert connection.close_calls == 1


def test_impala_adapter_cleans_connection_when_cursor_is_published_after_timeout(
    monkeypatch,
) -> None:
    cursor = _FakeCursor()
    connection = _BlockingCursorConnection(cursor)
    monkeypatch.setattr(
        "rhetorica_backend.infra.engine_impala.adapter.impala_connect",
        lambda **_kwargs: connection,
    )

    with pytest.raises(TimeoutError, match="after 50 ms"):
        ImpalaAdapter().start(
            connection=EngineConnectionConfig(
                engine="impala",
                host="impala.local",
                port=21050,
                username="analyst",
            ),
            request=EngineRunRequest(
                execution_id="exec-impala-late-cursor",
                sql="DESCRIBE base_dm_v_l2.v_strg_stock_fa_d",
                timeout_ms=50,
                row_limit=100,
            ),
        )

    assert connection.cursor_started.is_set()
    assert not connection.close_called.is_set()

    connection.release_cursor()

    assert connection.close_called.wait(timeout=1)
    assert cursor.cancel_calls == 1
    assert cursor.close_calls == 1
    assert connection.close_calls == 1


def test_impala_options_factory_supports_dbt_profile_keys() -> None:
    options = ImpalaConnectionOptionsFactory().build({
        "auth_type": "ldap",
        "use_http_transport": "false",
        "retries": 1,
        "timeout": "30",
    })

    assert options.auth_mechanism == "LDAP"
    assert options.use_http_transport is False
    assert options.retries == 1
    assert options.timeout == 30
