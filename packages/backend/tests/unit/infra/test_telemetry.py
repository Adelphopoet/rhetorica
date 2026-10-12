from __future__ import annotations

from unittest.mock import MagicMock

import rhetorica_backend.infra.telemetry as telemetry_module
from rhetorica_backend.infra.telemetry import (
    TelemetryRuntime,
    configure_telemetry,
    instrument_celery,
)


def test_configure_telemetry_is_opt_in(monkeypatch) -> None:
    exporter = MagicMock()
    monkeypatch.setattr(telemetry_module, "OTLPSpanExporter", exporter)

    runtime = configure_telemetry(
        enabled=False,
        service_name="test",
        service_version="1",
        environment="test",
        otlp_endpoint="http://collector:4317",
        otlp_insecure=True,
    )

    assert runtime.enabled is False
    exporter.assert_not_called()


def test_configure_telemetry_requires_endpoint(monkeypatch) -> None:
    exporter = MagicMock()
    monkeypatch.setattr(telemetry_module, "OTLPSpanExporter", exporter)

    runtime = configure_telemetry(
        enabled=True,
        service_name="test",
        service_version="1",
        environment="test",
        otlp_endpoint=" ",
        otlp_insecure=True,
    )

    assert runtime.enabled is False
    exporter.assert_not_called()


def test_configure_telemetry_installs_trace_and_metric_providers(monkeypatch) -> None:
    resource = MagicMock()
    tracer_provider = MagicMock()
    meter_provider = MagicMock()
    span_exporter = MagicMock()
    metric_exporter = MagicMock()
    span_processor = MagicMock()
    metric_reader = MagicMock()

    resource_create = MagicMock(return_value=resource)
    monkeypatch.setattr(telemetry_module.Resource, "create", resource_create)
    monkeypatch.setattr(
        telemetry_module, "TracerProvider", MagicMock(return_value=tracer_provider)
    )
    monkeypatch.setattr(
        telemetry_module, "MeterProvider", MagicMock(return_value=meter_provider)
    )
    monkeypatch.setattr(
        telemetry_module, "OTLPSpanExporter", MagicMock(return_value=span_exporter)
    )
    monkeypatch.setattr(
        telemetry_module, "OTLPMetricExporter", MagicMock(return_value=metric_exporter)
    )
    monkeypatch.setattr(
        telemetry_module, "BatchSpanProcessor", MagicMock(return_value=span_processor)
    )
    monkeypatch.setattr(
        telemetry_module,
        "PeriodicExportingMetricReader",
        MagicMock(return_value=metric_reader),
    )
    set_tracer_provider = MagicMock()
    set_meter_provider = MagicMock()
    monkeypatch.setattr(telemetry_module.trace, "set_tracer_provider", set_tracer_provider)
    monkeypatch.setattr(telemetry_module.metrics, "set_meter_provider", set_meter_provider)
    monkeypatch.setattr(telemetry_module.socket, "gethostname", lambda: "pod-1")

    runtime = configure_telemetry(
        enabled=True,
        service_name="rhetorica-api",
        service_version="1.2.3",
        environment="dev",
        otlp_endpoint=" http://collector:4317 ",
        otlp_insecure=True,
    )

    assert runtime.enabled is True
    resource_create.assert_called_once_with(
        {
            "service.name": "rhetorica-api",
            "service.namespace": "rhetorica",
            "service.version": "1.2.3",
            "service.instance.id": "pod-1",
            "deployment.environment.name": "dev",
        }
    )
    telemetry_module.OTLPSpanExporter.assert_called_once_with(
        endpoint="http://collector:4317", insecure=True
    )
    telemetry_module.OTLPMetricExporter.assert_called_once_with(
        endpoint="http://collector:4317", insecure=True
    )
    tracer_provider.add_span_processor.assert_called_once_with(span_processor)
    set_tracer_provider.assert_called_once_with(tracer_provider)
    set_meter_provider.assert_called_once_with(meter_provider)


def test_configure_telemetry_fails_open(monkeypatch) -> None:
    tracer_provider = MagicMock()
    monkeypatch.setattr(
        telemetry_module, "TracerProvider", MagicMock(return_value=tracer_provider)
    )
    monkeypatch.setattr(
        telemetry_module,
        "OTLPSpanExporter",
        MagicMock(side_effect=RuntimeError("bad exporter")),
    )

    runtime = configure_telemetry(
        enabled=True,
        service_name="test",
        service_version="1",
        environment="test",
        otlp_endpoint="http://collector:4317",
        otlp_insecure=True,
    )

    assert runtime.enabled is False
    tracer_provider.shutdown.assert_called_once_with()


def test_runtime_shutdown_is_idempotent() -> None:
    tracer_provider = MagicMock()
    meter_provider = MagicMock()
    runtime = TelemetryRuntime(
        enabled=True,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )

    runtime.shutdown()
    runtime.shutdown()

    meter_provider.shutdown.assert_called_once_with()
    tracer_provider.shutdown.assert_called_once_with()


def test_celery_instrumentation_fails_open(monkeypatch) -> None:
    instrument = MagicMock(side_effect=RuntimeError("broken instrumentation"))
    instrumentor = MagicMock(instrument=instrument)
    monkeypatch.setattr(
        telemetry_module, "CeleryInstrumentor", MagicMock(return_value=instrumentor)
    )

    installed = instrument_celery(TelemetryRuntime(enabled=True))

    assert installed is False
