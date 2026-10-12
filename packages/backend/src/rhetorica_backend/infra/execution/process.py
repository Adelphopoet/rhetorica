"""Bounded subprocess primitive shared by coding and file execution flows."""

from __future__ import annotations

import os
import resource
import signal
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessLimits:
    max_log_bytes: int
    pid_limit: int
    memory_limit_bytes: int | None = None
    file_size_limit_bytes: int | None = None
    cpu_time_seconds: int | None = None


@dataclass(frozen=True)
class ProcessExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    oom_killed: bool
    truncated: bool


class ProcessExecutor:
    """Runs argv-only child processes with one reusable resource policy."""

    def __init__(self, limits: ProcessLimits) -> None:
        self._limits = limits

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
        stdin: bytes | None = None,
        on_started: Callable[[subprocess.Popen[bytes]], None] | None = None,
        on_finished: Callable[[subprocess.Popen[bytes]], None] | None = None,
    ) -> ProcessExecutionResult:
        timed_out = False
        captured = [bytearray(), bytearray()]
        truncated_streams = [False, False]
        process = subprocess.Popen(  # noqa: S603 - argv only; shell execution is disabled.
            list(argv),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
            preexec_fn=self._apply_limits,
        )
        stdout_stream = process.stdout
        stderr_stream = process.stderr
        if stdout_stream is None or stderr_stream is None:  # pragma: no cover - Popen contract
            raise RuntimeError("subprocess_capture_unavailable")

        def drain(index: int) -> None:
            stream = stdout_stream if index == 0 else stderr_stream
            while chunk := stream.read(64 * 1024):
                remaining = self._limits.max_log_bytes - len(captured[index])
                if remaining > 0:
                    captured[index].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated_streams[index] = True

        drain_threads = [
            threading.Thread(target=drain, args=(index,), daemon=True) for index in range(2)
        ]
        for thread in drain_threads:
            thread.start()
        if on_started is not None:
            try:
                on_started(process)
            except Exception:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
                for thread in drain_threads:
                    thread.join(timeout=5)
                raise
        try:
            if stdin is not None:
                stdin_stream = process.stdin
                if stdin_stream is None:  # pragma: no cover - Popen contract
                    raise RuntimeError("subprocess_stdin_unavailable")
                try:
                    stdin_stream.write(stdin)
                except BrokenPipeError:
                    pass
                finally:
                    stdin_stream.close()
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            for thread in drain_threads:
                thread.join(timeout=5)
        finally:
            if on_finished is not None:
                on_finished(process)
        stdout_text = captured[0].decode("utf-8", "replace")
        stderr_text = captured[1].decode("utf-8", "replace")
        if timed_out:
            stderr_text = f"{stderr_text}\ncommand_timeout".lstrip()
        exit_code = 124 if timed_out else int(process.returncode)
        return ProcessExecutionResult(
            exit_code=exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            timed_out=timed_out,
            oom_killed=process.returncode in {-9, 137} and not timed_out,
            truncated=any(truncated_streams),
        )

    def _apply_limits(self) -> None:
        resource.setrlimit(resource.RLIMIT_NPROC, (self._limits.pid_limit,) * 2)
        if self._limits.memory_limit_bytes is not None:
            resource.setrlimit(resource.RLIMIT_AS, (self._limits.memory_limit_bytes,) * 2)
        if self._limits.file_size_limit_bytes is not None:
            resource.setrlimit(resource.RLIMIT_FSIZE, (self._limits.file_size_limit_bytes,) * 2)
        if self._limits.cpu_time_seconds is not None:
            resource.setrlimit(resource.RLIMIT_CPU, (self._limits.cpu_time_seconds,) * 2)
