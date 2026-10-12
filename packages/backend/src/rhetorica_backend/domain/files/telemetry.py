"""Low-cardinality telemetry port for the file bounded context."""

from __future__ import annotations

from typing import Protocol


class FileTelemetry(Protocol):
    def record(
        self,
        *,
        operation: str,
        outcome: str,
        duration_seconds: float = 0.0,
        bytes_count: int = 0,
        count: int = 1,
    ) -> None: ...


class NoopFileTelemetry:
    def record(
        self,
        *,
        operation: str,
        outcome: str,
        duration_seconds: float = 0.0,
        bytes_count: int = 0,
        count: int = 1,
    ) -> None:
        return None
