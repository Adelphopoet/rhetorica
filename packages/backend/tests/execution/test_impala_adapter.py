from __future__ import annotations

from typing import Any

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

    def execute(self, sql: str) -> None:
        self.executed_sql.append(sql)

    def fetchall(self) -> list[tuple[int]]:
        return [(1,)]

    def close(self) -> None:
        self.closed = True

    def cancel_operation(self) -> None:
        self.cancelled = True


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


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
            host="10.234.176.50",
            port=21050,
            username="analyst_user",
            password="secret",
            schema="default",
            secure=True,
            verify=False,
            extra_settings={
                "auth_type": "ldap",
                "use_http_transport": False,
                "retries": 1,
                "metadata_service": "impala-prod",
                "metadata_database": "default",
            },
        ),
        request=EngineRunRequest(
            execution_id="exec-impala-1",
            sql="SELECT 1",
            timeout_ms=12_000,
            row_limit=100,
        ),
    )

    result = active.fetch()

    assert captured == {
        "host": "10.234.176.50",
        "port": 21050,
        "database": "default",
        "auth_mechanism": "LDAP",
        "user": "analyst_user",
        "password": "secret",
        "use_ssl": True,
        "use_http_transport": False,
        "retries": 1,
        "ca_cert": None,
        "http_path": None,
        "timeout": None,
    }
    assert cursor.executed_sql == [
        "SET QUERY_TIMEOUT_S=12",
        "SET EXEC_TIME_LIMIT_S=12",
        "SELECT 1",
    ]
    assert result.columns == ["value"]
    assert result.rows == [[1]]


def test_impala_options_factory_supports_dbt_profile_keys() -> None:
    options = ImpalaConnectionOptionsFactory().build({
        "auth_type": "ldap",
        "use_http_transport": "false",
        "retries": 1,
        "timeout": "30",
        "metadata_service": "impala-prod",
    })

    assert options.auth_mechanism == "LDAP"
    assert options.use_http_transport is False
    assert options.retries == 1
    assert options.timeout == 30
