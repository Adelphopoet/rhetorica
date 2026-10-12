from __future__ import annotations

from dataclasses import dataclass, field

from rhetorica_backend.infra.telemetry import (
    TelemetryRuntime,
    configure_telemetry,
    instrument_celery,
)


@dataclass(slots=True)
class WorkerTelemetry:
    enabled: bool
    service_version: str
    environment: str
    otlp_endpoint: str
    otlp_insecure: bool
    runtime: TelemetryRuntime = field(default_factory=TelemetryRuntime.disabled, init=False)

    def start(self) -> None:
        self.runtime = configure_telemetry(
            enabled=self.enabled,
            service_name="rhetorica-worker",
            service_version=self.service_version,
            environment=self.environment,
            otlp_endpoint=self.otlp_endpoint,
            otlp_insecure=self.otlp_insecure,
        )
        instrument_celery(self.runtime)

    def stop(self) -> None:
        self.runtime.shutdown()
