"""OpenTelemetry adapter for bounded file and sandbox measurements."""

from __future__ import annotations

from opentelemetry import metrics
from rhetorica_backend.domain.files.telemetry import FileTelemetry


class OpenTelemetryFileTelemetry(FileTelemetry):
    """Records no identifiers, filenames, URLs, code, or file contents."""

    def __init__(self) -> None:
        meter = metrics.get_meter("rhetorica.file_artifacts")
        self._operations = meter.create_counter(
            "rhetorica.file.operations",
            description="File and sandbox operation outcomes",
        )
        self._bytes = meter.create_counter(
            "rhetorica.file.bytes",
            unit="By",
            description="Bytes transferred by file operations",
        )
        self._duration = meter.create_histogram(
            "rhetorica.file.duration",
            unit="s",
            description="File and sandbox operation duration",
        )

    def record(
        self,
        *,
        operation: str,
        outcome: str,
        duration_seconds: float = 0.0,
        bytes_count: int = 0,
        count: int = 1,
    ) -> None:
        attributes = {"operation": operation, "outcome": outcome}
        self._operations.add(max(count, 0), attributes)
        if bytes_count > 0:
            self._bytes.add(bytes_count, attributes)
        if duration_seconds > 0:
            self._duration.record(duration_seconds, attributes)
