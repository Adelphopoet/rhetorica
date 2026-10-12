from unittest.mock import MagicMock

from fastapi import FastAPI
from rhetorica_api.app import telemetry as telemetry_module
from rhetorica_backend.infra.telemetry import TelemetryRuntime


def test_api_telemetry_stays_uninstrumented_when_disabled(monkeypatch) -> None:
    runtime = TelemetryRuntime.disabled()
    configure = MagicMock(return_value=runtime)
    instrument_celery = MagicMock()
    instrument_fastapi = MagicMock()
    monkeypatch.setattr(telemetry_module, "configure_telemetry", configure)
    monkeypatch.setattr(telemetry_module, "instrument_celery", instrument_celery)
    monkeypatch.setattr(
        telemetry_module.FastAPIInstrumentor,
        "instrument_app",
        instrument_fastapi,
    )

    result = telemetry_module.install_api_telemetry(
        FastAPI(),
        enabled=False,
        service_version="1",
        environment="test",
        otlp_endpoint="",
        otlp_insecure=False,
    )

    assert result is runtime
    instrument_celery.assert_not_called()
    instrument_fastapi.assert_not_called()


def test_api_telemetry_instruments_fastapi_and_celery(monkeypatch) -> None:
    runtime = TelemetryRuntime(
        enabled=True,
        tracer_provider=MagicMock(),
        meter_provider=MagicMock(),
    )
    monkeypatch.setattr(
        telemetry_module, "configure_telemetry", MagicMock(return_value=runtime)
    )
    instrument_celery = MagicMock()
    instrument_fastapi = MagicMock()
    monkeypatch.setattr(telemetry_module, "instrument_celery", instrument_celery)
    monkeypatch.setattr(
        telemetry_module.FastAPIInstrumentor,
        "instrument_app",
        instrument_fastapi,
    )
    app = FastAPI()

    result = telemetry_module.install_api_telemetry(
        app,
        enabled=True,
        service_version="1",
        environment="dev",
        otlp_endpoint="http://collector:4317",
        otlp_insecure=True,
    )

    assert result is runtime
    instrument_celery.assert_called_once_with(runtime)
    instrument_fastapi.assert_called_once_with(
        app,
        tracer_provider=runtime.tracer_provider,
        meter_provider=runtime.meter_provider,
        excluded_urls=r".*/healthz",
        exclude_spans=["receive", "send"],
    )
