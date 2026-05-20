from fastapi.testclient import TestClient
from rhetorica_api.app.main import app


def test_sql_guard_route_returns_guard_decision() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/sql/guard",
        json={
            "sql": "SELECT city FROM sales",
            "dialect": "clickhouse",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["allowed"] is True
    assert "LIMIT 5000" in payload["normalized_sql"].upper()


def test_sql_guard_route_blocks_insert() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/sql/guard",
        json={
            "sql": "INSERT INTO t SELECT 1",
            "dialect": "clickhouse",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["allowed"] is False
    assert payload["violations"][0]["code"] == "NON_READ_ONLY"
