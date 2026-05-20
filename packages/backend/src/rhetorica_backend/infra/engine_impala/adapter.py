from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, ClassVar

from impala.dbapi import connect as impala_connect  # type: ignore[import-untyped]
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.domain.execution.models import (
    ActiveExecution,
    EngineAdapter,
    EngineRunRequest,
    EngineRunResult,
)


class ImpalaActiveExecution(ActiveExecution):
    def __init__(self, connection: Any, cursor: Any):
        self._connection = connection
        self._cursor = cursor
        self.query_id: str | None = getattr(cursor, "query_id", None)

    def fetch(self) -> EngineRunResult:
        rows = self._cursor.fetchall()
        description = self._cursor.description or []
        columns = [column[0] for column in description]
        self.query_id = getattr(self._cursor, "query_id", self.query_id)
        return EngineRunResult(
            query_id=self.query_id,
            columns=columns,
            rows=[list(row) for row in rows],
            row_count=len(rows),
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


class ImpalaAdapter(EngineAdapter):
    """Execution adapter for Impala via impyla DBAPI."""

    def __init__(self, options_factory: ImpalaConnectionOptionsFactory | None = None):
        self._options_factory = options_factory or ImpalaConnectionOptionsFactory()

    def start(
        self, connection: EngineConnectionConfig, request: EngineRunRequest
    ) -> ActiveExecution:
        options = self._options_factory.build(connection.extra_settings)
        default_db = connection.database or connection.schema_name
        conn = impala_connect(
            host=connection.host,
            port=connection.port,
            database=default_db,
            auth_mechanism=options.auth_mechanism,
            user=connection.username,
            password=connection.password,
            use_ssl=connection.secure,
            use_http_transport=options.use_http_transport,
            retries=options.retries,
            ca_cert=options.ca_cert if connection.verify else None,
            http_path=options.http_path,
            timeout=options.timeout,
        )
        cursor = conn.cursor()
        timeout_seconds = max(1, ceil(request.timeout_ms / 1000))
        cursor.execute(f"SET QUERY_TIMEOUT_S={timeout_seconds}")
        cursor.execute(f"SET EXEC_TIME_LIMIT_S={timeout_seconds}")
        cursor.execute(request.sql)
        return ImpalaActiveExecution(connection=conn, cursor=cursor)
