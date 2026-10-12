"""Shared Kubernetes job admission for coding and file execution workloads."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx


class KubernetesExecutionError(RuntimeError):
    pass


class ExecutionCapacityError(RuntimeError):
    pass


class KubernetesJobApi(Protocol):
    def create_job(self, manifest: dict[str, Any]) -> None: ...
    def delete_job(self, name: str) -> None: ...
    def list_job_pods(self, job_name: str) -> list[dict[str, Any]]: ...
    def count_active_jobs(self, *, instance: str) -> int: ...
    def list_managed_jobs(self, *, instance: str, workload: str | None = None) -> list[str]: ...


class InClusterExecutionApi:
    def __init__(
        self,
        *,
        namespace: str,
        api_url: str = "https://kubernetes.default.svc",
        token_path: Path | None = None,
        ca_path: Path | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        service_account_root = Path("/var/run/secrets/kubernetes.io/serviceaccount")
        resolved_token_path = token_path or service_account_root / "token"
        resolved_ca_path = ca_path or service_account_root / "ca.crt"
        token = resolved_token_path.read_text(encoding="utf-8").strip()
        self._namespace = namespace
        self._client = httpx.Client(
            base_url=api_url,
            verify=str(resolved_ca_path),
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
            (
                f"/apis/batch/v1/namespaces/{quote(self._namespace, safe='')}/jobs/"
                f"{quote(name, safe='')}"
            ),
            params={"propagationPolicy": "Background", "gracePeriodSeconds": "0"},
        )
        if response.status_code not in {200, 202, 404}:
            raise KubernetesExecutionError(
                f"execution Job deletion failed ({response.status_code})"
            )

    def list_job_pods(self, job_name: str) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            f"/api/v1/namespaces/{quote(self._namespace, safe='')}/pods",
            params={"labelSelector": f"job-name={job_name}"},
        )
        payload = response.json()
        return [item for item in payload.get("items", []) if isinstance(item, dict)]

    def list_managed_jobs(self, *, instance: str, workload: str | None = None) -> list[str]:
        return [
            str(item["metadata"]["name"])
            for item in self._list_managed_job_items(instance=instance, workload=workload)
            if isinstance(item.get("metadata", {}).get("name"), str)
        ]

    def count_active_jobs(self, *, instance: str) -> int:
        return sum(
            not self._job_is_terminal(item)
            for item in self._list_managed_job_items(instance=instance)
        )

    def _list_managed_job_items(
        self, *, instance: str, workload: str | None = None
    ) -> list[dict[str, Any]]:
        selector = (
            "app.kubernetes.io/managed-by=rhetorica-execution-controller,"
            f"app.kubernetes.io/instance={instance}"
        )
        if workload is not None:
            selector += f",rhetorica.io/workload={workload}"
        response = self._request(
            "GET",
            f"/apis/batch/v1/namespaces/{quote(self._namespace, safe='')}/jobs",
            params={"labelSelector": selector},
        )
        payload = response.json()
        return [item for item in payload.get("items", []) if isinstance(item, dict)]

    @staticmethod
    def _job_is_terminal(job: dict[str, Any]) -> bool:
        status = job.get("status", {})
        if not isinstance(status, dict):
            return False
        conditions = status.get("conditions", [])
        return any(
            isinstance(condition, dict)
            and condition.get("type") in {"Complete", "Failed"}
            and condition.get("status") == "True"
            for condition in conditions
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise KubernetesExecutionError("Kubernetes API unavailable") from exc
        if response.is_error:
            raise KubernetesExecutionError(
                f"Kubernetes API rejected {method} {path} ({response.status_code})"
            )
        return response


class KubernetesJobCapacity:
    """Serializes admission and enforces one deployment-wide Job ceiling."""

    def __init__(self, *, api: KubernetesJobApi, instance: str, max_active_jobs: int) -> None:
        if max_active_jobs < 1:
            raise ValueError("max_active_jobs must be positive")
        self._api = api
        self._instance = instance
        self._max_active_jobs = max_active_jobs
        self._lock = threading.Lock()

    def ensure_job(self, manifest: dict[str, Any], *, workload: str) -> bool:
        """Create a named managed Job once without charging retries twice."""

        name = manifest.get("metadata", {}).get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("execution_job_name_required")
        with self._lock:
            existing = self._api.list_managed_jobs(
                instance=self._instance,
                workload=workload,
            )
            if name in existing:
                return False
            active = self._api.count_active_jobs(instance=self._instance)
            if active >= self._max_active_jobs:
                raise ExecutionCapacityError("execution_capacity_exhausted")
            self._api.create_job(manifest)
            return True
