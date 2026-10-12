from __future__ import annotations

import logging
from dataclasses import dataclass
from math import ceil
from threading import Event, RLock, Thread
from typing import Any, ClassVar

from impala.dbapi import connect as impala_connect  # type: ignore[import-untyped]
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.domain.execution.models import (
    ActiveExecution,
    EngineAdapter,
    EngineRunRequest,
    EngineRunResult,
    ResultBlockConsumer,
)

logger = logging.getLogger(__name__)


class ImpalaActiveExecution(ActiveExecution):
    def __init__(self, connection: Any, cursor: Any, row_limit: int):
        self._connection = connection
        self._cursor = cursor
        self.query_id: str | None = getattr(cursor, "query_id", None)
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
        self.query_id = getattr(self._cursor, "query_id", self.query_id)
        return EngineRunResult(
            query_id=self.query_id,
            columns=columns,
            rows=preview,
            row_count=row_count,
        )

    def cancel(self) -> None:
        cancel_operation = getattr(self._cursor, "cancel_operation", None)
        if callable(cancel_operation):
            cancel_operation()
            return
        cancel = getattr(self._cursor, "cancel", None)
        if callable(cancel):
            cancel()
            return
        self._connection.close()

    def close(self) -> None:
        self._cursor.close()
        self._connection.close()


@dataclass(frozen=True)
class ImpalaConnectionOptions:
    auth_mechanism: str = "LDAP"
    use_http_transport: bool = False
    retries: int = 1
    ca_cert: str | None = None
    http_path: str | None = None
    timeout: int | None = None


