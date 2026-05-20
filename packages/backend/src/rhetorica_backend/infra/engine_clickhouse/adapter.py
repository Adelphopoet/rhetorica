from __future__ import annotations

from math import ceil
from typing import Any

import clickhouse_connect  # type: ignore[import-untyped]
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.domain.execution.models import (
    ActiveExecution,
    EngineAdapter,
    EngineRunRequest,
    EngineRunResult,
)


class ClickHouseActiveExecution(ActiveExecution):
    def __init__(
        self,
        client: Any,
        sql: str,
        settings: dict[str, str | int | float | bool],
        query_id: str,
    ):
        self._client = client
        self._sql = sql
        self._settings = settings
        self.query_id: str | None = query_id

    def fetch(self) -> EngineRunResult:
        result = self._client.query(self._sql, settings=self._settings)
        self.query_id = result.query_id
        return EngineRunResult(
            query_id=self.query_id,
            columns=list(result.column_names),
            rows=[list(row) for row in result.result_rows],
            row_count=result.row_count,
        )

    def cancel(self) -> None:
        if self.query_id:
            self._client.command(f"KILL QUERY WHERE query_id = '{self.query_id}' SYNC")
            return
        self._client.close_connections()

    def close(self) -> None:
        self._client.close()


class ClickHouseAdapter(EngineAdapter):
    """Execution adapter for ClickHouse via clickhouse-connect."""

    def start(
        self, connection: EngineConnectionConfig, request: EngineRunRequest
    ) -> ActiveExecution:
        engine_settings = self._sanitize_extra_settings(connection.extra_settings)
        settings: dict[str, str | int | float | bool] = {
            **engine_settings,
            "max_execution_time": max(1, ceil(request.timeout_ms / 1000)),
            "query_id": request.execution_id,
        }
        client = clickhouse_connect.get_client(
            host=connection.host,
            port=connection.port,
            username=connection.username,
            password=connection.password,
            database=connection.database,
            secure=connection.secure,
            verify=connection.verify,
        )
        return ClickHouseActiveExecution(
            client=client,
            sql=request.sql,
            settings=settings,
            query_id=request.execution_id,
        )

    @staticmethod
    def _sanitize_extra_settings(
        extra_settings: dict[str, str | int | float | bool],
    ) -> dict[str, str | int | float | bool]:
        # metadata_* and SQL timeout aliases are control-plane settings.
        blocked_prefixes = ("metadata_",)
        blocked_keys = {
            "query_timeout_seconds",
            "query_timeout_sec",
            "sql_timeout_seconds",
            "sql_timeout_sec",
            "query_timeout_ms",
            "sql_timeout_ms",
        }
        return {
            key: value
            for key, value in extra_settings.items()
            if key not in blocked_keys
            and not any(key.startswith(prefix) for prefix in blocked_prefixes)
        }
