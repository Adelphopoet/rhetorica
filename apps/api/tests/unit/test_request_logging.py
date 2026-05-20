import logging

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app


def test_request_logging_adds_request_id_header() -> None:
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]


def test_request_logging_emits_started_and_completed_records(caplog) -> None:
    client = TestClient(create_app())

    with caplog.at_level(logging.INFO):
        response = client.get("/healthz?ping=1")

    assert response.status_code == 200
    started = next(
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == "http.request.started"
    )
    completed = next(
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == "http.request.completed"
    )

    assert started["http_method"] == "GET"
    assert started["http_path"] == "/healthz"
    assert started["query_params"] == {"ping": "1"}
    assert completed["http_method"] == "GET"
    assert completed["http_path"] == "/healthz"
    assert completed["status_code"] == 200
