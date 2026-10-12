from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.domain.execution.models import EngineRunRequest
from rhetorica_backend.infra.engine_trino.adapter import TrinoAdapter


class _FakeCursor:
    def __init__(self) -> None:
        self.query_id = "trino-query-1"
        self.description = [("value",)]
        self.executed_sql: str | None = None
        self._fetched = False

    def execute(self, sql: str) -> None:
        self.executed_sql = sql

    def fetchmany(self, _size: int) -> list[tuple[int]]:
        if self._fetched:
            return []
        self._fetched = True
        return [(1,)]

    def close(self) -> None:
        return None

    def cancel(self) -> None:
        return None


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        return None


class _CaptureConsumer:
    def __init__(self) -> None:
        self.columns: list[str] = []
        self.rows: list[list[Any]] = []

    def start(self, columns) -> None:
        self.columns = list(columns)

    def write_rows(self, rows) -> None:
        self.rows.extend([list(row) for row in rows])


def test_trino_adapter_does_not_pass_metadata_mapping_to_session_properties(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    cursor = _FakeCursor()

    def _fake_connect(**kwargs: Any) -> _FakeConnection:
        captured.update(kwargs)
        return _FakeConnection(cursor)

    monkeypatch.setattr(
        "rhetorica_backend.infra.engine_trino.adapter.connect",
        _fake_connect,
    )

    adapter = TrinoAdapter()
    active = adapter.start(
        connection=EngineConnectionConfig(
            engine="trino",
            host="localhost",
            port=443,
            username="analyst",
            password=None,
            catalog="iceberg",
            schema="iceberg",
            secure=True,
            extra_settings={
                "http_headers": {
                    "Host": "trino.example.com",
                    "X-Forwarded-Host": "192.0.2.7",
                },
                "query_priority": "high",
            },
        ),
        request=EngineRunRequest(
            execution_id="exec-trino-1",
            sql="SELECT 1",
            timeout_ms=12_000,
            row_limit=100,
        ),
    )

    consumer = _CaptureConsumer()
    result = active.fetch_streaming(consumer=consumer, preview_row_limit=1)

    session_properties = captured["session_properties"]
    assert "http_headers" not in session_properties
    assert session_properties["query_priority"] == "high"
    assert session_properties["query_max_run_time"] == "12s"
    assert captured["http_headers"] == {
        "Host": "trino.example.com",
        "X-Forwarded-Host": "192.0.2.7",
    }
    assert cursor.executed_sql == "SELECT 1"
    assert result.rows == [[1]]
    assert consumer.columns == ["value"]
    assert consumer.rows == [[1]]


def test_trino_adapter_uses_basic_auth_over_https_when_password_present(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    cursor = _FakeCursor()

    monkeypatch.setattr(
        "rhetorica_backend.infra.engine_trino.adapter.BasicAuthentication",
        lambda user, password: ("basic-auth", user, password),
    )

    def _fake_connect(**kwargs: Any) -> _FakeConnection:
        captured.update(kwargs)
        return _FakeConnection(cursor)

    monkeypatch.setattr(
        "rhetorica_backend.infra.engine_trino.adapter.connect",
        _fake_connect,
    )

    adapter = TrinoAdapter()
    adapter.start(
        connection=EngineConnectionConfig(
            engine="trino",
            host="localhost",
            port=443,
            username="analyst",
            password="secret",
            catalog="iceberg",
            schema="iceberg",
            secure=True,
            verify=True,
        ),
        request=EngineRunRequest(
            execution_id="exec-trino-2",
            sql="SELECT 1",
            timeout_ms=5_000,
            row_limit=100,
        ),
    )

    assert captured["auth"] == ("basic-auth", "analyst", "secret")
    assert captured["http_scheme"] == "https"
    assert captured["verify"] is True
    assert captured["http_session"] is None


def test_trino_adapter_passes_sorted_extra_credentials(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    cursor = _FakeCursor()

    def _fake_connect(**kwargs: Any) -> _FakeConnection:
        captured.update(kwargs)
        return _FakeConnection(cursor)

    monkeypatch.setattr(
        "rhetorica_backend.infra.engine_trino.adapter.connect",
        _fake_connect,
    )

    TrinoAdapter().start(
        connection=EngineConnectionConfig(
            engine="trino",
            host="localhost",
            port=443,
            username="analyst",
            catalog="iceberg",
            schema="dm",
            secure=True,
            extra_credentials={
                "td_password": "teradata-secret",
                "td_user": "teradata-user",
            },
        ),
        request=EngineRunRequest(
            execution_id="exec-trino-extra-credentials",
            sql="SELECT 1",
            timeout_ms=5_000,
            row_limit=1,
        ),
    )

    assert captured["extra_credential"] == [
        ("td_password", "teradata-secret"),
        ("td_user", "teradata-user"),
    ]


def test_trino_extra_credentials_are_redacted_from_connection_summary() -> None:
    connection = EngineConnectionConfig(
        engine="trino",
        host="localhost",
        port=443,
        username="analyst",
        password="basic-secret",
        extra_credentials={
            "td_user": "teradata-user",
            "td_password": "teradata-secret",
        },
    )

    summary = connection.redacted_dict()

    assert "password" not in summary
    assert summary["extra_credentials"] == {
        "td_user": None,
        "td_password": None,
    }


def test_trino_adapter_rejects_unresolved_extra_credential() -> None:
    connection = EngineConnectionConfig(
        engine="trino",
        host="localhost",
        port=443,
        username="analyst",
        extra_credentials={"td_user": None},
    )

    try:
        TrinoAdapter._extra_credentials(connection)
    except ValueError as exc:
        assert str(exc) == "trino_extra_credential_value_required:td_user"
    else:
        raise AssertionError("unresolved credential must be rejected")


def test_trino_adapter_disables_http_session_environment_when_verify_false(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    cursor = _FakeCursor()

    def _fake_connect(**kwargs: Any) -> _FakeConnection:
        captured.update(kwargs)
        return _FakeConnection(cursor)

    monkeypatch.setattr(
        "rhetorica_backend.infra.engine_trino.adapter.connect",
        _fake_connect,
    )

    adapter = TrinoAdapter()
    adapter.start(
        connection=EngineConnectionConfig(
            engine="trino",
            host="192.0.2.7",
            port=443,
            username="analyst",
            password="secret",
            catalog="system",
            schema="runtime",
            secure=True,
            verify=False,
            extra_settings={
                "http_headers": {
                    "Host": "trino.example.com",
                    "X-Forwarded-Host": "192.0.2.7",
                    "X-Forwarded-Port": "443",
                    "X-Forwarded-Proto": "https",
                },
            },
        ),
        request=EngineRunRequest(
            execution_id="exec-trino-3",
            sql="SELECT 1",
            timeout_ms=5_000,
            row_limit=100,
        ),
    )

    assert captured["verify"] is False
    assert captured["http_headers"] == {
        "Host": "trino.example.com",
        "X-Forwarded-Host": "192.0.2.7",
        "X-Forwarded-Port": "443",
        "X-Forwarded-Proto": "https",
    }
    assert captured["http_session"].trust_env is False
    assert captured["http_session"].verify is False


def test_trino_adapter_can_skip_query_max_run_time_session_property(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    cursor = _FakeCursor()

    def _fake_connect(**kwargs: Any) -> _FakeConnection:
        captured.update(kwargs)
        return _FakeConnection(cursor)

    monkeypatch.setattr(
        "rhetorica_backend.infra.engine_trino.adapter.connect",
        _fake_connect,
    )

    adapter = TrinoAdapter()
    adapter.start(
        connection=EngineConnectionConfig(
            engine="trino",
            host="192.0.2.7",
            port=443,
            username="analyst",
            password="secret",
            catalog="system",
            schema="runtime",
            secure=True,
            verify=False,
            extra_settings={
                "set_query_max_run_time": False,
                "query_priority": "high",
            },
        ),
        request=EngineRunRequest(
            execution_id="exec-trino-4",
            sql="SELECT 1",
            timeout_ms=5_000,
            row_limit=100,
        ),
    )

    assert "query_max_run_time" not in captured["session_properties"]
    assert "set_query_max_run_time" not in captured["session_properties"]
    assert captured["session_properties"] == {"query_priority": "high"}
