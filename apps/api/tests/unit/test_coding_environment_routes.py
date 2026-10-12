from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import coding_environments as routes
from rhetorica_api.app.settings import settings
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


def _client(tmp_path: Path) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/coding-environments-api.db"
    settings.auth_enabled = False
    settings.coding_runner_image_digest = "runner@sha256:" + "a" * 64
    settings.coding_runner_runtime_class_name = "runsc"
    settings.coding_runner_software_manifest_digest = "b" * 64
    routes._repository.cache_clear()
    DatabaseRuntime(settings.database_url).create_schema()
    return TestClient(create_app())


def test_admin_can_create_and_version_gvisor_resource_profile(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path)
    monkeypatch.setattr(routes, "_tool_versions", lambda _: {"python": "3.12.12", "dbt-core": "1.11.15"})
    payload = {
        "id": "gvisor-small",
        "expected_revision": 0,
        "max_runtime_seconds": 1200,
        "cpu_limit": "1000m",
        "memory_limit": "2Gi",
        "disk_limit_bytes": 5 * 1024**3,
        "pid_limit": 128,
        "output_limit_bytes": 64 * 1024**2,
        "pip_dependencies": ["pytest==8.3.5"],
        "dependency_sources": ["https://packages.example/simple"],
    }

    first = client.put("/v1/admin/coding-environments/gvisor-small", json=payload)
    assert first.status_code == 200
    assert first.json()["revision"] == 1
    assert first.json()["pip_dependencies"] == ["pytest==8.3.5"]
    assert first.json()["tool_versions"]["dbt-core"] == "1.11.15"
    assert first.json()["runtime_class_name"] == "runsc"
    assert first.json()["image_digest"].endswith("a" * 64)

    conflict = client.put("/v1/admin/coding-environments/gvisor-small", json=payload)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["current_revision"] == 1
    assert client.get("/v1/admin/coding-environments").json()[0]["id"] == "gvisor-small"
