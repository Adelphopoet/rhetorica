from __future__ import annotations

from typing import Any

from rhetorica_backend.sql_guard.models import DEFAULT_SQL_TIMEOUT_MS, SqlGuardPolicy

_MAX_SQL_TIMEOUT_MS = 900_000
_MIN_SQL_TIMEOUT_MS = 100
_QUERY_TIMEOUT_SECONDS_KEYS = (
    "query_timeout_seconds",
    "query_timeout_sec",
    "sql_timeout_seconds",
    "sql_timeout_sec",
)
_QUERY_TIMEOUT_MS_KEYS = ("query_timeout_ms", "sql_timeout_ms")


def sql_guard_policy_for_connection(
    connection: Any,
    base_policy: SqlGuardPolicy | None = None,
) -> SqlGuardPolicy:
    policy = base_policy or SqlGuardPolicy()
    return policy.model_copy(update={"timeout_ms": _query_timeout_ms(connection, policy.timeout_ms)})


def _query_timeout_ms(connection: Any, default: int = DEFAULT_SQL_TIMEOUT_MS) -> int:
    settings = getattr(connection, "extra_settings", None)
    if not isinstance(settings, dict):
        return default

    for key in _QUERY_TIMEOUT_SECONDS_KEYS:
        timeout_ms = _parse_timeout_ms(settings.get(key), multiplier=1000)
        if timeout_ms is not None:
            return timeout_ms

    for key in _QUERY_TIMEOUT_MS_KEYS:
        timeout_ms = _parse_timeout_ms(settings.get(key), multiplier=1)
        if timeout_ms is not None:
            return timeout_ms

    return default


def _parse_timeout_ms(value: Any, *, multiplier: int) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = int(float(value) * multiplier)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return min(max(parsed, _MIN_SQL_TIMEOUT_MS), _MAX_SQL_TIMEOUT_MS)
