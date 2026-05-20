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

    def execute(self, sql: str) -> None:
        self.executed_sql = sql

    def fetchall(self) -> list[tuple[int]]:
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
                "metadata_service": "DWH-iceberrg",
                "metadata_database": "iceberg",
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

    active.fetch()

    session_properties = captured["session_properties"]
    assert "metadata_service" not in session_properties
    assert "metadata_database" not in session_properties
    assert session_properties["query_priority"] == "high"
    assert session_properties["query_max_run_time"] == "12s"
    assert cursor.executed_sql == "SELECT 1"


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
