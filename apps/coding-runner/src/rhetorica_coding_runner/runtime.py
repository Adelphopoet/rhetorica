from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import tarfile
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rhetorica_backend.contracts.coding import DbtCommand, DbtRunRequest
from rhetorica_backend.domain.coding.dbt import DbtAdmissionError, DbtCommandPolicy
from rhetorica_backend.infra.coding.archives import ArchivePolicy, SafeArchive
from rhetorica_backend.infra.execution import ProcessExecutor, ProcessLimits

from rhetorica_coding_runner.contracts import ExecuteRequest
from rhetorica_coding_runner.settings import CodingRunnerSettings


class ExecutionRuntimeError(RuntimeError):
    pass


class ExecutionRuntime:
    def __init__(self, settings: CodingRunnerSettings) -> None:
        self._settings = settings
        self._policy = ArchivePolicy(
            max_files=settings.max_files,
            max_file_bytes=settings.max_file_bytes,
            max_total_bytes=settings.max_snapshot_bytes,
        )
        self._dbt_policy = DbtCommandPolicy()
        self._processes = ProcessExecutor(
            ProcessLimits(
                max_log_bytes=settings.max_output_bytes,
                pid_limit=settings.pid_limit,
                memory_limit_bytes=settings.process_memory_limit_bytes,
                file_size_limit_bytes=settings.max_output_bytes,
            )
        )
        from rhetorica_coding_runner.python_environment import PythonEnvironmentPreparer

        self.python_environment = PythonEnvironmentPreparer(settings.scratch_root, self._run)
        self._cancelled = threading.Event()
        self._process_lock = threading.Lock()
        self._active_processes: dict[int, subprocess.Popen[bytes]] = {}

    def stage(self, payload: bytes, *, expected_digest: str) -> dict[str, Any]:
        self._reject_cancelled()
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected_digest:
            raise ExecutionRuntimeError("snapshot_digest_mismatch")
        root = self._settings.workspace_root
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=False)
        validated = SafeArchive.extract(payload, target=str(root), policy=self._policy)
        self._settings.scratch_root.mkdir(parents=True, exist_ok=True)
        (self._settings.scratch_root / "baseline-paths.json").write_text(
            json.dumps(list(validated.paths)), encoding="utf-8"
        )
        self._initialize_git_baseline(root)
        self._reject_cancelled()
        return {"digest": actual, "paths": len(validated.paths), "size_bytes": len(payload)}

    def execute(self, request: ExecuteRequest) -> dict[str, Any]:
        self._reject_cancelled()
        started = time.monotonic()
        operation = request.operation
        arguments = request.arguments
        if self._settings.phase == "dependencies" and operation != "prepare_dependencies":
            raise ExecutionRuntimeError("operation_not_allowed_in_dependency_phase")
        if operation == "read_file":
            path = self._path(str(arguments.get("path", "")), require_file=False)
            if not path.is_file():
                return self._result(operation, 1, "", "repository_file_not_found", started)
            offset = self._integer_argument(arguments, "offset", 0)
            max_chars = min(self._integer_argument(arguments, "max_chars", 12_000), 64_000)
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return self._result(operation, 1, "", "repository_file_not_utf8", started)
            stdout = text[offset : offset + max_chars]
            self._reject_cancelled()
            result = self._result(operation, 0, stdout, "", started)
            next_offset = offset + len(result["stdout"])
            result["truncated"] = result["truncated"] or next_offset < len(text)
            result.update({
                "offset": offset,
                "next_offset": next_offset,
                "total_chars": len(text),
            })
            return result
        if operation == "search":
            pattern = str(arguments.get("pattern", ""))
            if not pattern or len(pattern) > 1_000:
                raise ExecutionRuntimeError("search_pattern_invalid")
            prefix = str(arguments.get("path_prefix") or ".")
            root = self._settings.workspace_root.resolve()
            if prefix != ".":
                normalized = SafeArchive.normalize_path(prefix)
                path = (root / normalized).resolve()
                if root not in path.parents:
                    raise ExecutionRuntimeError("repository_path_invalid")
                if not path.exists():
                    return self._result(operation, 1, "", "repository_path_not_found", started)
            limit = min(self._integer_argument(arguments, "limit", 50), 100)
            argv = [
                "rg",
                "--no-heading",
                "--line-number",
                "--color",
                "never",
                "--max-count",
                str(limit),
                "--",
                pattern,
                prefix,
            ]
            return self._run(operation, argv, request.timeout_seconds, started=started)
        if operation == "apply_patch":
            patch = str(arguments.get("patch", ""))
            if not patch or len(patch) > 256_000:
                raise ExecutionRuntimeError("patch_invalid")
            applied = self._run(
                operation,
                ["git", "apply", "--recount", "--whitespace=nowarn", "-"],
                request.timeout_seconds,
                stdin=patch,
                started=started,
            )
            if applied["exit_code"] != 0:
                return applied
            # git apply --intent-to-add can replace the existing index. Use Git's
            # dedicated index operation to retain baseline entries and prior edits.
            return self._run(
                operation,
                ["git", "add", "--intent-to-add", "--all"],
                request.timeout_seconds,
                started=started,
            )
        if operation == "run_command":
            command_argument = arguments.get("argv")
            if (
                not isinstance(command_argument, list)
                or not command_argument
                or len(command_argument) > 64
            ):
                raise ExecutionRuntimeError("command_argv_invalid")
            values = [str(item) for item in command_argument]
            if any(len(item) > 4096 for item in values):
                raise ExecutionRuntimeError("command_argv_invalid")
            return self._run(operation, values, request.timeout_seconds, started=started)
        if operation == "prepare_dependencies":
            if self._settings.phase != "dependencies":
                raise ExecutionRuntimeError("dependency_operation_requires_isolated_phase")
            return self._prepare_dependencies(request.timeout_seconds, started=started)
        if operation == "run_dbt":
            if request.dbt_request is None or request.dbt_runtime_profile is None:
                raise ExecutionRuntimeError("run_dbt_requires_trusted_profile")
            return self._run_dbt(
                request=request.dbt_request,
                profile=request.dbt_runtime_profile,
                timeout=request.timeout_seconds,
                started=started,
            )
        raise ExecutionRuntimeError("operation_not_supported")

    def collect(self) -> tuple[bytes, dict[str, Any]]:
        root = self._settings.workspace_root
        output = io.BytesIO()
        paths: list[str] = []
        total = 0
        baseline_file = self._settings.scratch_root / "baseline-paths.json"
        baseline = set(json.loads(baseline_file.read_text())) if baseline_file.is_file() else set()
        with (
            gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed,
            tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive,
        ):
            for path in sorted(root.rglob("*")):
                relative = path.relative_to(root).as_posix()
                if (
                    relative == ".git"
                    or relative.startswith(".git/")
                    or relative.startswith(".rhetorica/")
                    or (
                        relative not in baseline
                        and any(
                            part
                            in {
                                "target",
                                "logs",
                                "dbt_packages",
                                "__pycache__",
                                ".pytest_cache",
                                ".venv",
                            }
                            for part in path.relative_to(root).parts
                        )
                    )
                ):
                    continue
                stat = path.lstat()
                if path.is_symlink() or not (path.is_dir() or path.is_file()):
                    raise ExecutionRuntimeError("candidate_non_regular_entry")
                if path.is_dir():
                    continue
                if stat.st_nlink != 1:
                    raise ExecutionRuntimeError("candidate_hardlink_forbidden")
                if stat.st_size > self._settings.max_file_bytes:
                    raise ExecutionRuntimeError("candidate_file_size_exceeded")
                total += stat.st_size
                if total > self._settings.max_output_bytes:
                    raise ExecutionRuntimeError("candidate_output_size_exceeded")
                data = path.read_bytes()
                info = tarfile.TarInfo(relative)
                info.size = len(data)
                info.mode = 0o644 | (0o111 if stat.st_mode & 0o111 else 0)
                info.mtime = 0
                archive.addfile(info, io.BytesIO(data))
                paths.append(relative)
        payload = output.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        return payload, {"digest": digest, "size_bytes": len(payload), "regular_paths": paths}

    def cancel(self) -> None:
        self._cancelled.set()
        marker = self._cancelled_marker
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("cancelled\n", encoding="utf-8")
        with self._process_lock:
            for process in self._active_processes.values():
                if process.poll() is None:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)

    def _prepare_dependencies(self, timeout: int, *, started: float) -> dict[str, Any]:
        root = self._settings.workspace_root
        if (root / "uv.lock").is_file() and (root / "pyproject.toml").is_file():
            argv = ["uv", "sync", "--frozen", "--no-editable"]
        elif (root / "requirements.lock").is_file():
            argv = ["python", "-m", "pip", "install", "--require-hashes", "-r", "requirements.lock"]
        elif (root / "packages.yml").is_file() and (root / "package-lock.yml").is_file():
            argv = ["dbt", "deps", "--project-dir", "."]
        else:
            raise ExecutionRuntimeError("pinned_dependency_manifest_required")
        return self._run(
            "prepare_dependencies", argv, timeout, started=started, dependency_phase=True
        )

    def _run_dbt(
        self,
        *,
        request: DbtRunRequest,
        profile: Any,
        timeout: int,
        started: float,
    ) -> dict[str, Any]:
        if profile.expires_at <= datetime.now(tz=UTC):
            raise ExecutionRuntimeError("dbt_gateway_profile_expired")
        project_dir = (self._settings.workspace_root / request.project_subdir).resolve()
        workspace_root = self._settings.workspace_root.resolve()
        if (project_dir != workspace_root and workspace_root not in project_dir.parents) or not (
            project_dir / "dbt_project.yml"
        ).is_file():
            raise ExecutionRuntimeError("dbt_project_path_invalid")
        profiles_dir = self._settings.scratch_root / "dbt-profile"
        profiles_dir.mkdir(parents=True, exist_ok=True)
        profile_path = profiles_dir / "profiles.yml"
        profile_path.write_text(profile.profiles_yml, encoding="utf-8")
        profile_path.chmod(0o600)
        env = {
            "HOME": str(self._settings.scratch_root),
            "DBT_PROFILES_DIR": str(profiles_dir),
            "PYTHONNOUSERSITE": "1",
        }
        if request.command == DbtCommand.DATA_TEST:
            parse_request = DbtRunRequest(
                command=DbtCommand.PARSE,
                project_subdir=request.project_subdir,
                variables=request.variables,
                threads=request.threads,
            )
            parse_result = self._run(
                "run_dbt",
                self._dbt_policy.argv(
                    parse_request,
                    project_dir=project_dir,
                    profiles_dir=profiles_dir,
                    profile_name=profile.profile_name,
                ),
                timeout,
                started=started,
                env_overrides=env,
                fail_on_truncation=True,
            )
            if int(parse_result["exit_code"]) != 0:
                parse_result["dbt_receipt"] = self._dbt_policy.receipt(
                    request=request,
                    exit_code=int(parse_result["exit_code"]),
                    duration_ms=int(parse_result["duration_ms"]),
                    stdout=str(parse_result["stdout"]),
                    incomplete=bool(parse_result["truncated"]),
                    reason="dbt_parse_failed",
                ).model_dump(mode="json")
                return parse_result
            try:
                self._dbt_policy.validate_test_manifest(project_dir / "target" / "manifest.json")
            except DbtAdmissionError as exc:
                raise ExecutionRuntimeError(str(exc)) from exc

        result = self._run(
            "run_dbt",
            self._dbt_policy.argv(
                request,
                project_dir=project_dir,
                profiles_dir=profiles_dir,
                profile_name=profile.profile_name,
            ),
            timeout,
            started=started,
            env_overrides=env,
            fail_on_truncation=True,
        )
        if (
            request.command == DbtCommand.LIST
            and result["exit_code"] == 0
            and not result["truncated"]
        ):
            # dbt ls --quiet --output json has a JSON-lines output contract.
            # Decode Unicode and remove redundant spacing before the SDK sees it.
            try:
                nodes = [
                    json.loads(line) for line in str(result["stdout"]).splitlines() if line.strip()
                ]
            except json.JSONDecodeError:
                nodes = []
            if nodes and all(isinstance(node, dict) and "unique_id" in node for node in nodes):
                result["stdout"] = "\n".join(
                    json.dumps(node, ensure_ascii=False, separators=(",", ":")) for node in nodes
                )
            else:
                result["exit_code"] = 65
                result["stderr"] += "\ndbt_list_output_invalid"
        incomplete = bool(result["truncated"]) or int(result["exit_code"]) == 124
        reason = None
        if bool(result["truncated"]):
            reason = "dbt_output_budget_exceeded"
        elif int(result["exit_code"]) == 124:
            reason = "dbt_command_timeout"
        result["dbt_receipt"] = self._dbt_policy.receipt(
            request=request,
            exit_code=int(result["exit_code"]),
            duration_ms=int(result["duration_ms"]),
            stdout=str(result["stdout"]),
            incomplete=incomplete,
            reason=reason,
        ).model_dump(mode="json")
        return result

    def _run(
        self,
        operation: str,
        argv: list[str],
        timeout: int,
        *,
        started: float,
        stdin: str | None = None,
        dependency_phase: bool = False,
        env_overrides: dict[str, str] | None = None,
        fail_on_truncation: bool = False,
    ) -> dict[str, Any]:
        env = {
            "PATH": os.environ.get("PATH", "/opt/rhetorica-venv/bin:/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        env.update(self.python_environment.environment())
        if dependency_phase:
            sources = [
                item.strip()
                for item in self._settings.allowed_dependency_sources.split(",")
                if item.strip()
            ]
            if not sources:
                raise ExecutionRuntimeError("dependency_sources_not_configured")
            env["PIP_INDEX_URL"] = sources[0]
            env["UV_INDEX"] = sources[0]
            env["DBT_PACKAGES_INSTALL_PATH"] = str(self._settings.scratch_root / "dbt_packages")
        if env_overrides:
            env.update(env_overrides)
        self._reject_cancelled()
        try:
            completed = self._processes.run(
                argv,
                cwd=self._settings.workspace_root,
                env=env,
                timeout_seconds=timeout,
                stdin=stdin.encode() if stdin is not None else None,
                on_started=self._on_process_started,
                on_finished=self._on_process_finished,
            )
        except FileNotFoundError:
            return self._result(operation, 127, "", "command_not_found", started)
        self._reject_cancelled()
        return self._result(
            operation,
            completed.exit_code,
            completed.stdout,
            completed.stderr,
            started,
            fail_on_truncation=fail_on_truncation,
            forced_truncated=completed.truncated,
        )

    @property
    def _cancelled_marker(self) -> Path:
        return self._settings.scratch_root / "cancelled"

    def _reject_cancelled(self) -> None:
        if self._cancelled.is_set() or self._cancelled_marker.exists():
            self._cancelled.set()
            raise ExecutionRuntimeError("operation_cancelled")

    def _on_process_started(self, process: subprocess.Popen[bytes]) -> None:
        with self._process_lock:
            if self._cancelled.is_set() or self._cancelled_marker.exists():
                self._cancelled.set()
                raise ExecutionRuntimeError("operation_cancelled")
            self._active_processes[process.pid] = process

    def _on_process_finished(self, process: subprocess.Popen[bytes]) -> None:
        with self._process_lock:
            self._active_processes.pop(process.pid, None)

    def _result(
        self,
        operation: str,
        exit_code: int,
        stdout: str,
        stderr: str,
        started: float,
        *,
        fail_on_truncation: bool = False,
        forced_truncated: bool = False,
    ) -> dict[str, Any]:
        limit = self._settings.max_output_bytes
        encoded = (stdout + stderr).encode(errors="replace")
        truncated = forced_truncated or len(encoded) > limit
        if truncated:
            stdout = stdout[: limit // 2]
            stderr = stderr[: limit // 2]
            if fail_on_truncation:
                exit_code = 74
                stderr += "\ndbt_output_budget_exceeded"
        return {
            "operation": operation,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": truncated,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    def _path(self, relative: str, *, require_file: bool = True) -> Path:
        normalized = SafeArchive.normalize_path(relative)
        root = self._settings.workspace_root.resolve()
        path = (root / normalized).resolve()
        if root not in path.parents or (require_file and not path.is_file()):
            raise ExecutionRuntimeError("repository_path_invalid")
        return path

    @staticmethod
    def _integer_argument(
        arguments: dict[str, str | int | bool | list[str]], name: str, default: int
    ) -> int:
        value = arguments.get(name, default)
        if isinstance(value, bool) or not isinstance(value, str | int):
            raise ExecutionRuntimeError(f"{name}_invalid")
        return int(value)

    @staticmethod
    def _initialize_git_baseline(root: Path) -> None:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        commands = [
            ["git", "init", "--quiet"],
            ["git", "config", "core.hooksPath", "/dev/null"],
            ["git", "config", "user.name", "Rhetorica Coding Runner"],
            ["git", "config", "user.email", "coding-runner@localhost"],
            ["git", "add", "--all"],
            ["git", "commit", "--quiet", "--no-gpg-sign", "-m", "Pinned repository snapshot"],
        ]
        for command in commands:
            subprocess.run(  # noqa: S603 - fixed Git argv; shell execution is disabled.
                command, cwd=root, env=env, check=True, capture_output=True
            )
