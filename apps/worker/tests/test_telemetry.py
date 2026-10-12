from importlib import import_module
from unittest.mock import MagicMock

from rhetorica_backend.infra.telemetry import TelemetryRuntime
from rhetorica_worker.runtime import telemetry as telemetry_module

celery_runtime = import_module("rhetorica_worker.runtime.celery_app")


def test_worker_telemetry_starts_after_fork_and_stops_cleanly(monkeypatch) -> None:
    runtime = TelemetryRuntime(
        enabled=True,
        tracer_provider=MagicMock(),
        meter_provider=MagicMock(),
    )
    configure = MagicMock(return_value=runtime)
    instrument = MagicMock()
    monkeypatch.setattr(telemetry_module, "configure_telemetry", configure)
    monkeypatch.setattr(telemetry_module, "instrument_celery", instrument)
    worker_telemetry = telemetry_module.WorkerTelemetry(
        enabled=True,
        service_version="1",
        environment="dev",
        otlp_endpoint="http://collector:4317",
        otlp_insecure=True,
    )

    worker_telemetry.start()
    worker_telemetry.stop()

    configure.assert_called_once_with(
        enabled=True,
        service_name="rhetorica-worker",
        service_version="1",
        environment="dev",
        otlp_endpoint="http://collector:4317",
        otlp_insecure=True,
    )
    instrument.assert_called_once_with(runtime)
    assert runtime._closed is True


def test_worker_process_signals_delegate_to_telemetry_runtime(monkeypatch) -> None:
    worker_telemetry = MagicMock()
    monkeypatch.setattr(celery_runtime, "_telemetry", worker_telemetry)

    celery_runtime._start_worker_telemetry()
    celery_runtime._stop_worker_telemetry()

    worker_telemetry.start.assert_called_once_with()
    worker_telemetry.stop.assert_called_once_with()
