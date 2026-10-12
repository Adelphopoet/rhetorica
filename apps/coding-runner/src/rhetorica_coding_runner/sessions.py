"""Local development coding sessions, using the same bounded execution runtime."""

from __future__ import annotations

import shutil
import threading
from uuid import UUID

from rhetorica_coding_runner.runtime import ExecutionRuntime
from rhetorica_coding_runner.settings import CodingRunnerSettings


class CodingSessionRegistry:
    def __init__(self, settings: CodingRunnerSettings):
        self._settings = settings
        self._root = settings.workspace_root.parent / "coding-sessions"
        self._sessions: dict[str, ExecutionRuntime] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> ExecutionRuntime:
        if str(UUID(session_id)) != session_id:
            raise ValueError("coding_session_id_invalid")
        with self._lock:
            if session_id not in self._sessions:
                root = self._root / session_id
                self._sessions[session_id] = ExecutionRuntime(
                    self._settings.model_copy(
                        update={
                            "workspace_root": root / "repository",
                            "scratch_root": root / "scratch",
                        }
                    )
                )
            return self._sessions[session_id]

    def staged(self, session_id: str) -> bool:
        self.get(session_id)
        return (self._root / session_id / "repository" / ".git").is_dir()

    def close(self, session_id: str) -> None:
        self.get(session_id).cancel()
        with self._lock:
            self._sessions.pop(session_id, None)
            shutil.rmtree(self._root / session_id, ignore_errors=True)
