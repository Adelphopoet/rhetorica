from __future__ import annotations

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from rhetorica_backend.infra.logging import get_logger
from rhetorica_backend.infra.telemetry import (
    TelemetryRuntime,
    configure_telemetry,
    instrument_celery,
)

logger = get_logger(__name__)


def install_api_telemetry(
    app: FastAPI,
    *,
    enabled: bool,
    service_version: str,
    environment: str,
    otlp_endpoint: str,
    otlp_insecure: bool,
) -> TelemetryRuntime:
    runtime = configure_telemetry(
        enabled=enabled,
        service_name="rhetorica-api",
        service_version=service_version,
        environment=environment,
        otlp_endpoint=otlp_endpoint,
        otlp_insecure=otlp_insecure,
    )
    if not runtime.enabled:
        return runtime

    instrument_celery(runtime)
    try:
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=runtime.tracer_provider,
            meter_provider=runtime.meter_provider,
            excluded_urls=r".*/healthz",
            exclude_spans=["receive", "send"],
        )
    except Exception:
        logger.exception("telemetry.fastapi.instrumentation_failed")
    return runtime
