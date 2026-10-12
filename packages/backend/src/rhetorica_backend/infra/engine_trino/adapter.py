from __future__ import annotations

from math import ceil
from typing import Any

from requests import Session
from rhetorica_backend.contracts.execution import (
    EngineConnectionConfig,
    EngineExtraSetting,
    EngineScalarSetting,
)
from rhetorica_backend.domain.execution.models import (
    ActiveExecution,
    EngineAdapter,
    EngineRunRequest,
    EngineRunResult,
    ResultBlockConsumer,
)
from trino.auth import BasicAuthentication
from trino.dbapi import connect


class TrinoActiveExecution(ActiveExecution):
    def __init__(
        self,
        connection: Any,
        cursor: Any,
        query_id: str | None,
        row_limit: int,
    ):
        self._connection = connection
        self._cursor = cursor
        self.query_id = query_id
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
        columns = [column[0] for column in description]
        if consumer is not None:
            consumer.start(columns)
        preview: list[list[object]] = []
        row_count = 0
        while block := self._cursor.fetchmany(10_000):
            rows = [list(row) for row in block]
            row_count += len(rows)
            if len(preview) < preview_row_limit:
                preview.extend(rows[: preview_row_limit - len(preview)])
            if consumer is not None:
                consumer.write_rows(rows)
        return EngineRunResult(
            query_id=getattr(self._cursor, "query_id", self.query_id),
            columns=columns,
            rows=preview,
            row_count=row_count,
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
        session_properties = self._session_properties(
            connection.extra_settings,
            timeout_seconds=timeout_seconds,
        )

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
            extra_credential=self._extra_credentials(connection),
            http_headers=self._http_headers(connection.extra_settings),
            http_session=self._http_session(connection.verify),
        )
        cursor = conn.cursor()
        cursor.execute(request.sql)
        return TrinoActiveExecution(
            connection=conn,
            cursor=cursor,
            query_id=getattr(cursor, "query_id", None),
            row_limit=request.row_limit,
        )

    @staticmethod
    def _extra_credentials(
        connection: EngineConnectionConfig,
    ) -> list[tuple[str, str]] | None:
        if not connection.extra_credentials:
            return None
        credentials: list[tuple[str, str]] = []
        for name, value in sorted(connection.extra_credentials.items()):
            if value is None:
                raise ValueError(f"trino_extra_credential_value_required:{name}")
            credentials.append((name, value))
        return credentials

    @staticmethod
    def _sanitize_extra_settings(
        extra_settings: dict[str, EngineExtraSetting],
    ) -> dict[str, EngineScalarSetting]:
        blocked_prefixes = ("metadata_",)
        blocked_keys = {"http_headers", "set_query_max_run_time"}
        return {
            key: value
            for key, value in extra_settings.items()
            if isinstance(value, str | int | float | bool)
            and key not in blocked_keys
            and not any(key.startswith(prefix) for prefix in blocked_prefixes)
        }

    @classmethod
    def _session_properties(
        cls,
        extra_settings: dict[str, EngineExtraSetting],
        *,
        timeout_seconds: int,
    ) -> dict[str, str]:
        session_properties = {
            key: str(value)
            for key, value in cls._sanitize_extra_settings(extra_settings).items()
        }
        if cls._bool_setting(
            extra_settings,
            "set_query_max_run_time",
            default=True,
        ):
            session_properties = {
                "query_max_run_time": f"{timeout_seconds}s",
                **session_properties,
            }
        return session_properties

    @staticmethod
    def _bool_setting(
        extra_settings: dict[str, EngineExtraSetting],
        key: str,
        *,
        default: bool,
    ) -> bool:
        value = extra_settings.get(key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        raise ValueError(f"Unable to parse Trino boolean setting {key}: {value!r}")

    @staticmethod
    def _http_headers(
        extra_settings: dict[str, EngineExtraSetting],
    ) -> dict[str, str] | None:
        raw_headers = extra_settings.get("http_headers")
        if raw_headers is None:
            return None
        if not isinstance(raw_headers, dict):
            raise TypeError("Trino extra_settings.http_headers must be an object.")
        headers = {
            str(key).strip(): str(value)
            for key, value in raw_headers.items()
            if str(key).strip()
        }
        return headers or None

    @staticmethod
    def _http_session(verify: bool) -> Session | None:
        if verify:
            return None
        session = Session()
        session.trust_env = False
        session.verify = False
        return session
