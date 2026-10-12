"""Shared prompt window contract for SQL tool results."""

from __future__ import annotations

from typing import Any

DEFAULT_SQL_PROMPT_ROWS = 100
MAX_SQL_PROMPT_ROWS = 500
DEFAULT_SQL_EXECUTION_ROWS = 5_000
MAX_SQL_EXECUTION_ROWS = 1_048_575


def normalize_sql_prompt_row_limit(value: Any) -> int:
    """Clamp caller-requested SQL prompt rows to the explicit product contract."""
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_SQL_PROMPT_ROWS
    return max(1, min(value, MAX_SQL_PROMPT_ROWS))


def normalize_sql_execution_row_limit(value: Any) -> int:
    """Clamp the guarded result snapshot independently from the LLM preview."""
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_SQL_EXECUTION_ROWS
    return max(1, min(value, MAX_SQL_EXECUTION_ROWS))
