from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock


def test_large_repository_candidate_uses_bounded_headers(monkeypatch, tmp_path):
    monkeypatch.setenv("EXECUTION_RUNTIME_INTERNAL_TOKEN", "test-token")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "repository"))
    monkeypatch.setenv("SCRATCH_ROOT", str(tmp_path / "scratch"))
    monkeypatch.setenv("EXECUTION_RUNTIME_SESSIONS_ROOT", str(tmp_path / "files"))
    monkeypatch.setattr(
        "rhetorica_backend.infra.execution.file_sessions.FileSessionRuntime",
        lambda *args: Mock(),
    )
    app = import_module("rhetorica_coding_runner.app")
    session = SimpleNamespace(
        collect=Mock(
            return_value=(
                b"archive",
                {
                    "digest": "a" * 64,
                    "files": [{"path": f"models/{i}.sql", "digest": "b" * 64} for i in range(2000)],
                },
            )
        )
    )
    monkeypatch.setattr(app, "coding_sessions", SimpleNamespace(get=lambda _: session))
    response = app.coding_candidate("session", token="test-token")
    assert response.body == b"archive"
    assert response.headers["X-Rhetorica-Artifact-Sha256"] == "a" * 64
    assert sum(len(key) + len(value) for key, value in response.raw_headers) < 1024
