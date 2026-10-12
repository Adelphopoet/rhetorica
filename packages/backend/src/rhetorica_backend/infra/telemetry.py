from __future__ import annotations

import socket
from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from rhetorica_backend.infra.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class TelemetryRuntime:
    """Owns process-local OpenTelemetry providers and their shutdown lifecycle."""

    enabled: bool
    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None
    _closed: bool = False

    @classmethod
    def disabled(cls) -> TelemetryRuntime:
        return cls(enabled=False)

    def shutdown(self) -> None:
        if not self.enabled or self._closed:
            return
        self._closed = True
        if self.meter_provider is not None:
            try:
                self.meter_provider.shutdown()
            except Exception:
                logger.exception("telemetry.metrics.shutdown_failed")
        if self.tracer_provider is not None:
            try:
                self.tracer_provider.shutdown()
            except Exception:
                logger.exception("telemetry.traces.shutdown_failed")


def configure_telemetry(
    *,
    enabled: bool,
    service_name: str,
    service_version: str,
    environment: str,
    otlp_endpoint: str,
    otlp_insecure: bool,
) -> TelemetryRuntime:
    """Configure OTLP traces and metrics without making app startup depend on telemetry."""

    if not enabled:
        logger.info("telemetry.disabled", reason="configuration")
        return TelemetryRuntime.disabled()

    endpoint = otlp_endpoint.strip()
    if not endpoint:
        logger.error("telemetry.disabled", reason="otlp_endpoint_missing")
        return TelemetryRuntime.disabled()

    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None
    try:
        resource = Resource.create({
            "service.name": service_name,
            "service.namespace": "rhetorica",
            "service.version": service_version,
            "service.instance.id": socket.gethostname(),
            "deployment.environment.name": environment,
        })
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=otlp_insecure))
        )
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=endpoint, insecure=otlp_insecure)
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])

        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)
    except Exception:
        logger.exception("telemetry.disabled", reason="initialization_failed")
        TelemetryRuntime(
            enabled=True,
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
        ).shutdown()
        return TelemetryRuntime.disabled()

    logger.info(
        "telemetry.enabled",
        otlp_endpoint=endpoint,
        signals=["traces", "metrics"],
    )
    return TelemetryRuntime(
        enabled=True,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )


def instrument_celery(runtime: TelemetryRuntime) -> bool:
    """Install Celery propagation and task spans in the current process."""

    if not runtime.enabled:
        return False
    try:
        CeleryInstrumentor().instrument(
            tracer_provider=runtime.tracer_provider,
            meter_provider=runtime.meter_provider,
        )
    except Exception:
        logger.exception("telemetry.celery.instrumentation_failed")
        return False
    return True
