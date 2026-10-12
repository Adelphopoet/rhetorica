"""File/Python session adapter backed by the shared process executor."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import signal
import stat
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePath
from typing import Any
from uuid import UUID, uuid4

import httpx
from rhetorica_backend.infra.execution.process import ProcessExecutionResult, ProcessExecutor


class ExecutionRejectedError(ValueError):
    pass


@dataclass(frozen=True)
class FileSessionConfig:
    sessions_root: Path
    workspace_mount: Path = Path("/workspace")
    python_path: str = "/opt/rhetorica-venv/bin/python"
    process_timeout_seconds: int = 60
    session_ttl_minutes: int = 20
    max_input_bytes: int = 25 * 1024 * 1024
    max_output_bytes: int = 50 * 1024 * 1024
    max_session_output_bytes: int = 100 * 1024 * 1024
    max_workspace_bytes: int = 512 * 1024 * 1024
    max_output_files: int = 100
    max_session_output_files: int = 200


@dataclass
class _Session:
    id: str
    root: Path
    created_at: datetime
    touched_at: datetime
    inputs: dict[str, Path] = field(default_factory=dict)
    outputs: dict[str, Path] = field(default_factory=dict)
    output_versions: dict[str, tuple[str, str]] = field(default_factory=dict)
    snapshot_bytes: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    process_lock: threading.Lock = field(default_factory=threading.Lock)
    cancelled: threading.Event = field(default_factory=threading.Event)
    active_process: subprocess.Popen[bytes] | None = None
    operation_results: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)


@dataclass(frozen=True)
class _PendingOutput:
    output_id: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    source: Path


class FileSessionRuntime:
    """Adapts file sessions to the same bounded process primitive as coding."""

    _ALLOWED_OUTPUT_EXTENSIONS = frozenset({
        ".txt",
        ".md",
        ".csv",
        ".tsv",
        ".json",
        ".parquet",
        ".xlsx",
        ".docx",
        ".pptx",
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
    })

    def __init__(self, config: FileSessionConfig, processes: ProcessExecutor) -> None:
        self._config = config
        self._processes = processes
        config.sessions_root.mkdir(parents=True, exist_ok=True)
        config.workspace_mount.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, _Session] = {}
        self._sessions_lock = threading.Lock()
        self._workspace_lock = threading.Lock()

    def create_session(self, session_id: str | None = None) -> str:
        self.cleanup_expired()
        resolved = str(UUID(session_id)) if session_id else str(uuid4())
        with self._sessions_lock:
            if resolved in self._sessions:
                return resolved
            root = self._config.sessions_root / resolved
            (root / "input").mkdir(parents=True, mode=0o700)
            (root / "output").mkdir(mode=0o700)
            now = datetime.now(tz=UTC)
            self._sessions[resolved] = _Session(resolved, root, now, now)
        return resolved

    def execute(
        self,
        *,
        session_id: str,
        code: str,
        inputs: list[dict[str, str]],
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        if not code or len(code) > 20_000:
            raise ExecutionRejectedError("invalid_code")
        session = self._session(session_id)
        with session.lock, self._workspace_lock:
            self._reject_cancelled(session)
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "code": code,
                        "inputs": [
                            {key: value for key, value in item.items() if key != "download_url"}
                            for item in inputs
                        ],
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            if operation_id and operation_id in session.operation_results:
                saved_fingerprint, saved_result = session.operation_results[operation_id]
                if fingerprint != saved_fingerprint:
                    raise ExecutionRejectedError("operation_id_reused")
                session.touched_at = datetime.now(tz=UTC)
                return saved_result
            self._materialize_inputs(session, inputs)
            completed = self._run(session, [self._config.python_path, "-I", "-"], code.encode())
            self._reject_cancelled(session)
            result = {
                "exit_code": completed.exit_code,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "timed_out": completed.timed_out,
                "oom_killed": completed.oom_killed,
                "outputs": self._collect_outputs(session),
            }
            session.touched_at = datetime.now(tz=UTC)
            if operation_id:
                session.operation_results[operation_id] = (fingerprint, result)
            return result

    def inspect(self, *, session_id: str, input_spec: dict[str, str]) -> dict[str, Any]:
        session = self._session(session_id)
        with session.lock, self._workspace_lock:
            self._reject_cancelled(session)
            materialized = self._materialize_inputs(session, [input_spec])[0]
            completed = self._run(
                session,
                [
                    self._config.python_path,
                    "-I",
                    "-m",
                    "rhetorica_backend.infra.execution.inspector",
                    f"/workspace/input/{materialized.name}",
                ],
            )
            self._reject_cancelled(session)
            if completed.exit_code != 0 or completed.timed_out or completed.oom_killed:
                raise ExecutionRejectedError("inspection_failed")
            try:
                payload = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise ExecutionRejectedError("invalid_inspection_result") from exc
            if not isinstance(payload, dict):
                raise ExecutionRejectedError("invalid_inspection_result")
            session.touched_at = datetime.now(tz=UTC)
            return payload

    def output(self, *, session_id: str, output_id: str) -> tuple[Path, str]:
        session = self._session(session_id)
        path = session.outputs.get(output_id)
        if path is None or not path.is_file() or path.is_symlink():
            raise KeyError(output_id)
        session.touched_at = datetime.now(tz=UTC)
        return path, mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    def delete_session(self, session_id: str) -> bool:
        with self._sessions_lock:
            session = self._sessions.get(session_id)
        if session is None:
            return False
        session.cancelled.set()
        with session.process_lock:
            process = session.active_process
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
        with session.lock:
            with self._sessions_lock:
                self._sessions.pop(session_id, None)
            shutil.rmtree(session.root, ignore_errors=True)
        return True

    def cleanup_expired(self) -> int:
        cutoff = datetime.now(tz=UTC) - timedelta(minutes=self._config.session_ttl_minutes)
        with self._sessions_lock:
            expired = [key for key, value in self._sessions.items() if value.touched_at < cutoff]
        for session_id in expired:
            self.delete_session(session_id)
        return len(expired)

    def _run(
        self, session: _Session, argv: list[str], stdin: bytes | None = None
    ) -> ProcessExecutionResult:
        input_link = self._config.workspace_mount / "input"
        output_link = self._config.workspace_mount / "output"
        for link in (input_link, output_link):
            if link.is_symlink() or link.exists():
                if link.is_dir() and not link.is_symlink():
                    shutil.rmtree(link)
                else:
                    link.unlink()
        input_link.symlink_to(session.root / "input", target_is_directory=True)
        output_link.symlink_to(session.root / "output", target_is_directory=True)
        env = {
            "PATH": "/opt/rhetorica-venv/bin:/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "MPLBACKEND": "Agg",
            "HOME": "/tmp",  # noqa: S108 - container-scoped tmpfs
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
        }

        def started(process: subprocess.Popen[bytes]) -> None:
            with session.process_lock:
                self._reject_cancelled(session)
                session.active_process = process

        def finished(process: subprocess.Popen[bytes]) -> None:
            with session.process_lock:
                if session.active_process is process:
                    session.active_process = None

        try:
            return self._processes.run(
                argv,
                cwd=self._config.workspace_mount,
                env=env,
                timeout_seconds=self._config.process_timeout_seconds,
                stdin=stdin,
                on_started=started,
                on_finished=finished,
            )
        finally:
            input_link.unlink(missing_ok=True)
            output_link.unlink(missing_ok=True)

    def _session(self, session_id: str) -> _Session:
        self.cleanup_expired()
        with self._sessions_lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def _materialize_inputs(self, session: _Session, specs: list[dict[str, str]]) -> list[Path]:
        if len(specs) > 6:
            raise ExecutionRejectedError("too_many_inputs")
        result: list[Path] = []
        for spec in specs:
            self._reject_cancelled(session)
            artifact_id = str(spec.get("artifact_id") or "")
            filename = self._safe_name(str(spec.get("filename") or ""))
            existing = session.inputs.get(artifact_id)
            if existing is not None:
                result.append(existing)
                continue
            target = session.root / "input" / filename
            if target.exists():
                raise ExecutionRejectedError("duplicate_input_filename")
            self._download(
                url=str(spec.get("download_url") or ""),
                target=target,
                expected_sha256=str(spec.get("sha256") or ""),
                existing_workspace_bytes=self._workspace_bytes(session.root),
                cancelled=session.cancelled,
            )
            target.chmod(0o400)
            session.inputs[artifact_id] = target
            result.append(target)
        return result

    def _download(
        self,
        *,
        url: str,
        target: Path,
        expected_sha256: str,
        existing_workspace_bytes: int,
        cancelled: threading.Event,
    ) -> None:
        if not url or len(expected_sha256) != 64:
            raise ExecutionRejectedError("invalid_input_reference")
        digest = hashlib.sha256()
        size_bytes = 0
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            with httpx.stream("GET", url, timeout=30.0, follow_redirects=False) as response:
                response.raise_for_status()
                with temporary.open("xb") as output:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        if cancelled.is_set():
                            raise ExecutionRejectedError("operation_cancelled")
                        size_bytes += len(chunk)
                        if size_bytes > self._config.max_input_bytes:
                            raise ExecutionRejectedError("input_too_large")
                        if existing_workspace_bytes + size_bytes > self._config.max_workspace_bytes:
                            raise ExecutionRejectedError("workspace_too_large")
                        digest.update(chunk)
                        output.write(chunk)
            if digest.hexdigest() != expected_sha256:
                raise ExecutionRejectedError("input_hash_mismatch")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def _collect_outputs(self, session: _Session) -> list[dict[str, Any]]:
        paths = sorted((session.root / "output").iterdir(), key=lambda item: item.name)
        if len(paths) > self._config.max_output_files:
            raise ExecutionRejectedError("too_many_output_files")
        total_bytes = 0
        pending: list[_PendingOutput] = []
        for path in paths:
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise ExecutionRejectedError("invalid_output_entry")
            filename = self._safe_name(path.name)
            if PurePath(filename).suffix.lower() not in self._ALLOWED_OUTPUT_EXTENSIONS:
                raise ExecutionRejectedError("unsupported_output_format")
            size_bytes = path.stat().st_size
            total_bytes += size_bytes
            if size_bytes > self._config.max_output_bytes:
                raise ExecutionRejectedError("output_too_large")
            if total_bytes > self._config.max_session_output_bytes:
                raise ExecutionRejectedError("session_output_too_large")
            digest = self._sha256(path)
            previous = session.output_versions.get(filename)
            if previous is not None and previous[0] == digest:
                continue
            pending.append(
                _PendingOutput(
                    output_id=f"out_{uuid4().hex}",
                    filename=filename,
                    media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
                    size_bytes=size_bytes,
                    sha256=digest,
                    source=path,
                )
            )
        pending_bytes = sum(item.size_bytes for item in pending)
        if session.snapshot_bytes + pending_bytes > self._config.max_session_output_bytes:
            raise ExecutionRejectedError("session_output_too_large")
        if len(session.outputs) + len(pending) > self._config.max_session_output_files:
            raise ExecutionRejectedError("too_many_session_output_files")
        if self._workspace_bytes(session.root) + pending_bytes > self._config.max_workspace_bytes:
            raise ExecutionRejectedError("workspace_too_large")
        if not pending:
            return []
        batch_id = f"batch_{uuid4().hex}"
        staged_root = session.root / "snapshots" / f".{batch_id}.pending"
        committed_root = session.root / "snapshots" / batch_id
        try:
            for item in pending:
                staged = staged_root / item.output_id / item.filename
                staged.parent.mkdir(parents=True, mode=0o700)
                shutil.copyfile(item.source, staged)
                staged.chmod(0o400)
            staged_root.replace(committed_root)
        except Exception:
            shutil.rmtree(staged_root, ignore_errors=True)
            raise
        manifests: list[dict[str, Any]] = []
        for item in pending:
            snapshot = committed_root / item.output_id / item.filename
            session.outputs[item.output_id] = snapshot
            session.output_versions[item.filename] = (item.sha256, item.output_id)
            manifests.append({
                "output_id": item.output_id,
                "filename": item.filename,
                "media_type": item.media_type,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
            })
        session.snapshot_bytes += pending_bytes
        return sorted(manifests, key=lambda item: str(item["filename"]))

    @staticmethod
    def _reject_cancelled(session: _Session) -> None:
        if session.cancelled.is_set():
            raise ExecutionRejectedError("operation_cancelled")

    @staticmethod
    def _workspace_bytes(root: Path) -> int:
        return sum(
            path.stat().st_size
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )

    @staticmethod
    def _safe_name(filename: str) -> str:
        normalized = filename.strip()
        if (
            not normalized
            or len(normalized) > 255
            or "\x00" in normalized
            or "/" in normalized
            or "\\" in normalized
            or PurePath(normalized).name != normalized
            or normalized in {".", ".."}
        ):
            raise ExecutionRejectedError("invalid_filename")
        return normalized

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
