from __future__ import annotations

import json
import re
import threading
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

_TMP_MOUNT_PATH = "/tmp"  # noqa: S108 - isolated per-runner tmpfs mount


class KubernetesApiError(RuntimeError):
    pass


class SandboxCapacityError(RuntimeError):
    pass


class RunnerResponseError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class KubernetesJobConfig:
    namespace: str
    instance: str
    runner_image: str
    runner_image_pull_policy: str
    runner_secret_name: str
    image_pull_secrets_json: str = "[]"
    max_sessions: int = 10
    startup_timeout_seconds: int = 60
    session_ttl_minutes: int = 20
    process_timeout_seconds: int = 60
    max_input_bytes: int = 25 * 1024 * 1024
    max_output_bytes: int = 50 * 1024 * 1024
    max_session_output_bytes: int = 100 * 1024 * 1024
    max_workspace_bytes: int = 512 * 1024 * 1024
    max_tmp_bytes: int = 128 * 1024 * 1024
    max_log_bytes: int = 64 * 1024
    memory_limit_bytes: int = 768 * 1024 * 1024
    pids_limit: int = 64
    cpu_ms_per_second: int = 400
    workspace_size_limit: str = "768Mi"
    cpu_request: str = "400m"
    cpu_limit: str = "425m"
    memory_request: str = "1Gi"
    memory_limit: str = "1152Mi"
    ephemeral_storage_request: str = "512Mi"
    ephemeral_storage_limit: str = "1Gi"

    @property
    def image_pull_secrets(self) -> list[dict[str, str]]:
        parsed = json.loads(self.image_pull_secrets_json)
        if not isinstance(parsed, list):
            raise TypeError("runner image pull secrets must be a JSON list")
        result: list[dict[str, str]] = []
        for item in parsed:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise TypeError("runner image pull secret entries require a name")
            result.append({"name": item["name"]})
        return result


class InClusterKubernetesApi:
    def __init__(
        self,
        *,
        namespace: str,
        api_url: str = "https://kubernetes.default.svc",
        token_path: Path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token"),
        ca_path: Path = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"),
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        token = token_path.read_text(encoding="utf-8").strip()
        self._namespace = namespace
        self._client = httpx.Client(
            base_url=api_url,
            verify=str(ca_path),
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(10.0, connect=5.0),
            transport=transport,
        )

    def create_job(self, manifest: dict[str, Any]) -> None:
        self._request(
            "POST",
            f"/apis/batch/v1/namespaces/{quote(self._namespace, safe='')}/jobs",
            json=manifest,
        )

    def delete_job(self, name: str) -> None:
        response = self._client.request(
            "DELETE",
            f"/apis/batch/v1/namespaces/{quote(self._namespace, safe='')}/jobs/{quote(name, safe='')}",
            params={"propagationPolicy": "Background", "gracePeriodSeconds": "0"},
        )
        if response.status_code not in {200, 202, 404}:
            raise KubernetesApiError(
                f"Kubernetes API rejected Job deletion ({response.status_code})"
            )

    def list_job_pods(self, job_name: str) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            f"/api/v1/namespaces/{quote(self._namespace, safe='')}/pods",
            params={"labelSelector": f"job-name={job_name}"},
        )
        payload = response.json()
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [item for item in items if isinstance(item, dict)]

    def list_managed_jobs(self, *, instance: str) -> list[str]:
        response = self._request(
            "GET",
            f"/apis/batch/v1/namespaces/{quote(self._namespace, safe='')}/jobs",
            params={
                "labelSelector": (
                    "app.kubernetes.io/managed-by=rhetorica-sandbox-controller,"
                    f"app.kubernetes.io/instance={instance}"
                )
            },
        )
        payload = response.json()
        items = payload.get("items", []) if isinstance(payload, dict) else []
        names: list[str] = []
        for item in items:
            metadata = item.get("metadata", {}) if isinstance(item, dict) else {}
            name = metadata.get("name") if isinstance(metadata, dict) else None
            if isinstance(name, str):
                names.append(name)
        return names

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise KubernetesApiError("Kubernetes API is unavailable") from exc
        if response.is_error:
            raise KubernetesApiError(
                f"Kubernetes API rejected {method} {path} ({response.status_code})"
            )
        return response


