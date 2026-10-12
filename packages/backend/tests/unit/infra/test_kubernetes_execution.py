from __future__ import annotations

from typing import Any

import pytest
from rhetorica_backend.infra.execution.kubernetes import (
    ExecutionCapacityError,
    KubernetesJobCapacity,
)
from rhetorica_backend.infra.execution.kubernetes_file_sessions import (
    KubernetesFileSessionConfig,
    KubernetesFileSessionController,
)


class FakeJobApi:
    def __init__(self, *, active_jobs: int = 0) -> None:
        self.active_jobs = active_jobs
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.managed: dict[str | None, list[str]] = {}

    def create_job(self, manifest: dict[str, Any]) -> None:
        self.created.append(manifest)

    def delete_job(self, name: str) -> None:
        self.deleted.append(name)

    def list_job_pods(self, job_name: str) -> list[dict[str, Any]]:
        return []

    def count_active_jobs(self, *, instance: str) -> int:
        return self.active_jobs

    def list_managed_jobs(self, *, instance: str, workload: str | None = None) -> list[str]:
        return self.managed.get(workload, [])


def test_shared_capacity_rejects_job_at_deployment_limit() -> None:
    api = FakeJobApi(active_jobs=10)
    capacity = KubernetesJobCapacity(
        api=api,
        instance="rhetorica",
        max_active_jobs=10,
    )

    with pytest.raises(ExecutionCapacityError, match="execution_capacity_exhausted"):
        capacity.ensure_job(
            {"kind": "Job", "metadata": {"name": "rhetorica-file-1"}},
            workload="file",
        )

    assert api.created == []


def test_shared_capacity_admits_job_below_deployment_limit() -> None:
    api = FakeJobApi(active_jobs=9)
    capacity = KubernetesJobCapacity(
        api=api,
        instance="rhetorica",
        max_active_jobs=10,
    )
    manifest = {"kind": "Job", "metadata": {"name": "rhetorica-file-1"}}

    created = capacity.ensure_job(manifest, workload="file")

    assert created is True
    assert api.created == [manifest]


def test_shared_capacity_reuses_named_managed_job_without_charging_capacity() -> None:
    api = FakeJobApi(active_jobs=10)
    api.managed["coding"] = ["rhetorica-coding-lease-1"]
    capacity = KubernetesJobCapacity(
        api=api,
        instance="rhetorica",
        max_active_jobs=10,
    )
    manifest = {"metadata": {"name": "rhetorica-coding-lease-1"}}

    created = capacity.ensure_job(manifest, workload="coding")

    assert created is False
    assert api.created == []


def _file_controller(api: FakeJobApi) -> KubernetesFileSessionController:
    return KubernetesFileSessionController(
        api=api,
        capacity=KubernetesJobCapacity(
            api=api,
            instance="rhetorica",
            max_active_jobs=10,
        ),
        config=KubernetesFileSessionConfig(
            instance="rhetorica",
            runner_image="registry.example/execution:latest",
            runner_secret_name="rhetorica-secret",
            runtime_class_name="runsc",
        ),
        internal_token="secret",
    )


def test_file_job_is_ephemeral_gvisor_and_has_no_broad_secrets() -> None:
    manifest = _file_controller(FakeJobApi()).build_job_manifest(job_name="rhetorica-file-123")
    spec = manifest["spec"]
    pod_spec = spec["template"]["spec"]
    container = pod_spec["containers"][0]

    assert pod_spec["runtimeClassName"] == "runsc"
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["enableServiceLinks"] is False
    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert pod_spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert container["securityContext"] == {
        "privileged": False,
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert spec["backoffLimit"] == 0
    assert spec["activeDeadlineSeconds"] == 1260
    assert spec["ttlSecondsAfterFinished"] == 60
    assert manifest["metadata"]["labels"]["rhetorica.io/workload"] == "file"
    assert (
        manifest["metadata"]["labels"]["app.kubernetes.io/managed-by"]
        == "rhetorica-execution-controller"
    )
    env_names = {entry["name"] for entry in container["env"]}
    assert env_names.isdisjoint({
        "DATABASE_URL",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "AUTH_JWT_SECRET",
        "LLM_API_KEY",
    })


def test_file_reconcile_removes_orphan_jobs() -> None:
    api = FakeJobApi()
    api.managed["file"] = ["orphan-file-job"]

    result = _file_controller(api).reconcile()

    assert result == {"expired": 0, "removed_orphans": 1}
    assert api.deleted == ["orphan-file-job"]
