"""One gVisor Kubernetes Job per file/Python session."""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from rhetorica_backend.infra.execution.kubernetes import (
    KubernetesJobApi,
    KubernetesJobCapacity,
)


class RunnerResponseError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class KubernetesFileSessionConfig:
    instance: str
    runner_image: str
    runner_secret_name: str
    runtime_class_name: str
    runner_image_pull_policy: str = "IfNotPresent"
    image_pull_secrets_json: str = "[]"
    node_selector_json: str = '{"rhetorica.io/coding-runtime":"gvisor"}'
    tolerations_json: str = "[]"
    startup_timeout_seconds: int = 60
    session_ttl_minutes: int = 20
    cleanup_interval_seconds: int = 60
    process_timeout_seconds: int = 60
    max_input_bytes: int = 25 * 1024 * 1024
    max_output_bytes: int = 50 * 1024 * 1024
    max_session_output_bytes: int = 100 * 1024 * 1024
    max_workspace_bytes: int = 512 * 1024 * 1024
    max_log_bytes: int = 64 * 1024
    max_output_files: int = 100
    max_session_output_files: int = 200
    process_memory_limit_bytes: int = 768 * 1024 * 1024
    pid_limit: int = 64
    workspace_size_limit: str = "1Gi"
    cpu_request: str = "400m"
    cpu_limit: str = "1"
    memory_request: str = "1Gi"
    memory_limit: str = "2304Mi"
    ephemeral_storage_request: str = "512Mi"
    ephemeral_storage_limit: str = "2Gi"

    @property
    def image_pull_secrets(self) -> list[dict[str, str]]:
        return self._named_objects(self.image_pull_secrets_json, "image pull secret")

    @property
    def node_selector(self) -> dict[str, str]:
        parsed = json.loads(self.node_selector_json)
        if not isinstance(parsed, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()
        ):
            raise TypeError("runner node selector must be a JSON object of strings")
        return parsed

    @property
    def tolerations(self) -> list[dict[str, Any]]:
        parsed = json.loads(self.tolerations_json)
        if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
            raise TypeError("runner tolerations must be a JSON list of objects")
        return parsed

    @staticmethod
    def _named_objects(value: str, label: str) -> list[dict[str, str]]:
        parsed = json.loads(value)
        if not isinstance(parsed, list):
            raise TypeError(f"runner {label}s must be a JSON list")
        result: list[dict[str, str]] = []
        for item in parsed:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise TypeError(f"runner {label} entries require a name")
            result.append({"name": item["name"]})
        return result


@dataclass
class _FileRunnerSession:
    id: str
    native_id: str
    job_name: str
    client: httpx.Client
    touched_monotonic: float
    lock: threading.Lock = field(default_factory=threading.Lock)


