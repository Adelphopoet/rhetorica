from __future__ import annotations

from typing import Any

import psycopg
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.domain.execution.models import (
    ActiveExecution,
    EngineAdapter,
    EngineRunRequest,
    EngineRunResult,
    ResultBlockConsumer,
)


class GreenplumActiveExecution(ActiveExecution):
    def __init__(self, connection: Any, cursor: Any, row_limit: int):
        self._connection = connection
        self._cursor = cursor
        self.query_id: str | None = None
        self._row_limit = row_limit

    def fetch(self) -> EngineRunResult:
        return self.fetch_streaming(
            consumer=None,
            preview_row_limit=self._row_limit,
        )

    def fetch_streaming(
        self,
        *,
        consumer: ResultBlockConsumer | None,
        preview_row_limit: int,
    ) -> EngineRunResult:
        description = self._cursor.description or []
        columns = [column.name for column in description]
        if consumer is not None:
            consumer.start(columns)
        preview: list[list[object]] = []
        row_count = 0
        while description and (block := self._cursor.fetchmany(10_000)):
            rows = [list(row) for row in block]
            row_count += len(rows)
            if len(preview) < preview_row_limit:
                preview.extend(rows[: preview_row_limit - len(preview)])
            if consumer is not None:
                consumer.write_rows(rows)
        return EngineRunResult(
            query_id=self.query_id,
            columns=columns,
            rows=preview,
            row_count=row_count,
        )

    def cancel(self) -> None:
        self._connection.cancel()

    def close(self) -> None:
        self._cursor.close()
        self._connection.close()


class GreenplumAdapter(EngineAdapter):
    """Execution adapter for Greenplum via psycopg."""

    def start(
        self, connection: EngineConnectionConfig, request: EngineRunRequest
    ) -> ActiveExecution:
        engine_settings = self._sanitize_extra_settings(connection.extra_settings)
        connect_kwargs: dict[str, Any] = {
            "host": connection.host,
            "port": connection.port,
            "user": connection.username,
            "password": connection.password,
            "dbname": connection.database,
            "autocommit": True,
            "sslmode": self._sslmode(
                secure=connection.secure,
                verify=connection.verify,
            ),
        }
        connect_timeout = engine_settings.get("connect_timeout")
        if isinstance(connect_timeout, int):
            connect_kwargs["connect_timeout"] = connect_timeout
        elif isinstance(connect_timeout, str) and connect_timeout.isdigit():
            connect_kwargs["connect_timeout"] = int(connect_timeout)

        application_name = engine_settings.get("application_name")
        if isinstance(application_name, str) and application_name.strip():
            connect_kwargs["application_name"] = application_name.strip()

        conn = psycopg.connect(**connect_kwargs)
        cursor = conn.cursor()
        cursor.execute(f"SET statement_timeout = {max(1, request.timeout_ms)}")
        cursor.execute(request.sql)
        return GreenplumActiveExecution(
            connection=conn,
            cursor=cursor,
            row_limit=request.row_limit,
        )

    @staticmethod
    def _sanitize_extra_settings(
        extra_settings: dict[str, str | int | float | bool],
    ) -> dict[str, str | int | float | bool]:
        blocked_prefixes = ("metadata_",)
        return {
            key: value
            for key, value in extra_settings.items()
            if not any(key.startswith(prefix) for prefix in blocked_prefixes)
        }

    @staticmethod
    def _sslmode(*, secure: bool, verify: bool) -> str:
        if not secure:
            return "disable"
        if verify:
            return "verify-full"
        return "require"