@dataclass
class _RunnerSession:
    id: str
    native_id: str
    job_name: str
    client: httpx.Client
    created_monotonic: float


class KubernetesJobSandboxController:
    """Small control plane that creates one lean runner Job per active turn."""

    def __init__(
        self,
        *,
        api: InClusterKubernetesApi,
        config: KubernetesJobConfig,
        internal_token: str,
    ) -> None:
        self._api = api
        self._config = config
        self._internal_token = internal_token
        self._sessions: dict[str, _RunnerSession] = {}
        self._sessions_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(config.max_sessions)

    def cleanup_orphan_jobs(self) -> None:
        for job_name in self._api.list_managed_jobs(instance=self._config.instance):
            self._api.delete_job(job_name)

    def create_session(self, session_id: str | None = None) -> str:
        if session_id:
            from uuid import UUID

            session_id = str(UUID(session_id))
            with self._sessions_lock:
                if session_id in self._sessions:
                    return session_id
        if not self._slots.acquire(blocking=False):
            raise SandboxCapacityError("sandbox_capacity_exhausted")
        session_id = session_id or str(uuid4())
        job_name = self._job_name(session_id)
        client: httpx.Client | None = None
        try:
            self._api.create_job(self.build_job_manifest(job_name=job_name))
            pod_ip = self._wait_for_runner(job_name)
            client = httpx.Client(
                base_url=f"http://{pod_ip}:8090",
                timeout=httpx.Timeout(self._config.process_timeout_seconds + 10, connect=5.0),
                headers={"X-Rhetorica-Sandbox-Token": self._internal_token},
            )
            response = client.post("/internal/v1/sessions")
            self._raise_runner_error(response)
            native_id = str(response.json()["session_id"])
            session = _RunnerSession(
                id=session_id,
                native_id=native_id,
                job_name=job_name,
                client=client,
                created_monotonic=time.monotonic(),
            )
        except Exception:
            if client is not None:
                client.close()
            try:
                self._api.delete_job(job_name)
            finally:
                self._slots.release()
            raise
        else:
            with self._sessions_lock:
                self._sessions[session_id] = session
            return session_id

    def execute(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._session(session_id)
        response = session.client.post(
            f"/internal/v1/sessions/{quote(session.native_id, safe='')}/execute",
            json=payload,
        )
        self._raise_runner_error(response)
        result = response.json()
        return result if isinstance(result, dict) else {}

    def inspect(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._session(session_id)
        response = session.client.post(
            f"/internal/v1/sessions/{quote(session.native_id, safe='')}/inspect",
            json=payload,
        )
        self._raise_runner_error(response)
        result = response.json()
        return result if isinstance(result, dict) else {}

    def open_output(
        self,
        *,
        session_id: str,
        output_id: str,
    ) -> tuple[httpx.Response, AbstractContextManager[httpx.Response]]:
        session = self._session(session_id)
        stream = session.client.stream(
            "GET",
            (
                f"/internal/v1/sessions/{quote(session.native_id, safe='')}"
                f"/outputs/{quote(output_id, safe='')}"
            ),
        )
        response = stream.__enter__()
        try:
            self._raise_runner_error(response)
        except Exception:
            stream.__exit__(None, None, None)
            raise
        return response, stream

    def close_session(self, session_id: str) -> None:
        with self._sessions_lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return
        try:
            try:
                session.client.delete(
                    f"/internal/v1/sessions/{quote(session.native_id, safe='')}",
                    timeout=5.0,
                )
            finally:
                session.client.close()
        finally:
            try:
                self._api.delete_job(session.job_name)
            finally:
                self._slots.release()

    def cleanup_expired(self) -> int:
        deadline = time.monotonic() - self._config.session_ttl_minutes * 60
        with self._sessions_lock:
            expired = [
                session_id
                for session_id, session in self._sessions.items()
                if session.created_monotonic < deadline
            ]
        for session_id in expired:
            self.close_session(session_id)
        return len(expired)

    def build_job_manifest(self, *, job_name: str) -> dict[str, Any]:
        config = self._config
        pod_spec: dict[str, Any] = {
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "sandbox-runner",
                    "image": config.runner_image,
                    "imagePullPolicy": config.runner_image_pull_policy,
                    "securityContext": {
                        "privileged": True,
                        "readOnlyRootFilesystem": True,
                        "allowPrivilegeEscalation": True,
                        "runAsUser": 0,
                    },
                    "ports": [{"name": "http", "containerPort": 8090}],
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
                        {
                            "name": "sessions",
                            "mountPath": "/var/lib/rhetorica-sandbox/sessions",
                        },
                        {"name": "tmp", "mountPath": _TMP_MOUNT_PATH},
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
                    "name": "sessions",
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
        labels = {
            "app.kubernetes.io/name": "rhetorica-sandbox-runner",
            "app.kubernetes.io/instance": config.instance,
            "app.kubernetes.io/managed-by": "rhetorica-sandbox-controller",
        }
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": job_name, "labels": labels},
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": config.session_ttl_minutes * 60 + 120,
                "ttlSecondsAfterFinished": 60,
                "template": {
                    "metadata": {"labels": labels},
                    "spec": pod_spec,
                },
            },
        }

    def _runner_env(self) -> list[dict[str, Any]]:
        config = self._config
        values = {
            "SANDBOX_SESSIONS_ROOT": "/var/lib/rhetorica-sandbox/sessions",
            "SANDBOX_PYTHON_PATH": "/opt/rhetorica-venv/bin/python",
            "SANDBOX_MAX_PARALLEL_RUNS": "1",
            "SANDBOX_PROCESS_TIMEOUT_SECONDS": str(config.process_timeout_seconds),
            "SANDBOX_SESSION_TTL_MINUTES": str(config.session_ttl_minutes),
            "SANDBOX_CLEANUP_INTERVAL_SECONDS": "60",
            "SANDBOX_MAX_INPUT_BYTES": str(config.max_input_bytes),
            "SANDBOX_MAX_OUTPUT_BYTES": str(config.max_output_bytes),
            "SANDBOX_MAX_SESSION_OUTPUT_BYTES": str(config.max_session_output_bytes),
            "SANDBOX_MAX_WORKSPACE_BYTES": str(config.max_workspace_bytes),
            "SANDBOX_MAX_TMP_BYTES": str(config.max_tmp_bytes),
            "SANDBOX_MAX_LOG_BYTES": str(config.max_log_bytes),
            "SANDBOX_MAX_OUTPUT_FILES": "100",
            "SANDBOX_MAX_SESSION_OUTPUT_FILES": "200",
            "SANDBOX_MEMORY_LIMIT_BYTES": str(config.memory_limit_bytes),
            "SANDBOX_PIDS_LIMIT": str(config.pids_limit),
            "SANDBOX_CPU_MS_PER_SECOND": str(config.cpu_ms_per_second),
            "SANDBOX_MANAGE_LEGACY_CGROUP_PARENTS": "true",
        }
        env: list[dict[str, Any]] = [{"name": key, "value": value} for key, value in values.items()]
        env.extend([
            {
                "name": "SANDBOX_INTERNAL_TOKEN",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": config.runner_secret_name,
                        "key": "SANDBOX_INTERNAL_TOKEN",
                    }
                },
            },
            {
                "name": "POD_UID",
                "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}},
            },
        ])
        return env

    def _wait_for_runner(self, job_name: str) -> str:
        deadline = time.monotonic() + self._config.startup_timeout_seconds
        while time.monotonic() < deadline:
            for pod in self._api.list_job_pods(job_name):
                status = pod.get("status", {})
                if not isinstance(status, dict):
                    continue
                if status.get("phase") == "Failed":
                    raise KubernetesApiError("Sandbox runner Job failed during startup")
                pod_ip = status.get("podIP")
                if status.get("phase") == "Running" and isinstance(pod_ip, str):
                    try:
                        response = httpx.get(f"http://{pod_ip}:8090/healthz", timeout=1.0)
                        if response.status_code == 200:
                            return pod_ip
                    except httpx.RequestError:
                        pass
            time.sleep(0.5)
        raise KubernetesApiError("Sandbox runner Job did not become ready in time")

    def _session(self, session_id: str) -> _RunnerSession:
        with self._sessions_lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def _job_name(self, session_id: str) -> str:
        instance = re.sub(r"[^a-z0-9-]", "-", self._config.instance.lower()).strip("-")
        prefix = f"{instance}-sbx"[:45].rstrip("-")
        return f"{prefix}-{session_id.replace('-', '')[:12]}"

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