class ImpalaConnectionOptionsFactory:
    _BLOCKED_PREFIXES: ClassVar[tuple[str, ...]] = ("metadata_",)
    _AUTH_MECHANISMS: ClassVar[set[str]] = {"NOSASL", "PLAIN", "GSSAPI", "LDAP", "JWT"}

    def build(
        self,
        extra_settings: dict[str, str | int | float | bool],
    ) -> ImpalaConnectionOptions:
        engine_settings = self.sanitize(extra_settings)
        return ImpalaConnectionOptions(
            auth_mechanism=self._auth_mechanism(engine_settings),
            use_http_transport=self._bool_setting(
                engine_settings,
                "use_http_transport",
                default=False,
            ),
            retries=self._int_setting(engine_settings, "retries", default=1),
            ca_cert=self._optional_str(engine_settings, "ca_cert"),
            http_path=self._optional_str(engine_settings, "http_path"),
            timeout=self._optional_int(engine_settings, "timeout"),
        )

    @classmethod
    def sanitize(
        cls,
        extra_settings: dict[str, str | int | float | bool],
    ) -> dict[str, str | int | float | bool]:
        return {
            key: value
            for key, value in extra_settings.items()
            if not any(key.startswith(prefix) for prefix in cls._BLOCKED_PREFIXES)
        }

    @classmethod
    def _auth_mechanism(cls, settings: dict[str, str | int | float | bool]) -> str:
        raw = settings.get("auth_type") or settings.get("auth_mechanism")
        if raw is None:
            return "LDAP"
        normalized = str(raw).strip().upper()
        if normalized not in cls._AUTH_MECHANISMS:
            raise ValueError(f"Unsupported Impala auth mechanism: {raw!r}")
        return normalized

    @staticmethod
    def _bool_setting(
        settings: dict[str, str | int | float | bool],
        key: str,
        *,
        default: bool,
    ) -> bool:
        value = settings.get(key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        raise ValueError(f"Unable to parse Impala boolean setting {key}: {value!r}")

    @staticmethod
    def _int_setting(
        settings: dict[str, str | int | float | bool],
        key: str,
        *,
        default: int,
    ) -> int:
        value = settings.get(key)
        if value is None:
            return default
        parsed = int(value)
        if parsed < 0:
            raise ValueError(f"Impala setting {key} must be non-negative")
        return parsed

    @classmethod
    def _optional_int(
        cls,
        settings: dict[str, str | int | float | bool],
        key: str,
    ) -> int | None:
        if key not in settings or settings[key] in ("", None):
            return None
        return cls._int_setting(settings, key, default=0)

    @staticmethod
    def _optional_str(
        settings: dict[str, str | int | float | bool],
        key: str,
    ) -> str | None:
        value = settings.get(key)
        if value is None:
            return None
        parsed = str(value).strip()
        return parsed or None


class _ImpalaStartupOperation:
    """Run blocking Impala startup behind a process-level deadline."""

    def __init__(
        self,
        *,
        connect_kwargs: dict[str, Any],
        sql: str,
        server_timeout_seconds: int,
        row_limit: int,
    ) -> None:
        self._connect_kwargs = connect_kwargs
        self._sql = sql
        self._server_timeout_seconds = server_timeout_seconds
        self._row_limit = row_limit
        self._done = Event()
        self._timed_out = Event()
        self._lock = RLock()
        self._connection: Any | None = None
        self._cursor: Any | None = None
        self._cleanup_started = False
        self._result: ImpalaActiveExecution | None = None
        self._error: Exception | None = None

    def run(self, *, timeout_seconds: float, timeout_ms: int) -> ImpalaActiveExecution:
        Thread(
            target=self._execute,
            name="impala-startup",
            daemon=True,
        ).start()
        if not self._done.wait(timeout_seconds):
            self._timed_out.set()
            self._cleanup_async(require_cursor=True)
            raise TimeoutError(f"Impala startup timed out after {timeout_ms} ms.")

        with self._lock:
            error = self._error
            result = self._result
        if error is not None:
            raise error
        if result is None:
            raise RuntimeError("Impala startup finished without an active execution.")
        return result

    def _execute(self) -> None:
        try:
            connection = impala_connect(**self._connect_kwargs)
            with self._lock:
                self._connection = connection
            self._raise_if_timed_out()

            cursor = connection.cursor()
            with self._lock:
                self._cursor = cursor
            self._raise_if_timed_out()

            cursor.execute(f"SET QUERY_TIMEOUT_S={self._server_timeout_seconds}")
            cursor.execute(f"SET EXEC_TIME_LIMIT_S={self._server_timeout_seconds}")
            cursor.execute(self._sql)
            self._raise_if_timed_out()

            with self._lock:
                self._result = ImpalaActiveExecution(
                    connection=connection,
                    cursor=cursor,
                    row_limit=self._row_limit,
                )
        except Exception as exc:
            with self._lock:
                self._error = exc
        finally:
            self._done.set()
            if self._error is not None or self._timed_out.is_set():
                self._cleanup_async()

    def _raise_if_timed_out(self) -> None:
        if self._timed_out.is_set():
            raise TimeoutError("Impala startup deadline expired.")

    def _cleanup_async(self, *, require_cursor: bool = False) -> None:
        with self._lock:
            if self._cleanup_started:
                return
            cursor = self._cursor
            connection = self._connection
            if cursor is None and (require_cursor or connection is None):
                return
            self._cleanup_started = True
            self._cursor = None
            self._connection = None

        Thread(
            target=self._cleanup,
            args=(cursor, connection),
            name="impala-startup-cleanup",
            daemon=True,
        ).start()

    @staticmethod
    def _cleanup(cursor: Any | None, connection: Any | None) -> None:
        if cursor is not None:
            for operation_name in ("cancel_operation", "cancel"):
                operation = getattr(cursor, operation_name, None)
                if not callable(operation):
                    continue
                try:
                    operation()
                except Exception:
                    logger.debug("Impala startup cancel failed", exc_info=True)
                break
            try:
                cursor.close()
            except Exception:
                logger.debug("Impala startup cursor close failed", exc_info=True)
        if connection is not None:
            try:
                connection.close()
            except Exception:
                logger.debug("Impala startup connection close failed", exc_info=True)


class ImpalaAdapter(EngineAdapter):
    """Execution adapter for Impala via impyla DBAPI."""

    def __init__(self, options_factory: ImpalaConnectionOptionsFactory | None = None):
        self._options_factory = options_factory or ImpalaConnectionOptionsFactory()

    def start(
        self, connection: EngineConnectionConfig, request: EngineRunRequest
    ) -> ActiveExecution:
        options = self._options_factory.build(connection.extra_settings)
        default_db = connection.database or connection.schema_name
        timeout_seconds = max(1, ceil(request.timeout_ms / 1000))
        deadline_seconds = max(0.001, request.timeout_ms / 1000)
        client_timeout_seconds = self._client_timeout_seconds(
            configured_timeout=options.timeout,
            deadline_seconds=deadline_seconds,
        )
        operation = _ImpalaStartupOperation(
            connect_kwargs={
                "host": connection.host,
                "port": connection.port,
                "database": default_db,
                "auth_mechanism": options.auth_mechanism,
                "user": connection.username,
                "password": connection.password,
                "use_ssl": connection.secure,
                "use_http_transport": options.use_http_transport,
                "retries": options.retries,
                "ca_cert": options.ca_cert if connection.verify else None,
                "http_path": options.http_path,
                "timeout": client_timeout_seconds,
            },
            sql=request.sql,
            server_timeout_seconds=timeout_seconds,
            row_limit=request.row_limit,
        )
        return operation.run(
            timeout_seconds=deadline_seconds,
            timeout_ms=request.timeout_ms,
        )

    @staticmethod
    def _client_timeout_seconds(
        *,
        configured_timeout: int | None,
        deadline_seconds: float,
    ) -> float:
        if configured_timeout is None or configured_timeout <= 0:
            return deadline_seconds
        return min(float(configured_timeout), deadline_seconds)
