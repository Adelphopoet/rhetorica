from __future__ import annotations

import hashlib
import io
import json
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from rhetorica_backend.contracts.coding import DbtRunRequest, DbtRuntimeProfile
from rhetorica_coding_runner.contracts import ExecuteRequest
from rhetorica_coding_runner.runtime import ExecutionRuntime, ExecutionRuntimeError
from rhetorica_coding_runner.settings import CodingRunnerSettings


def _snapshot() -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        content = b"select 1\n"
        info = tarfile.TarInfo("models/orders.sql")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def _dbt_snapshot(*, store_failures: bool = False) -> bytes:
    output = io.BytesIO()
    files = {
        "dbt_project.yml": b"name: fixture\nversion: '1.0'\nprofile: rhetorica\nmodel-paths: [models]\n",
        "models/orders.sql": b"select 1 as order_id\n",
        "models/schema.yml": (
            b"version: 2\nmodels:\n  - name: orders\n    data_tests:\n      - not_null:\n"
            + (b"          config:\n            store_failures: true\n" if store_failures else b"")
            + b"          column_name: order_id\n"
        ),
    }
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def _runtime(tmp_path) -> ExecutionRuntime:
    return ExecutionRuntime(
        CodingRunnerSettings(
            task_token="token",
            workspace_root=tmp_path / "repo",
            scratch_root=tmp_path / "scratch",
            max_snapshot_bytes=1024 * 1024,
            max_output_bytes=1024 * 1024,
        )
    )


def test_missing_inspection_paths_are_recoverable_but_escape_is_rejected(tmp_path):
    runtime = _runtime(tmp_path)
    snapshot = _snapshot()
    runtime.stage(snapshot, expected_digest=hashlib.sha256(snapshot).hexdigest())
    for operation, arguments in [
        ("search", {"pattern": "test", "path_prefix": "null"}),
        ("read_file", {"path": "missing.md"}),
    ]:
        result = runtime.execute(
            ExecuteRequest(operation=operation, arguments=arguments, timeout_seconds=5)
        )
        assert result["exit_code"] == 1 and "not_found" in result["stderr"]
    with pytest.raises((ExecutionRuntimeError, ValueError)):
        runtime.execute(
            ExecuteRequest(
                operation="search",
                arguments={"pattern": "test", "path_prefix": "../outside"},
                timeout_seconds=5,
            )
        )


def _dependency_runtime(tmp_path) -> ExecutionRuntime:
    return ExecutionRuntime(
        CodingRunnerSettings(
            task_token="token",
            workspace_root=tmp_path / "repo",
            scratch_root=tmp_path / "scratch",
            max_snapshot_bytes=1024 * 1024,
            max_output_bytes=1024 * 1024,
            phase="dependencies",
            allowed_dependency_sources="http://package-mirror:8080/simple",
        )
    )


