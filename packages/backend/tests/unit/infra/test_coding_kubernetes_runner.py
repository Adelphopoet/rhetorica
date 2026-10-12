from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from rhetorica_backend.contracts.coding import (
    EnvironmentProfile,
    ExecutionPrincipal,
    SandboxLeaseAcquisition,
    SandboxLeaseStatus,
    SandboxTaskLease,
)
from rhetorica_backend.infra.coding.kubernetes_runner import (
    CodingArtifactRelay,
    KubernetesCodingSandbox,
    SandboxLeaseRepository,
)
from rhetorica_backend.infra.execution.kubernetes import (
    KubernetesJobApi,
    KubernetesJobCapacity,
)


def _profile() -> EnvironmentProfile:
    return EnvironmentProfile(
        id="dbt-trino",
        revision=3,
        image_digest="registry.example/coding@sha256:" + "a" * 64,
        runtime_class_name="runsc",
        tool_versions={"python": "3.12.12", "dbt-core": "1.10.13", "dbt-trino": "1.9.3"},
        max_runtime_seconds=1200,
        cpu_limit="1",
        memory_limit="2Gi",
        disk_limit_bytes=1024 * 1024 * 1024,
        pid_limit=128,
        output_limit_bytes=50 * 1024 * 1024,
        software_manifest_digest="b" * 64,
        created_at=datetime.now(tz=UTC),
    )


def _lease() -> SandboxTaskLease:
    now = datetime.now(tz=UTC)
    return SandboxTaskLease(
        id="lease-1",
        workspace_id="analytics",
        owner_user_id="user-1",
        run_id="run-1",
        task_id="task-1",
        environment_profile_id="dbt-trino",
        environment_profile_revision=3,
        image_digest=_profile().image_digest,
        runtime_class_name="runsc",
        job_name="rhetorica-coding-lease1",
        status=SandboxLeaseStatus.CREATED,
        created_at=now,
        expires_at=now + timedelta(minutes=20),
        updated_at=now,
    )


def _sandbox() -> KubernetesCodingSandbox:
    api = cast(KubernetesJobApi, object())
    return KubernetesCodingSandbox(
        api=api,
        capacity=KubernetesJobCapacity(api=api, instance="rhetorica", max_active_jobs=10),
        leases=cast(SandboxLeaseRepository, object()),
        artifacts=cast(CodingArtifactRelay, object()),
        instance="rhetorica",
        controller_secret="secret",
    )


def test_coding_job_is_gvisor_non_root_and_has_no_service_account_token() -> None:
    manifest = _sandbox().build_job_manifest(
        lease=_lease(), profile=_profile(), task_token="task-token"
    )
    spec = manifest["spec"]["template"]["spec"]
    container = spec["containers"][0]

    assert spec["runtimeClassName"] == "runsc"
    assert spec["automountServiceAccountToken"] is False
    assert "serviceAccountName" not in spec
    assert spec["nodeSelector"] == {"rhetorica.io/coding-runtime": "gvisor"}
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert container["image"].endswith("a" * 64)
    assert container["securityContext"] == {
        "privileged": False,
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert container["resources"]["limits"] == {
        "cpu": "1",
        "memory": "2Gi",
        "ephemeral-storage": "1073741824",
    }
    assert {item["name"] for item in container["env"]}.isdisjoint({
        "DATABASE_URL",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "GITLAB_TOKEN",
    })
    assert manifest["spec"]["backoffLimit"] == 0
    assert manifest["spec"]["activeDeadlineSeconds"] == 1200
    assert manifest["spec"]["ttlSecondsAfterFinished"] == 60
    assert (
        manifest["metadata"]["labels"]["app.kubernetes.io/managed-by"]
        == "rhetorica-execution-controller"
    )
    assert manifest["metadata"]["labels"]["rhetorica.io/workload"] == "coding"
    assert manifest["metadata"]["labels"]["rhetorica.io/network-profile"] == "coding-runtime"


def test_coding_job_labels_lease_workspace_run_and_task_without_raw_ids() -> None:
    manifest = _sandbox().build_job_manifest(
        lease=_lease(), profile=_profile(), task_token="task-token"
    )
    labels = manifest["metadata"]["labels"]
    annotations = manifest["metadata"]["annotations"]

    assert labels["rhetorica.io/workspace"] != "analytics"
    assert labels["rhetorica.io/run"] != "run-1"
    assert labels["rhetorica.io/task"] != "task-1"
    assert annotations["rhetorica.io/workspace-id"] == "analytics"
    assert annotations["rhetorica.io/run-id"] == "run-1"


def test_create_retry_reuses_active_lease_and_job(monkeypatch) -> None:
    class FakeApi:
        def __init__(self) -> None:
            self.created: list[dict[str, Any]] = []

        def create_job(self, manifest: dict[str, Any]) -> None:
            self.created.append(manifest)

        def delete_job(self, name: str) -> None:
            pass

        def list_job_pods(self, job_name: str) -> list[dict[str, Any]]:
            return []

        def count_active_jobs(self, *, instance: str) -> int:
            return len(self.created)

        def list_managed_jobs(self, *, instance: str, workload: str | None = None) -> list[str]:
            return [str(item["metadata"]["name"]) for item in self.created]

    class FakeLeases:
        def __init__(self) -> None:
            self.lease: SandboxTaskLease | None = None

        def acquire_attempt(self, lease: SandboxTaskLease) -> SandboxLeaseAcquisition:
            if self.lease is None:
                self.lease = lease
                return SandboxLeaseAcquisition(lease=lease, created=True)
            return SandboxLeaseAcquisition(lease=self.lease, created=False)

    api = FakeApi()
    leases = FakeLeases()
    sandbox = KubernetesCodingSandbox(
        api=cast(KubernetesJobApi, api),
        capacity=KubernetesJobCapacity(
            api=cast(KubernetesJobApi, api),
            instance="rhetorica",
            max_active_jobs=1,
        ),
        leases=cast(SandboxLeaseRepository, leases),
        artifacts=cast(CodingArtifactRelay, object()),
        instance="rhetorica",
        controller_secret="secret",
    )
    monkeypatch.setattr(sandbox, "_wait_for_runner", lambda _lease: "10.0.0.1")
    principal = ExecutionPrincipal(
        actor_user_id="user-1",
        workspace_id="analytics",
        entry_source="web_chat",
        run_id="run-1",
        task_id="task-1",
    )

    first = sandbox.create(
        run_id="run-1",
        task_id="task-1",
        profile=_profile(),
        principal=principal,
    )
    retried = sandbox.create(
        run_id="run-1",
        task_id="task-1",
        profile=_profile(),
        principal=principal,
    )

    assert retried.id == first.id
    assert len(api.created) == 1
