"""Explicit local Docker adapter; production never falls back to this boundary."""

import hashlib
from typing import Protocol

import httpx
from rhetorica_backend.contracts.coding import (
    EnvironmentProfile,
    SandboxExecutionRequest,
    SandboxExecutionResult,
)


class CodingRunnerPort(Protocol):
    def stage(self, task_id: str, payload: bytes) -> bool: ...
    def execute(self, task_id: str, request: SandboxExecutionRequest) -> SandboxExecutionResult: ...
    def prepare_environment(
        self, task_id: str, profile: EnvironmentProfile, timeout: int
    ) -> dict: ...
    def collect(self, task_id: str) -> bytes: ...
    def close(self, task_id: str) -> None: ...


class LocalCodingRunner:
    def __init__(self, *, base_url: str, token: str):
        self._url = base_url.rstrip("/")
        self._headers = {"X-Rhetorica-Task-Token": token}

    def _path(self, task_id, suffix=""):
        from uuid import UUID

        if str(UUID(task_id)) != task_id:
            raise ValueError("coding_session_id_invalid")
        return f"{self._url}/internal/v1/coding/sessions/{task_id}{suffix}"

    def stage(self, task_id: str, payload: bytes) -> bool:
        """Return whether an existing sandbox, including edits, was retained."""
        response = httpx.put(
            self._path(task_id, "/snapshot"),
            content=payload,
            headers={
                **self._headers,
                "X-Rhetorica-Snapshot-Sha256": hashlib.sha256(payload).hexdigest(),
            },
            timeout=60,
        )
        response.raise_for_status()
        return response.json().get("restored", False) is True

    def prepare_environment(self, task_id: str, profile: EnvironmentProfile, timeout: int) -> dict:
        response = httpx.put(
            self._path(task_id, "/environment"),
            json={
                "profile_id": profile.id,
                "profile_revision": profile.revision,
                "pip_dependencies": profile.pip_dependencies,
                "dependency_sources": profile.dependency_sources,
                "timeout_seconds": timeout,
            },
            headers=self._headers,
            timeout=timeout + 10,
        )
        response.raise_for_status()
        return response.json()

    def execute(self, task_id: str, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        response = httpx.post(
            self._path(task_id, "/execute"),
            json=request.model_dump(mode="json"),
            headers=self._headers,
            timeout=request.timeout_seconds + 10,
        )
        response.raise_for_status()
        return SandboxExecutionResult.model_validate(response.json())

    def collect(self, task_id: str) -> bytes:
        response = httpx.get(self._path(task_id, "/candidate"), headers=self._headers, timeout=60)
        response.raise_for_status()
        if (
            hashlib.sha256(response.content).hexdigest()
            != response.headers["X-Rhetorica-Artifact-Sha256"]
        ):
            raise ValueError("coding_candidate_digest_mismatch")
        return response.content

    def close(self, task_id: str):
        response = httpx.delete(self._path(task_id), headers=self._headers, timeout=30)
        response.raise_for_status()
