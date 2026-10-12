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
    ResultBlockConsumer,
)


class ClickHouseActiveExecution(ActiveExecution):
    def __init__(
        self,
        client: Any,
        sql: str,
        settings: dict[str, str | int | float | bool],
        query_id: str,
        row_limit: int,
    ):
        self._client = client
        self._sql = sql
        self._settings = settings
        self.query_id: str | None = query_id
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
        preview: list[list[object]] = []
        row_count = 0
        columns: list[str] = []
        with self._client.query_row_block_stream(
            self._sql,
            settings=self._settings,
        ) as stream:
            source = stream.source
            columns = list(source.column_names)
            if consumer is not None:
                consumer.start(columns)
            for block in stream:
                rows = [list(row) for row in block]
                row_count += len(rows)
                if len(preview) < preview_row_limit:
                    preview.extend(rows[: preview_row_limit - len(preview)])
                if consumer is not None:
                    consumer.write_rows(rows)
            self.query_id = getattr(source, "query_id", None) or self.query_id
        return EngineRunResult(
            query_id=self.query_id,
            columns=columns,
            rows=preview,
            row_count=row_count,
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
        configured_block_rows = settings.get("max_block_size")
        settings["max_block_size"] = (
            min(configured_block_rows, 10_000)
            if isinstance(configured_block_rows, int) and configured_block_rows > 0
            else 10_000
        )
        configured_block_bytes = settings.get("preferred_block_size_bytes")
        settings["preferred_block_size_bytes"] = (
            min(configured_block_bytes, 8 * 1024 * 1024)
            if isinstance(configured_block_bytes, int) and configured_block_bytes > 0
            else 8 * 1024 * 1024
        )
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
            row_limit=request.row_limit,
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
