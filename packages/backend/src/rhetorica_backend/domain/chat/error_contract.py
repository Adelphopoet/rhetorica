"""Shared error taxonomy and retry helpers for chat domain."""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import Enum
from secrets import randbelow
from typing import Any, TypeVar

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError

T = TypeVar("T")


class ErrorCategory(str, Enum):
    """Error category for orchestration and tool contracts."""

    TRANSIENT = "TRANSIENT"
    RECOVERABLE = "RECOVERABLE"
    FATAL = "FATAL"


TRANSIENT_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


def is_transient_http_status(status_code: int) -> bool:
    """Check if HTTP status is typically transient."""
    return status_code in TRANSIENT_HTTP_STATUSES


def classify_exception(exc: Exception) -> tuple[ErrorCategory, str, str, bool]:
    """Classify exception into error contract tuple."""
    if isinstance(
        exc,
        httpx.TimeoutException
        | httpx.ConnectError
        | httpx.NetworkError
        | httpx.RemoteProtocolError
        | httpx.ReadError
        | httpx.WriteError,
    ):
        return (
            ErrorCategory.TRANSIENT,
            "network_transient_error",
            "Temporary network issue. Please retry.",
            True,
        )
    if isinstance(exc, APIConnectionError | APITimeoutError):
        return (
            ErrorCategory.TRANSIENT,
            "provider_connection_transient_error",
            "Temporary network issue. Please retry.",
            True,
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if is_transient_http_status(status):
            return (
                ErrorCategory.TRANSIENT,
                f"http_{status}",
                f"Temporary upstream error ({status}). Please retry.",
                True,
            )
        if status in {400, 404, 422}:
            return (
                ErrorCategory.RECOVERABLE,
                f"http_{status}",
                "Request parameters are invalid for this operation.",
                False,
            )
        return (
            ErrorCategory.FATAL,
            f"http_{status}",
            "Upstream service returned a fatal error.",
            False,
        )
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        if is_transient_http_status(status):
            return (
                ErrorCategory.TRANSIENT,
                f"http_{status}",
                f"Temporary upstream error ({status}). Please retry.",
                True,
            )
        if status in {400, 404, 422}:
            return (
                ErrorCategory.RECOVERABLE,
                f"http_{status}",
                "Request parameters are invalid for this operation.",
                False,
            )
        return (
            ErrorCategory.FATAL,
            f"http_{status}",
            "Upstream service returned a fatal error.",
            False,
        )
    return (
        ErrorCategory.FATAL,
        type(exc).__name__.lower(),
        "Operation failed with a fatal error.",
        False,
    )


def build_tool_error(
    error_code: str,
    error_category: ErrorCategory,
    user_message: str,
    retry_recommended: bool,
    can_continue: bool,
) -> dict[str, Any]:
    """Build normalized tool error payload."""
    return {
        "ok": False,
        "error": error_code,  # backward compatibility
        "error_code": error_code,
        "error_category": error_category.value,
        "user_message": user_message,
        "retry_recommended": retry_recommended,
        "can_continue": can_continue,
    }


def retry_idempotent(
    operation: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.2,
    max_delay_seconds: float = 2.0,
    jitter_ratio: float = 0.2,
) -> T:
    """
    Execute idempotent operation with exponential backoff and jitter.

    Retries only transient exceptions. Non-transient exceptions are re-raised.
    """
    if max_attempts <= 1:
        return operation()

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            last_exc = exc
            category, _, _, _ = classify_exception(exc)
            if category != ErrorCategory.TRANSIENT or attempt >= max_attempts:
                raise

            delay = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
            jitter = delay * jitter_ratio * (randbelow(1000) / 1000)
            time.sleep(delay + jitter)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError
