"""Local NsJail supervisor implementing the internal sandbox service contract."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePath
from typing import Any
from uuid import UUID, uuid4

import httpx


class SandboxRejectedError(ValueError):
    pass


@dataclass(frozen=True)
class LocalSandboxConfig:
    sessions_root: Path
    nsjail_path: str = "/usr/local/bin/nsjail"
    python_path: str = "/opt/rhetorica-venv/bin/python"
    inspector_path: str = "/opt/rhetorica-sandbox/inspect_runner.py"
    max_parallel_runs: int = 2
    process_timeout_seconds: int = 60
    session_ttl_minutes: int = 20
    max_input_bytes: int = 25 * 1024 * 1024
    max_output_bytes: int = 50 * 1024 * 1024
    max_session_output_bytes: int = 100 * 1024 * 1024
    max_workspace_bytes: int = 512 * 1024 * 1024
    max_tmp_bytes: int = 128 * 1024 * 1024
    max_log_bytes: int = 64 * 1024
    max_output_files: int = 100
    max_session_output_files: int = 200
    memory_limit_bytes: int = 1024 * 1024 * 1024
    pids_limit: int = 64
    cpu_ms_per_second: int = 1000
    manage_legacy_cgroup_parents: bool = False
    cgroup_root: Path = Path("/sys/fs/cgroup")
    cgroup_parent: str = "NSJAIL"


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


class LocalSandboxSupervisor:
    """Creates ephemeral workspaces and runs exactly one jailed process per call."""

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

    def __init__(self, config: LocalSandboxConfig) -> None:
        self._config = config
        if config.manage_legacy_cgroup_parents:
            self._prepare_legacy_cgroup_parents()
        self._config.sessions_root.mkdir(parents=True, exist_ok=True, mode=0o711)
        os.chmod(self._config.sessions_root, 0o711)  # noqa: S103 - jail mount traversal only
        self._sessions: dict[str, _Session] = {}
        self._sessions_lock = threading.Lock()
        self._run_slots = threading.BoundedSemaphore(config.max_parallel_runs)

    def _prepare_legacy_cgroup_parents(self) -> None:
        """Create the controller parents NsJail expects on cgroup v1 hosts.

        Kubernetes can expose writable v1 controller mounts without the
        ``NSJAIL`` parent directory. NsJail creates only the per-process child,
        so the trusted supervisor prepares the fixed parents during startup.
        Cgroup v2 does not use these controller-specific paths.
        """
        root = self._config.cgroup_root
        if (root / "cgroup.controllers").is_file():
            return
        controllers = tuple(root / name for name in ("memory", "pids", "cpu,cpuacct"))
        missing = [str(path) for path in controllers if not path.is_dir()]
        if missing:
            raise RuntimeError(
                f"required cgroup v1 controllers are unavailable: {', '.join(missing)}"
            )
        if not self._config.cgroup_parent.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                "cgroup_parent must contain only letters, digits, underscores, or dashes"
            )
        for controller in controllers:
            (controller / self._config.cgroup_parent).mkdir(mode=0o755, exist_ok=True)

    def create_session(self, session_id: str | None = None) -> str:
        self.cleanup_expired()
        session_id = str(UUID(session_id)) if session_id else str(uuid4())
        with self._sessions_lock:
            if session_id in self._sessions:
                return session_id
            return self._create_session(session_id)

    def _create_session(self, session_id: str) -> str:
        root = self._config.sessions_root / session_id
        for path in (
            root / "rootfs" / "workspace" / "input",
            root / "rootfs" / "workspace" / "output",
            root / "input",
            root / "output",
        ):
            path.mkdir(parents=True, mode=0o700)
        for path in (root, root / "rootfs", root / "rootfs" / "workspace"):
            os.chmod(path, 0o711)  # noqa: S103 - required before dropping jail uid
        for path in (
            root / "rootfs" / "workspace" / "input",
            root / "rootfs" / "workspace" / "output",
        ):
            os.chmod(path, 0o755)  # noqa: S103 - replaced by isolated bind mounts
        os.chmod(root / "input", 0o555)  # noqa: S103 - jail input mount is intentionally read-only
        os.chmod(root / "output", 0o733)  # noqa: S103 - isolated nobody user needs output write
        now = datetime.now(tz=UTC)
        session = _Session(id=session_id, root=root, created_at=now, touched_at=now)
        self._sessions[session_id] = session
        return session_id

    def execute(
        self,
        *,
        session_id: str,
        code: str,
        inputs: list[dict[str, str]],
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        if not code or len(code) > 20_000:
            raise SandboxRejectedError("invalid_code")
        session = self._session(session_id)
        with session.lock, self._run_slots:
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
                    raise SandboxRejectedError("operation_id_reused")
                session.touched_at = datetime.now(tz=UTC)
                return saved_result
            self._materialize_inputs(session, inputs)
            completed = self._run_jailed(session=session, stdin=code.encode("utf-8"))
            self._reject_cancelled(session)
            outputs = self._collect_outputs(session)
            session.touched_at = datetime.now(tz=UTC)
            result = {
                "exit_code": completed[0],
                "stdout": completed[1],
                "stderr": completed[2],
                "timed_out": completed[3],
                "oom_killed": completed[4],
                "outputs": outputs,
            }
            if operation_id:
                session.operation_results[operation_id] = (fingerprint, result)
            return result

    def inspect(
        self,
        *,
        session_id: str,
        input_spec: dict[str, str],
    ) -> dict[str, Any]:
        session = self._session(session_id)
        with session.lock, self._run_slots:
            self._reject_cancelled(session)
            materialized = self._materialize_inputs(session, [input_spec])[0]
            jail_path = f"/workspace/input/{materialized.name}"
            completed = self._run_jailed(
                session=session,
                argv=[self._config.python_path, "-I", self._config.inspector_path, jail_path],
            )
            self._reject_cancelled(session)
            if completed[0] != 0 or completed[3] or completed[4]:
                raise SandboxRejectedError("inspection_failed")
            try:
                payload = json.loads(completed[1])
            except json.JSONDecodeError as exc:
                raise SandboxRejectedError("invalid_inspection_result") from exc
            if not isinstance(payload, dict):
                raise SandboxRejectedError("invalid_inspection_result")
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
                process.kill()
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

    def _session(self, session_id: str) -> _Session:
        self.cleanup_expired()
        with self._sessions_lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def _materialize_inputs(self, session: _Session, specs: list[dict[str, str]]) -> list[Path]:
        # Five user files plus the workflow's JSON input manifest.
        if len(specs) > 6:
            raise SandboxRejectedError("too_many_inputs")
        result: list[Path] = []
        for spec in specs:
            self._reject_cancelled(session)
            artifact_id = str(spec.get("artifact_id") or "")
            filename = self._safe_name(str(spec.get("filename") or ""))
            expected_sha256 = str(spec.get("sha256") or "")
            existing = session.inputs.get(artifact_id)
            if existing is not None:
                result.append(existing)
                continue
            target = session.root / "input" / filename
            if target.exists():
                raise SandboxRejectedError("duplicate_input_filename")
            self._download(
                url=str(spec.get("download_url") or ""),
                target=target,
                expected_sha256=expected_sha256,
                existing_workspace_bytes=self._workspace_bytes(session.root),
                cancelled=session.cancelled,
            )
            os.chmod(target, 0o400)
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
            raise SandboxRejectedError("invalid_input_reference")
        digest = hashlib.sha256()
        size_bytes = 0
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            with httpx.stream("GET", url, timeout=30.0, follow_redirects=False) as response:
                response.raise_for_status()
                with temporary.open("xb") as output:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        if cancelled.is_set():
                            raise SandboxRejectedError("operation_cancelled")
                        size_bytes += len(chunk)
                        if size_bytes > self._config.max_input_bytes:
                            raise SandboxRejectedError("input_too_large")
                        if existing_workspace_bytes + size_bytes > self._config.max_workspace_bytes:
                            raise SandboxRejectedError("workspace_too_large")
                        digest.update(chunk)
                        output.write(chunk)
            if digest.hexdigest() != expected_sha256:
                raise SandboxRejectedError("input_hash_mismatch")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def _run_jailed(
        self,
        *,
        session: _Session,
        stdin: bytes | None = None,
        argv: list[str] | None = None,
    ) -> tuple[int, str, str, bool, bool]:
        command = self._nsjail_command(session)
        command.extend(argv or [self._config.python_path, "-I", "-"])
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LD_LIBRARY_PATH": "/usr/local/lib:/usr/lib:/lib",
            "MPLBACKEND": "Agg",
            "HOME": "/tmp",  # noqa: S108 - /tmp is a per-jail tmpfs
            "PYTHONHASHSEED": "0",
        }
        timed_out = False
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            with session.process_lock:
                self._reject_cancelled(session)
                process = subprocess.Popen(  # noqa: S603 - fixed argv; code uses stdin
                    command,
                    stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    env=env,
                    close_fds=True,
                )
                session.active_process = process
            try:
                process.communicate(input=stdin, timeout=self._config.process_timeout_seconds + 5)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                process.wait(timeout=5)
            finally:
                with session.process_lock:
                    if session.active_process is process:
                        session.active_process = None
            stdout.seek(0)
            stderr.seek(0)
            stdout_text = stdout.read(self._config.max_log_bytes).decode("utf-8", "replace")
            stderr_text = stderr.read(self._config.max_log_bytes).decode("utf-8", "replace")
        oom_killed = process.returncode in {-9, 137} and not timed_out
        return process.returncode, stdout_text, stderr_text, timed_out, oom_killed

    @staticmethod
    def _reject_cancelled(session: _Session) -> None:
        if session.cancelled.is_set():
            raise SandboxRejectedError("operation_cancelled")

    def _nsjail_command(self, session: _Session) -> list[str]:
        rootfs = session.root / "rootfs"
        command = [
            self._config.nsjail_path,
            "--mode",
            "o",
            "--quiet",
            "--chroot",
            str(rootfs),
            "--cwd",
            "/workspace",
            "--user",
            "65534",
            "--group",
            "65534",
            "--hostname",
            "rhetorica-sandbox",
            "--env",
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "--env",
            "LANG=C.UTF-8",
            "--env",
            "LD_LIBRARY_PATH=/usr/local/lib:/usr/lib:/lib",
            "--env",
            "MPLBACKEND=Agg",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONHASHSEED=0",
            "--time_limit",
            str(self._config.process_timeout_seconds),
            "--rlimit_as",
            str(self._config.memory_limit_bytes // (1024 * 1024)),
            "--rlimit_cpu",
            str(self._config.process_timeout_seconds),
            "--rlimit_fsize",
            str(self._config.max_output_bytes // (1024 * 1024)),
            "--rlimit_nproc",
            str(self._config.pids_limit),
            "--detect_cgroupv2",
            "--cgroup_mem_max",
            str(self._config.memory_limit_bytes),
            "--cgroup_mem_parent",
            self._config.cgroup_parent,
            "--cgroup_pids_max",
            str(self._config.pids_limit),
            "--cgroup_pids_parent",
            self._config.cgroup_parent,
            "--cgroup_cpu_ms_per_sec",
            str(self._config.cpu_ms_per_second),
            "--cgroup_cpu_parent",
            self._config.cgroup_parent,
            "--disable_proc",
        ]
        for source in (
            "/usr",
            "/usr/local",
            "/lib",
            "/lib64",
            "/opt/rhetorica-sandbox",
            "/opt/rhetorica-venv",
        ):
            if Path(source).exists():
                command.extend(("--bindmount_ro", f"{source}:{source}"))
        command.extend([
            "--bindmount_ro",
            f"{session.root / 'input'}:/workspace/input",
            "--bindmount",
            f"{session.root / 'output'}:/workspace/output",
            "--mount",
            f"none:/tmp:tmpfs:size={self._config.max_tmp_bytes},mode=1777",
            "--seccomp_string",
            "ERRNO(1) { mount, pivot_root, ptrace, process_vm_readv, process_vm_writev, bpf, perf_event_open, kexec_load, open_by_handle_at } DEFAULT ALLOW",
            "--",
        ])
        return command

    def _collect_outputs(self, session: _Session) -> list[dict[str, Any]]:
        output_root = session.root / "output"
        total_bytes = 0
        paths = sorted(output_root.iterdir(), key=lambda item: item.name)
        if len(paths) > self._config.max_output_files:
            raise SandboxRejectedError("too_many_output_files")

        pending: list[_PendingOutput] = []
        for path in paths:
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise SandboxRejectedError("invalid_output_entry")
            filename = self._safe_name(path.name)
            if PurePath(filename).suffix.lower() not in self._ALLOWED_OUTPUT_EXTENSIONS:
                raise SandboxRejectedError("unsupported_output_format")
            size_bytes = path.stat().st_size
            total_bytes += size_bytes
            if size_bytes > self._config.max_output_bytes:
                raise SandboxRejectedError("output_too_large")
            if total_bytes > self._config.max_session_output_bytes:
                raise SandboxRejectedError("session_output_too_large")
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
            raise SandboxRejectedError("session_output_too_large")
        if len(session.outputs) + len(pending) > self._config.max_session_output_files:
            raise SandboxRejectedError("too_many_session_output_files")
        if self._workspace_bytes(session.root) + pending_bytes > self._config.max_workspace_bytes:
            raise SandboxRejectedError("workspace_too_large")

        if not pending:
            return []

        batch_id = f"batch_{uuid4().hex}"
        snapshots_root = session.root / "snapshots"
        staged_root = snapshots_root / f".{batch_id}.pending"
        committed_root = snapshots_root / batch_id
        try:
            for item in pending:
                staged = staged_root / item.output_id / item.filename
                staged.parent.mkdir(parents=True, mode=0o700)
                shutil.copyfile(item.source, staged)
                os.chmod(staged, 0o400)
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
            raise SandboxRejectedError("invalid_filename")
        return normalized

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