class KubernetesFileSessionController:
    """Controls ephemeral file runners without a second sandbox implementation."""

    def __init__(
        self,
        *,
        api: KubernetesJobApi,
        capacity: KubernetesJobCapacity,
        config: KubernetesFileSessionConfig,
        internal_token: str,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self._api = api
        self._capacity = capacity
        self._config = config
        self._internal_token = internal_token
        self._client_factory = client_factory
        self._sessions: dict[str, _FileRunnerSession] = {}
        self._sessions_lock = threading.Lock()
        self._creation_lock = threading.Lock()

    def create_session(self, session_id: str | None = None) -> str:
        resolved = str(UUID(session_id)) if session_id else str(uuid4())
        with self._creation_lock:
            with self._sessions_lock:
                if resolved in self._sessions:
                    return resolved
            job_name = self._job_name(resolved)
            client: httpx.Client | None = None
            try:
                self._capacity.ensure_job(
                    self.build_job_manifest(job_name=job_name),
                    workload="file",
                )
                pod_ip = self._wait_for_runner(job_name)
                client = self._client_factory(
                    base_url=f"http://{pod_ip}:8091",
                    timeout=httpx.Timeout(
                        self._config.process_timeout_seconds + 10,
                        connect=5.0,
                    ),
                    headers={"X-Rhetorica-Sandbox-Token": self._internal_token},
                )
                response = client.post("/internal/v1/files/sessions")
                self._raise_runner_error(response)
                native_id = str(response.json()["session_id"])
                session = _FileRunnerSession(
                    id=resolved,
                    native_id=native_id,
                    job_name=job_name,
                    client=client,
                    touched_monotonic=time.monotonic(),
                )
            except Exception:
                if client is not None:
                    client.close()
                with suppress(Exception):
                    self._api.delete_job(job_name)
                raise
            with self._sessions_lock:
                self._sessions[resolved] = session
            return resolved

    def execute(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._session(session_id)
        with session.lock:
            response = session.client.post(
                f"/internal/v1/files/sessions/{quote(session.native_id, safe='')}/execute",
                json=payload,
            )
            self._raise_runner_error(response)
            self._touch(session)
            result = response.json()
        return result if isinstance(result, dict) else {}

    def inspect(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._session(session_id)
        with session.lock:
            response = session.client.post(
                f"/internal/v1/files/sessions/{quote(session.native_id, safe='')}/inspect",
                json=payload,
            )
            self._raise_runner_error(response)
            self._touch(session)
            result = response.json()
        return result if isinstance(result, dict) else {}

    def open_output(
        self, *, session_id: str, output_id: str
    ) -> tuple[httpx.Response, Callable[[], None]]:
        session = self._session(session_id)
        session.lock.acquire()
        stream = session.client.stream(
            "GET",
            (
                f"/internal/v1/files/sessions/{quote(session.native_id, safe='')}/outputs/"
                f"{quote(output_id, safe='')}"
            ),
        )
        response = stream.__enter__()
        try:
            self._raise_runner_error(response)
            self._touch(session)
        except Exception:
            stream.__exit__(None, None, None)
            session.lock.release()
            raise

        def close_stream() -> None:
            try:
                stream.__exit__(None, None, None)
            finally:
                session.lock.release()

        return response, close_stream

    def close_session(self, session_id: str) -> None:
        with self._sessions_lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return
        with session.lock:
            try:
                try:
                    session.client.delete(
                        f"/internal/v1/files/sessions/{quote(session.native_id, safe='')}",
                        timeout=5.0,
                    )
                finally:
                    session.client.close()
            finally:
                self._api.delete_job(session.job_name)

    def reconcile(self) -> dict[str, int]:
        cutoff = time.monotonic() - self._config.session_ttl_minutes * 60
        with self._sessions_lock:
            expired = [
                session_id
                for session_id, session in self._sessions.items()
                if session.touched_monotonic < cutoff
            ]
        for session_id in expired:
            self.close_session(session_id)
        with self._sessions_lock:
            expected_jobs = {session.job_name for session in self._sessions.values()}
        managed_jobs = set(
            self._api.list_managed_jobs(
                instance=self._config.instance,
                workload="file",
            )
        )
        orphaned = managed_jobs - expected_jobs
        for job_name in orphaned:
            self._api.delete_job(job_name)
        return {"expired": len(expired), "removed_orphans": len(orphaned)}

    def build_job_manifest(self, *, job_name: str) -> dict[str, Any]:
        config = self._config
        labels = {
            "app.kubernetes.io/name": "rhetorica-execution-runner",
            "app.kubernetes.io/instance": config.instance,
            "app.kubernetes.io/managed-by": "rhetorica-execution-controller",
            "rhetorica.io/workload": "file",
            "rhetorica.io/network-profile": "file-runtime",
        }
        pod_spec: dict[str, Any] = {
            "runtimeClassName": config.runtime_class_name,
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "restartPolicy": "Never",
            "nodeSelector": config.node_selector,
            "tolerations": config.tolerations,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65532,
                "runAsGroup": 65532,
                "fsGroup": 65532,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "execution-runner",
                    "image": config.runner_image,
                    "imagePullPolicy": config.runner_image_pull_policy,
                    "securityContext": {
                        "privileged": False,
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "ports": [{"name": "http", "containerPort": 8091}],
                    "env": self._runner_env(),
                    "resources": {
                        "requests": {
                            "cpu": config.cpu_request,
                            "memory": config.memory_request,
                            "ephemeral-storage": config.ephemeral_storage_request,
                        },
                        "limits": {
                            "cpu": config.cpu_limit,
                            "memory": config.memory_limit,
                            "ephemeral-storage": config.ephemeral_storage_limit,
                        },
                    },
                    "volumeMounts": [
                        {"name": "workspace", "mountPath": "/workspace"},
                        {"name": "scratch", "mountPath": "/scratch"},
                        {"name": "tmp", "mountPath": "/tmp"},  # noqa: S108 - pod tmpfs
                    ],
                    "readinessProbe": {
                        "httpGet": {"path": "/healthz", "port": "http"},
                        "periodSeconds": 2,
                        "failureThreshold": 30,
                    },
                }
            ],
            "volumes": [
                {
                    "name": "workspace",
                    "emptyDir": {"sizeLimit": config.workspace_size_limit},
                },
                {
                    "name": "scratch",
                    "emptyDir": {"sizeLimit": config.workspace_size_limit},
                },
                {
                    "name": "tmp",
                    "emptyDir": {"medium": "Memory", "sizeLimit": "128Mi"},
                },
            ],
        }
        if config.image_pull_secrets:
            pod_spec["imagePullSecrets"] = config.image_pull_secrets
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": job_name, "labels": labels},
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": (
                    config.session_ttl_minutes * 60 + config.startup_timeout_seconds
                ),
                "ttlSecondsAfterFinished": 60,
                "template": {"metadata": {"labels": labels}, "spec": pod_spec},
            },
        }

    def _runner_env(self) -> list[dict[str, Any]]:
        config = self._config
        values = {
            "EXECUTION_RUNTIME_SESSIONS_ROOT": "/workspace/file-sessions",
            "EXECUTION_RUNTIME_PROCESS_TIMEOUT_SECONDS": str(config.process_timeout_seconds),
            "EXECUTION_RUNTIME_SESSION_TTL_MINUTES": str(config.session_ttl_minutes),
            "EXECUTION_RUNTIME_CLEANUP_INTERVAL_SECONDS": str(config.cleanup_interval_seconds),
            "EXECUTION_RUNTIME_MAX_INPUT_BYTES": str(config.max_input_bytes),
            "EXECUTION_RUNTIME_MAX_OUTPUT_BYTES": str(config.max_output_bytes),
            "EXECUTION_RUNTIME_MAX_SESSION_OUTPUT_BYTES": str(config.max_session_output_bytes),
            "EXECUTION_RUNTIME_MAX_WORKSPACE_BYTES": str(config.max_workspace_bytes),
            "EXECUTION_RUNTIME_MAX_LOG_BYTES": str(config.max_log_bytes),
            "EXECUTION_RUNTIME_MAX_OUTPUT_FILES": str(config.max_output_files),
            "EXECUTION_RUNTIME_MAX_SESSION_OUTPUT_FILES": str(config.max_session_output_files),
            "EXECUTION_RUNTIME_MEMORY_LIMIT_BYTES": str(config.process_memory_limit_bytes),
            "CODING_RUNNER_PID_LIMIT": str(config.pid_limit),
        }
        env: list[dict[str, Any]] = [{"name": key, "value": value} for key, value in values.items()]
        env.append({
            "name": "EXECUTION_RUNTIME_INTERNAL_TOKEN",
            "valueFrom": {
                "secretKeyRef": {
                    "name": config.runner_secret_name,
                    "key": "EXECUTION_RUNTIME_INTERNAL_TOKEN",
                }
            },
        })
        return env

    def _wait_for_runner(self, job_name: str) -> str:
        deadline = time.monotonic() + self._config.startup_timeout_seconds
        while time.monotonic() < deadline:
            for pod in self._api.list_job_pods(job_name):
                status = pod.get("status", {})
                if not isinstance(status, dict):
                    continue
                if status.get("phase") == "Failed":
                    raise RuntimeError("file runner Job failed during startup")
                pod_ip = status.get("podIP")
                if status.get("phase") == "Running" and isinstance(pod_ip, str):
                    try:
                        response = httpx.get(f"http://{pod_ip}:8091/healthz", timeout=1.0)
                        if response.status_code == 200:
                            return pod_ip
                    except httpx.RequestError:
                        pass
            time.sleep(0.5)
        raise RuntimeError("file runner Job did not become ready in time")

    def _session(self, session_id: str) -> _FileRunnerSession:
        with self._sessions_lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def _job_name(self, session_id: str) -> str:
        instance = re.sub(r"[^a-z0-9-]", "-", self._config.instance.lower()).strip("-")
        prefix = f"{instance}-file"[:45].rstrip("-")
        return f"{prefix}-{session_id.replace('-', '')[:12]}"

    @staticmethod
    def _touch(session: _FileRunnerSession) -> None:
        session.touched_monotonic = time.monotonic()

    @staticmethod
    def _raise_runner_error(response: httpx.Response) -> None:
        if not response.is_error:
            return
        detail = "runner_unavailable"
        try:
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
                detail = payload["detail"]
        except ValueError:
            pass
        raise RunnerResponseError(response.status_code, detail)