def test_runtime_stages_executes_and_collects_immutable_candidate(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    snapshot = _snapshot()
    runtime.stage(snapshot, expected_digest=hashlib.sha256(snapshot).hexdigest())

    result = runtime.execute(
        ExecuteRequest(
            operation="run_command",
            arguments={
                "argv": [
                    "python",
                    "-c",
                    "from pathlib import Path; Path('models/orders.sql').write_text('select 2\\n')",
                ]
            },
            timeout_seconds=5,
        )
    )
    candidate, manifest = runtime.collect()

    assert result["exit_code"] == 0
    assert manifest["digest"] == hashlib.sha256(candidate).hexdigest()
    assert manifest["regular_paths"] == ["models/orders.sql"]


def test_runtime_rejects_snapshot_digest_mismatch(tmp_path) -> None:
    with pytest.raises(ExecutionRuntimeError, match="snapshot_digest_mismatch"):
        _runtime(tmp_path).stage(_snapshot(), expected_digest="0" * 64)


def test_cancelled_runtime_rejects_new_execution(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    snapshot = _snapshot()
    runtime.stage(snapshot, expected_digest=hashlib.sha256(snapshot).hexdigest())
    runtime.cancel()

    with pytest.raises(ExecutionRuntimeError, match="operation_cancelled"):
        runtime.execute(
            ExecuteRequest(
                operation="run_command",
                arguments={"argv": ["python", "-c", "print('must not run')"]},
                timeout_seconds=5,
            )
        )


def test_cancel_interrupts_active_process_group(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    snapshot = _snapshot()
    runtime.stage(snapshot, expected_digest=hashlib.sha256(snapshot).hexdigest())
    started = tmp_path / "repo" / "started"
    completed = tmp_path / "repo" / "completed"
    request = ExecuteRequest(
        operation="run_command",
        arguments={
            "argv": [
                "python",
                "-c",
                (
                    "from pathlib import Path; import time; "
                    "Path('started').write_text('yes'); time.sleep(30); "
                    "Path('completed').write_text('no')"
                ),
            ]
        },
        timeout_seconds=40,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runtime.execute, request)
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists()
        runtime.cancel()
        with pytest.raises(ExecutionRuntimeError, match="operation_cancelled"):
            future.result(timeout=5)

    assert completed.exists() is False


def test_dependency_phase_requires_lock_and_approved_source(tmp_path) -> None:
    runtime = _dependency_runtime(tmp_path)
    snapshot = _snapshot()
    runtime.stage(snapshot, expected_digest=hashlib.sha256(snapshot).hexdigest())

    with pytest.raises(ExecutionRuntimeError, match="pinned_dependency_manifest_required"):
        runtime.execute(
            ExecuteRequest(operation="prepare_dependencies", arguments={}, timeout_seconds=5)
        )


def test_runtime_rejects_dependency_install_in_development_phase(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    snapshot = _snapshot()
    runtime.stage(snapshot, expected_digest=hashlib.sha256(snapshot).hexdigest())

    with pytest.raises(ExecutionRuntimeError, match="dependency_operation_requires_isolated_phase"):
        runtime.execute(
            ExecuteRequest(operation="prepare_dependencies", arguments={}, timeout_seconds=5)
        )


def test_runtime_builds_typed_dbt_show_and_marks_materialization_not_run(
    tmp_path, monkeypatch
) -> None:
    runtime = _runtime(tmp_path)
    snapshot = _dbt_snapshot()
    runtime.stage(snapshot, expected_digest=hashlib.sha256(snapshot).hexdigest())
    seen: list[list[str]] = []

    def fake_run(operation, argv, timeout, **_kwargs):
        seen.append(argv)
        return {
            "operation": operation,
            "exit_code": 0,
            "stdout": "one row",
            "stderr": "",
            "truncated": False,
            "duration_ms": 12,
        }

    monkeypatch.setattr(runtime, "_run", fake_run)
    result = runtime.execute(
        ExecuteRequest(
            operation="run_dbt",
            arguments={},
            timeout_seconds=30,
            dbt_request=DbtRunRequest(command="show", selectors=["orders"], show_limit=5),
            dbt_runtime_profile=DbtRuntimeProfile(
                profile_name="rhetorica",
                profiles_yml=json.dumps({"rhetorica": {"target": "gateway", "outputs": {}}}),
                expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
            ),
        )
    )

    assert seen[0][0:2] == ["dbt", "show"]
    assert seen[0][-2:] == ["--limit", "5"]
    assert result["dbt_receipt"]["status"] == "passed"
    assert result["dbt_receipt"]["materialization_status"] == "not_run"


def test_run_dbt_does_not_accept_shell_arguments() -> None:
    with pytest.raises(ValueError, match="run_dbt_arguments_forbidden"):
        ExecuteRequest(
            operation="run_dbt",
            arguments={"argv": ["dbt", "build"]},
            timeout_seconds=30,
            dbt_request=DbtRunRequest(command="parse"),
            dbt_runtime_profile=DbtRuntimeProfile(
                profile_name="rhetorica",
                profiles_yml="{}",
                expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
            ),
        )


def test_runtime_reports_truncated_command_without_changing_success(tmp_path) -> None:
    runtime = ExecutionRuntime(
        CodingRunnerSettings(
            task_token="token",
            workspace_root=tmp_path / "repo",
            scratch_root=tmp_path / "scratch",
            max_snapshot_bytes=1024 * 1024,
            max_output_bytes=16,
        )
    )
    snapshot = _snapshot()
    runtime.stage(snapshot, expected_digest=hashlib.sha256(snapshot).hexdigest())

    result = runtime.execute(
        ExecuteRequest(
            operation="run_command",
            arguments={"argv": ["python", "-c", "print('x' * 128)"]},
            timeout_seconds=5,
        )
    )

    assert result["exit_code"] == 0
    assert result["truncated"] is True


def test_runtime_fails_dbt_result_when_output_is_truncated(tmp_path) -> None:
    runtime = _runtime(tmp_path)

    result = runtime._result(
        "run_dbt",
        0,
        "partial output",
        "",
        0.0,
        fail_on_truncation=True,
        forced_truncated=True,
    )

    assert result["exit_code"] == 74
    assert result["truncated"] is True
    assert "dbt_output_budget_exceeded" in result["stderr"]


def test_coding_sessions_are_independent_and_restore_working_tree(tmp_path):
    from uuid import uuid4

    from rhetorica_coding_runner.sessions import CodingSessionRegistry

    settings = CodingRunnerSettings(
        task_token="token", workspace_root=tmp_path / "repo", scratch_root=tmp_path / "scratch"
    )
    registry = CodingSessionRegistry(settings)
    developer, qa = str(uuid4()), str(uuid4())
    payload = _snapshot()
    for task_id in [developer, qa]:
        registry.get(task_id).stage(payload, expected_digest=hashlib.sha256(payload).hexdigest())
    root = tmp_path / "coding-sessions" / developer / "repository"
    (root / "models/orders.sql").write_text("select 2\n")
    (root / "target").mkdir()
    (root / "target/manifest.json").write_text("generated")
    restarted = CodingSessionRegistry(settings)
    assert restarted.staged(developer)
    candidate, _ = restarted.get(developer).collect()
    with tarfile.open(fileobj=io.BytesIO(candidate)) as archive:
        assert "target/manifest.json" not in archive.getnames()
        assert archive.extractfile("models/orders.sql").read() == b"select 2\n"
    assert (
        registry.get(qa).execute(
            ExecuteRequest(
                operation="read_file", arguments={"path": "models/orders.sql"}, timeout_seconds=5
            )
        )["stdout"]
        == "select 1\n"
    )
    restarted.close(developer)
    assert not root.exists()
    assert registry.staged(qa)


def test_collection_preserves_baseline_files_inside_generated_directories(tmp_path):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        info = tarfile.TarInfo("local-airflow/logs/.gitkeep")
        archive.addfile(info, io.BytesIO(b""))
    payload = output.getvalue()
    runtime = _runtime(tmp_path)
    runtime.stage(payload, expected_digest=hashlib.sha256(payload).hexdigest())
    (tmp_path / "repo/local-airflow/logs/new.log").write_text("generated")
    candidate, _ = runtime.collect()
    with tarfile.open(fileobj=io.BytesIO(candidate)) as archive:
        assert archive.getnames() == ["local-airflow/logs/.gitkeep"]


def test_missing_executable_returns_a_receipt_instead_of_transport_failure(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)

    def missing(*args, **kwargs):
        raise FileNotFoundError("missing executable")

    monkeypatch.setattr(runtime._processes, "run", missing)
    result = runtime._run("shell", ["missing"], 5, started=time.monotonic())
    assert result["exit_code"] == 127
    assert result["stderr"] == "command_not_found"


def test_file_read_pagination_reports_exact_continuation(tmp_path):
    runtime = _runtime(tmp_path)
    snapshot = _snapshot()
    runtime.stage(snapshot, expected_digest=hashlib.sha256(snapshot).hexdigest())
    result = runtime.execute(
        ExecuteRequest(
            operation="read_file",
            arguments={"path": "models/orders.sql", "max_chars": 3},
            timeout_seconds=5,
        )
    )
    assert result["stdout"] == "sel"
    assert result["truncated"] is True and result["next_offset"] == 3
    rest = runtime.execute(
        ExecuteRequest(
            operation="read_file",
            arguments={"path": "models/orders.sql", "offset": 3},
            timeout_seconds=5,
        )
    )
    assert rest["stdout"] == "ect 1\n"
    assert rest["truncated"] is False and rest["next_offset"] == rest["total_chars"] == 9


def test_non_utf8_file_read_is_actionable_tool_failure(tmp_path):
    runtime = _runtime(tmp_path)
    snapshot = _snapshot()
    runtime.stage(snapshot, expected_digest=hashlib.sha256(snapshot).hexdigest())
    (tmp_path / "repo" / "binary.dat").write_bytes(b"\xff\xfe")
    result = runtime.execute(
        ExecuteRequest(operation="read_file", arguments={"path": "binary.dat"}, timeout_seconds=5)
    )
    assert result["exit_code"] == 1 and result["stderr"] == "repository_file_not_utf8"


def test_patch_uses_git_recount_without_relaxing_context_matching(tmp_path):
    runtime = _runtime(tmp_path)
    snapshot = _snapshot()
    runtime.stage(snapshot, expected_digest=hashlib.sha256(snapshot).hexdigest())
    patch = "--- /dev/null\n+++ b/models/smoke.sql\n@@ -0,0 +1,5 @@\n+select 1 as id\n"
    result = runtime.execute(
        ExecuteRequest(operation="apply_patch", arguments={"patch": patch}, timeout_seconds=5)
    )
    assert result["exit_code"] == 0
    assert (tmp_path / "repo/models/smoke.sql").read_text() == "select 1 as id\n"
    diff = runtime.execute(
        ExecuteRequest(
            operation="run_command", arguments={"argv": ["git", "diff"]}, timeout_seconds=5
        )
    )
    assert "+select 1 as id" in diff["stdout"]
    bad = "--- a/models/smoke.sql\n+++ b/models/smoke.sql\n@@ -1 +1 @@\n-select 99 as id\n+select 2 as id\n"
    result = runtime.execute(
        ExecuteRequest(operation="apply_patch", arguments={"patch": bad}, timeout_seconds=5)
    )
    assert result["exit_code"] != 0
    assert (tmp_path / "repo/models/smoke.sql").read_text() == "select 1 as id\n"


def test_patches_preserve_baseline_and_previous_new_files_in_index(tmp_path):
    runtime = _runtime(tmp_path)
    snapshot = _snapshot()
    runtime.stage(snapshot, expected_digest=hashlib.sha256(snapshot).hexdigest())
    for name in ("first", "second"):
        patch = (
            f"diff --git a/models/{name}.sql b/models/{name}.sql\n"
            f"new file mode 100644\n--- /dev/null\n+++ b/models/{name}.sql\n"
            "@@ -0,0 +1,99 @@\n+select 1 as id\n"
        )
        result = runtime.execute(
            ExecuteRequest(operation="apply_patch", arguments={"patch": patch}, timeout_seconds=5)
        )
        assert result["exit_code"] == 0, result
    result = runtime.execute(
        ExecuteRequest(
            operation="run_command",
            arguments={"argv": ["git", "status", "--porcelain"]},
            timeout_seconds=5,
        )
    )
    assert result["stdout"].splitlines() == [" A models/first.sql", " A models/second.sql"]
    tracked = runtime.execute(
        ExecuteRequest(
            operation="run_command", arguments={"argv": ["git", "ls-files"]}, timeout_seconds=5
        )
    )
    assert "models/orders.sql" in tracked["stdout"].splitlines()
    patch = "--- a/models/orders.sql\n+++ b/models/orders.sql\n@@ -1 +1 @@\n-select 1\n+select 2\n"
    result = runtime.execute(
        ExecuteRequest(operation="apply_patch", arguments={"patch": patch}, timeout_seconds=5)
    )
    assert result["exit_code"] == 0
    result = runtime.execute(
        ExecuteRequest(
            operation="run_command",
            arguments={"argv": ["git", "diff", "HEAD", "--name-only"]},
            timeout_seconds=5,
        )
    )
    assert result["stdout"].splitlines() == [
        "models/first.sql",
        "models/orders.sql",
        "models/second.sql",
    ]


@pytest.mark.parametrize("command", ["list", "compile", "show", "data_test"])
def test_real_dbt_empty_selection_is_not_a_passed_check(tmp_path, command):
    import shutil

    if shutil.which("dbt") is None:
        pytest.skip("Pinned Docker dbt runtime is required")
    runtime = _runtime(tmp_path)
    snapshot = _dbt_snapshot()
    runtime.stage(snapshot, expected_digest=hashlib.sha256(snapshot).hexdigest())
    result = runtime.execute(
        ExecuteRequest(
            operation="run_dbt",
            arguments={},
            timeout_seconds=30,
            dbt_request=DbtRunRequest(
                command=command,
                selectors=["missing_model"],
                show_limit=1 if command == "show" else None,
                no_introspect=command == "compile",
                no_populate_cache=command in {"compile", "show", "data_test"},
            ),
            dbt_runtime_profile=DbtRuntimeProfile(
                profile_name="rhetorica",
                profiles_yml=json.dumps({
                    "rhetorica": {
                        "target": "local",
                        "outputs": {
                            "local": {
                                "type": "trino",
                                "host": "127.0.0.1",
                                "port": 9,
                                "user": "test",
                                "database": "system",
                                "schema": "runtime",
                                "threads": 1,
                                "http_scheme": "http",
                            }
                        },
                    }
                }),
                expires_at=datetime.now(UTC) + timedelta(minutes=2),
            ),
        )
    )
    assert result["exit_code"] != 0
    assert "missing_model" in result["stdout"] and "does not match" in result["stdout"]
    assert result["dbt_receipt"]["status"] == "failed"


def test_dbt_list_returns_compact_unicode_metadata_without_log_wrappers(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    snapshot = _dbt_snapshot()
    runtime.stage(snapshot, expected_digest=hashlib.sha256(snapshot).hexdigest())
    node = {
        "unique_id": "model.fixture.orders",
        "description": "Заказы",
        "columns": {},
        "config": {"materialized": "view"},
    }

    def output(operation, argv, timeout, **kwargs):
        assert "--quiet" in argv and "--output-keys" in argv
        return {
            "operation": operation,
            "exit_code": 0,
            "stdout": json.dumps(node) + "\n",
            "stderr": "",
            "truncated": False,
            "duration_ms": 1,
        }

    monkeypatch.setattr(runtime, "_run", output)
    result = runtime.execute(
        ExecuteRequest(
            operation="run_dbt",
            arguments={},
            timeout_seconds=5,
            dbt_request=DbtRunRequest(command="list", selectors=["orders"]),
            dbt_runtime_profile=DbtRuntimeProfile(
                profile_name="rhetorica",
                profiles_yml="{}",
                expires_at=datetime.now(UTC) + timedelta(minutes=1),
            ),
        )
    )
    assert "Заказы" in result["stdout"]
    assert json.loads(result["stdout"]) == node
    assert result["dbt_receipt"]["status"] == "passed"
