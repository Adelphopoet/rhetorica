from __future__ import annotations

from math import ceil
from typing import Any

from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.domain.execution.models import (
    ActiveExecution,
    EngineAdapter,
    EngineRunRequest,
    EngineRunResult,
)
from trino.auth import BasicAuthentication
from trino.dbapi import connect


class TrinoActiveExecution(ActiveExecution):
    def __init__(self, connection: Any, cursor: Any, query_id: str | None):
        self._connection = connection
        self._cursor = cursor
        self.query_id = query_id

    def fetch(self) -> EngineRunResult:
        rows = self._cursor.fetchall()
        description = self._cursor.description or []
        columns = [column[0] for column in description]
        return EngineRunResult(
            query_id=getattr(self._cursor, "query_id", self.query_id),
            columns=columns,
            rows=[list(row) for row in rows],
            row_count=len(rows),
        )

    def cancel(self) -> None:
        self._cursor.cancel()

    def close(self) -> None:
        self._cursor.close()
        self._connection.close()


class TrinoAdapter(EngineAdapter):
    """Execution adapter for Trino DBAPI driver."""

    def start(
        self, connection: EngineConnectionConfig, request: EngineRunRequest
    ) -> ActiveExecution:
        timeout_seconds = max(1, ceil(request.timeout_ms / 1000))
        session_properties = {
            "query_max_run_time": f"{timeout_seconds}s",
            **{
                key: str(value)
                for key, value in self._sanitize_extra_settings(
                    connection.extra_settings
                ).items()
            },
        }

        auth = None
        if connection.password:
            auth = BasicAuthentication(connection.username, connection.password)

        conn = connect(
            host=connection.host,
            port=connection.port,
            user=connection.username,
            catalog=connection.catalog,
            schema=connection.schema_name,
            auth=auth,
            http_scheme="https" if connection.secure else "http",
            verify=connection.verify,
            session_properties=session_properties,
        )
        cursor = conn.cursor()
        cursor.execute(request.sql)
        return TrinoActiveExecution(
            connection=conn,
            cursor=cursor,
            query_id=getattr(cursor, "query_id", None),
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
