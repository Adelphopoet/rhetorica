"""gVisor-only Kubernetes adapter for governed coding sandboxes."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Protocol
from uuid import uuid4

import httpx
from rhetorica_backend.contracts.coding import (
    CodingReasonCode,
    EnvironmentProfile,
    ExecutionPrincipal,
    SandboxArtifactManifest,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxLeaseAcquisition,
    SandboxLeaseStatus,
    SandboxPhase,
    SandboxTaskLease,
)
from rhetorica_backend.domain.coding.admission import CodingAdmissionError
from rhetorica_backend.infra.execution.kubernetes import (
    ExecutionCapacityError,
    KubernetesJobApi,
    KubernetesJobCapacity,
)


class KubernetesCodingError(RuntimeError):
    pass


class SandboxLeaseRepository(Protocol):
    def acquire_attempt(self, lease: SandboxTaskLease) -> SandboxLeaseAcquisition: ...
    def get_owned(
        self, *, lease_id: str, workspace_id: str, owner_user_id: str
    ) -> SandboxTaskLease | None: ...
    def transition(
        self,
        *,
        lease_id: str,
        workspace_id: str,
        owner_user_id: str,
        expected_version: int,
        expected_statuses: tuple[str, ...],
        status: str,
        checkpoint_ref: str | None = None,
        candidate_artifact_id: str | None = None,
    ) -> SandboxTaskLease | None: ...
    def list_reconcilable(self) -> list[SandboxTaskLease]: ...


class CodingArtifactRelay(Protocol):
    def download_owned(
        self,
        *,
        artifact_id: str,
        workspace_id: str,
        owner_user_id: str,
        run_id: str,
        task_id: str,
    ) -> tuple[bytes, str]: ...
    def persist(
        self,
        *,
        payload: bytes,
        digest: str,
        workspace_id: str,
        owner_user_id: str,
        run_id: str,
        task_id: str,
        kind: str,
    ) -> str: ...


class KubernetesCodingSandbox:
    _TRANSITION_SOURCES: ClassVar[dict[SandboxLeaseStatus, tuple[SandboxLeaseStatus, ...]]] = {
        SandboxLeaseStatus.STAGED: (SandboxLeaseStatus.CREATED,),
        SandboxLeaseStatus.RUNNING: (
            SandboxLeaseStatus.CREATED,
            SandboxLeaseStatus.STAGED,
            SandboxLeaseStatus.RUNNING,
            SandboxLeaseStatus.CHECKPOINTED,
        ),
        SandboxLeaseStatus.CHECKPOINTED: (
            SandboxLeaseStatus.STAGED,
            SandboxLeaseStatus.RUNNING,
            SandboxLeaseStatus.CHECKPOINTED,
        ),
        SandboxLeaseStatus.CANCEL_REQUESTED: (
            SandboxLeaseStatus.CREATED,
            SandboxLeaseStatus.STAGED,
            SandboxLeaseStatus.RUNNING,
            SandboxLeaseStatus.CHECKPOINTED,
            SandboxLeaseStatus.CANCEL_REQUESTED,
        ),
        SandboxLeaseStatus.CLOSED: (
            SandboxLeaseStatus.CREATED,
            SandboxLeaseStatus.STAGED,
            SandboxLeaseStatus.RUNNING,
            SandboxLeaseStatus.CHECKPOINTED,
            SandboxLeaseStatus.CANCEL_REQUESTED,
        ),
        SandboxLeaseStatus.EXPIRED: (
            SandboxLeaseStatus.CREATED,
            SandboxLeaseStatus.STAGED,
            SandboxLeaseStatus.RUNNING,
            SandboxLeaseStatus.CHECKPOINTED,
            SandboxLeaseStatus.CANCEL_REQUESTED,
        ),
        SandboxLeaseStatus.FAILED: (
            SandboxLeaseStatus.CREATED,
            SandboxLeaseStatus.STAGED,
            SandboxLeaseStatus.RUNNING,
            SandboxLeaseStatus.CHECKPOINTED,
            SandboxLeaseStatus.CANCEL_REQUESTED,
        ),
    }

    def __init__(
        self,
        *,
        api: KubernetesJobApi,
        capacity: KubernetesJobCapacity,
        leases: SandboxLeaseRepository,
        artifacts: CodingArtifactRelay,
        instance: str,
        controller_secret: str,
        node_selector: dict[str, str] | None = None,
        tolerations: list[dict[str, Any]] | None = None,
        image_pull_secrets: list[dict[str, str]] | None = None,
        startup_timeout_seconds: int = 120,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        if not controller_secret:
            raise ValueError("coding_controller_secret_required")
        self._api = api
        self._capacity = capacity
        self._leases = leases
        self._artifacts = artifacts
        self._instance = instance
        self._secret = controller_secret.encode()
        self._node_selector = node_selector or {"rhetorica.io/coding-runtime": "gvisor"}
        self._tolerations = tolerations or []
        self._image_pull_secrets = image_pull_secrets or []
        self._startup_timeout = startup_timeout_seconds
        self._client_factory = client_factory

    def create(
        self,
        *,
        run_id: str,
        task_id: str,
        profile: EnvironmentProfile,
        principal: ExecutionPrincipal,
        phase: SandboxPhase = SandboxPhase.RUNTIME,
    ) -> SandboxTaskLease:
        self._require_principal(principal=principal, run_id=run_id, task_id=task_id)
        lease_id = str(uuid4())
        job_name = self._job_name(lease_id)
        now = datetime.now(tz=UTC)
        lease = SandboxTaskLease(
            id=lease_id,
            workspace_id=principal.workspace_id,
            owner_user_id=principal.actor_user_id,
            run_id=run_id,
            task_id=task_id,
            environment_profile_id=profile.id,
            environment_profile_revision=profile.revision,
            image_digest=profile.image_digest,
            runtime_class_name=profile.runtime_class_name,
            tool_versions=profile.tool_versions,
            software_manifest_digest=profile.software_manifest_digest,
            phase=phase,
            job_name=job_name,
            status=SandboxLeaseStatus.CREATED,
            created_at=now,
            expires_at=now + timedelta(seconds=profile.max_runtime_seconds),
            updated_at=now,
        )
        acquisition = self._leases.acquire_attempt(lease)
        lease = acquisition.lease
        job_created = False
        try:
            if acquisition.created or lease.status == SandboxLeaseStatus.CREATED:
                job_created = self._capacity.ensure_job(
                    self.build_job_manifest(
                        lease=lease,
                        profile=profile,
                        task_token=self._task_token(lease.id),
                    ),
                    workload="coding",
                )
                self._wait_for_runner(lease)
        except ExecutionCapacityError:
            if acquisition.created or lease.status == SandboxLeaseStatus.CREATED:
                self._transition(lease, SandboxLeaseStatus.FAILED)
            raise
        except Exception as exc:
            if acquisition.created or job_created:
                with suppress(Exception):
                    self._api.delete_job(lease.job_name)
            if acquisition.created or job_created:
                self._transition(lease, SandboxLeaseStatus.FAILED)
            raise CodingAdmissionError(CodingReasonCode.SANDBOX_RUNTIME_UNAVAILABLE) from exc
        return lease

    def stage_snapshot(
        self, *, lease_id: str, snapshot_id: str, principal: ExecutionPrincipal
    ) -> None:
        lease = self._lease(lease_id, principal)
        payload, digest = self._artifacts.download_owned(
            artifact_id=snapshot_id,
            workspace_id=lease.workspace_id,
            owner_user_id=lease.owner_user_id,
            run_id=lease.run_id,
            task_id=lease.task_id,
        )
        with self._client(lease) as client:
            response = client.put(
                "/internal/v1/snapshot",
                content=payload,
                headers={"X-Rhetorica-Snapshot-Sha256": digest},
            )
        self._raise_runner(response)
        self._transition(lease, SandboxLeaseStatus.STAGED)

    def execute(
        self,
        *,
        lease_id: str,
        request: SandboxExecutionRequest,
        principal: ExecutionPrincipal,
    ) -> SandboxExecutionResult:
        lease = self._lease(lease_id, principal)
        remaining = int((lease.expires_at - datetime.now(tz=UTC)).total_seconds())
        if remaining <= 0:
            self.cancel(lease_id=lease_id, principal=principal)
            raise TimeoutError("sandbox_lease_expired")
        bounded = request.model_copy(
            update={"timeout_seconds": min(request.timeout_seconds, remaining)}
        )
        self._transition(lease, SandboxLeaseStatus.RUNNING)
        with self._client(lease) as client:
            response = client.post("/internal/v1/execute", json=bounded.model_dump(mode="json"))
        self._raise_runner(response)
        return SandboxExecutionResult.model_validate(response.json())

    def collect(self, *, lease_id: str, principal: ExecutionPrincipal) -> SandboxArtifactManifest:
        lease = self._lease(lease_id, principal)
        with self._client(lease) as client:
            response = client.get("/internal/v1/candidate")
        self._raise_runner(response)
        digest = response.headers.get("X-Rhetorica-Artifact-Sha256", "")
        if not hmac.compare_digest(hashlib.sha256(response.content).hexdigest(), digest):
            raise KubernetesCodingError("candidate_digest_mismatch")
        metadata = json.loads(response.headers.get("X-Rhetorica-Artifact-Manifest", "{}"))
        artifact_id = self._artifacts.persist(
            payload=response.content,
            digest=digest,
            workspace_id=lease.workspace_id,
            owner_user_id=lease.owner_user_id,
            run_id=lease.run_id,
            task_id=lease.task_id,
            kind="candidate",
        )
        self._transition(
            lease,
            SandboxLeaseStatus.CHECKPOINTED,
            candidate_artifact_id=artifact_id,
        )
        return SandboxArtifactManifest(
            artifact_id=artifact_id,
            workspace_id=lease.workspace_id,
            owner_user_id=lease.owner_user_id,
            run_id=lease.run_id,
            task_id=lease.task_id,
            digest=digest,
            size_bytes=int(metadata.get("size_bytes", len(response.content))),
            regular_paths=[str(item) for item in metadata.get("regular_paths", [])],
            created_at=datetime.now(tz=UTC),
        )

    def checkpoint(self, *, lease_id: str, principal: ExecutionPrincipal) -> str:
        lease = self._lease(lease_id, principal)
        checkpoint_ref = self._persist_checkpoint(lease)
        self._transition(lease, SandboxLeaseStatus.CHECKPOINTED, checkpoint_ref=checkpoint_ref)
        return checkpoint_ref

    def cancel(self, *, lease_id: str, principal: ExecutionPrincipal) -> None:
        lease = self._lease(lease_id, principal)
        if lease.status == SandboxLeaseStatus.CLOSED:
            self._api.delete_job(lease.job_name)
            return
        current = self._transition(lease, SandboxLeaseStatus.CANCEL_REQUESTED)
        with suppress(Exception):
            checkpoint_ref = self._persist_checkpoint(current)
            current = self._transition(
                current,
                SandboxLeaseStatus.CANCEL_REQUESTED,
                checkpoint_ref=checkpoint_ref,
            )
        with suppress(Exception), self._client(lease) as client:
            client.post("/internal/v1/cancel")
        self._close_transition(current, principal)
        self._api.delete_job(lease.job_name)

    def close(self, *, lease_id: str, principal: ExecutionPrincipal) -> None:
        lease = self._lease(lease_id, principal)
        if lease.status == SandboxLeaseStatus.CLOSED:
            self._api.delete_job(lease.job_name)
            return
        self._close_transition(lease, principal)
        self._api.delete_job(lease.job_name)

    def reconcile(self) -> dict[str, int]:
        now = datetime.now(tz=UTC)
        leases = {lease.job_name: lease for lease in self._leases.list_reconcilable()}
        managed_jobs = set(self._api.list_managed_jobs(instance=self._instance, workload="coding"))
        expired = 0
        removed = 0
        for name, lease in leases.items():
            if lease.expires_at <= now:
                self._api.delete_job(name)
                with suppress(KubernetesCodingError):
                    self._transition(lease, SandboxLeaseStatus.EXPIRED)
                expired += 1
        for name in managed_jobs - set(leases):
            self._api.delete_job(name)
            removed += 1
        return {"expired": expired, "removed_orphans": removed}

    def build_job_manifest(
        self, *, lease: SandboxTaskLease, profile: EnvironmentProfile, task_token: str
    ) -> dict[str, Any]:
        disk = str(profile.disk_limit_bytes)
        labels = {
            "app.kubernetes.io/name": "rhetorica-coding-runner",
            "app.kubernetes.io/instance": self._instance,
            "app.kubernetes.io/managed-by": "rhetorica-execution-controller",
            "rhetorica.io/workload": "coding",
            "rhetorica.io/network-profile": f"coding-{lease.phase}",
            "rhetorica.io/lease": self._label_digest(lease.id),
            "rhetorica.io/workspace": self._label_digest(lease.workspace_id),
            "rhetorica.io/run": self._label_digest(lease.run_id),
            "rhetorica.io/task": self._label_digest(lease.task_id),
        }
        pod_spec: dict[str, Any] = {
            "runtimeClassName": profile.runtime_class_name,
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "restartPolicy": "Never",
            "nodeSelector": self._node_selector,
            "tolerations": self._tolerations,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65532,
                "runAsGroup": 65532,
                "fsGroup": 65532,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "coding-runner",
                    "image": profile.image_digest,
                    "imagePullPolicy": "IfNotPresent",
                    "securityContext": {
                        "privileged": False,
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "ports": [{"name": "http", "containerPort": 8091}],
                    "env": [
                        {"name": "CODING_RUNNER_TASK_TOKEN", "value": task_token},
                        {"name": "CODING_RUNNER_PID_LIMIT", "value": str(profile.pid_limit)},
                        {"name": "CODING_RUNNER_MAX_SNAPSHOT_BYTES", "value": disk},
                        {
                            "name": "CODING_RUNNER_MAX_OUTPUT_BYTES",
                            "value": str(profile.output_limit_bytes),
                        },
                        {
                            "name": "CODING_RUNNER_DEPENDENCY_SOURCES",
                            "value": ",".join(profile.dependency_sources),
                        },
                        {"name": "CODING_RUNNER_PHASE", "value": lease.phase},
                    ],
                    "resources": {
                        "requests": {
                            "cpu": profile.cpu_limit,
                            "memory": profile.memory_limit,
                            "ephemeral-storage": disk,
                        },
                        "limits": {
                            "cpu": profile.cpu_limit,
                            "memory": profile.memory_limit,
                            "ephemeral-storage": disk,
                        },
                    },
                    "volumeMounts": [
                        {"name": "workspace", "mountPath": "/workspace"},
                        {"name": "scratch", "mountPath": "/scratch"},
                        {"name": "tmp", "mountPath": "/tmp"},  # noqa: S108
                    ],
                    "readinessProbe": {
                        "httpGet": {"path": "/healthz", "port": "http"},
                        "periodSeconds": 2,
                        "failureThreshold": 60,
                    },
                }
            ],
            "volumes": [
                {"name": "workspace", "emptyDir": {"sizeLimit": disk}},
                {"name": "scratch", "emptyDir": {"sizeLimit": disk}},
                {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "128Mi"}},
            ],
        }
        if self._image_pull_secrets:
            pod_spec["imagePullSecrets"] = self._image_pull_secrets
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": lease.job_name,
                "labels": labels,
                "annotations": {
                    "rhetorica.io/lease-id": lease.id,
                    "rhetorica.io/workspace-id": lease.workspace_id,
                    "rhetorica.io/run-id": lease.run_id,
                    "rhetorica.io/task-id": lease.task_id,
                    "rhetorica.io/image-digest": profile.image_digest,
                    "rhetorica.io/software-manifest-digest": profile.software_manifest_digest,
                },
            },
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": profile.max_runtime_seconds,
                "ttlSecondsAfterFinished": 60,
                "template": {"metadata": {"labels": labels}, "spec": pod_spec},
            },
        }

    def _wait_for_runner(self, lease: SandboxTaskLease) -> str:
        deadline = time.monotonic() + self._startup_timeout
        while time.monotonic() < deadline:
            for pod in self._api.list_job_pods(lease.job_name):
                status = pod.get("status", {})
                if status.get("phase") == "Failed":
                    raise KubernetesCodingError("coding runner failed during startup")
                pod_ip = status.get("podIP")
                if status.get("phase") == "Running" and isinstance(pod_ip, str):
                    try:
                        with self._client_factory(timeout=1.0) as client:
                            response = client.get(f"http://{pod_ip}:8091/healthz")
                        if response.status_code == 200:
                            health = response.json()
                            if lease.software_manifest_digest and not hmac.compare_digest(
                                str(health.get("software_manifest_digest", "")),
                                lease.software_manifest_digest,
                            ):
                                raise KubernetesCodingError("coding runner manifest mismatch")
                            if health.get("tool_versions") != lease.tool_versions:
                                raise KubernetesCodingError("coding runner tool versions mismatch")
                            return pod_ip
                    except httpx.RequestError:
                        pass
            time.sleep(0.5)
        raise KubernetesCodingError("coding runner startup timed out")

    def _client(self, lease: SandboxTaskLease) -> httpx.Client:
        pod_ip = self._pod_ip(lease)
        return self._client_factory(
            base_url=f"http://{pod_ip}:8091",
            timeout=httpx.Timeout(3700.0, connect=5.0),
            headers={"X-Rhetorica-Task-Token": self._task_token(lease.id)},
        )

    def _pod_ip(self, lease: SandboxTaskLease) -> str:
        for pod in self._api.list_job_pods(lease.job_name):
            status = pod.get("status", {})
            if status.get("phase") == "Running" and isinstance(status.get("podIP"), str):
                return str(status["podIP"])
        raise KubernetesCodingError("coding runner pod unavailable")

    def _lease(self, lease_id: str, principal: ExecutionPrincipal) -> SandboxTaskLease:
        lease = self._leases.get_owned(
            lease_id=lease_id,
            workspace_id=principal.workspace_id,
            owner_user_id=principal.actor_user_id,
        )
        if lease is None or lease.run_id != principal.run_id or lease.task_id != principal.task_id:
            raise PermissionError("sandbox_lease_scope_mismatch")
        return lease

    def _transition(
        self,
        lease: SandboxTaskLease,
        status: SandboxLeaseStatus,
        *,
        checkpoint_ref: str | None = None,
        candidate_artifact_id: str | None = None,
    ) -> SandboxTaskLease:
        expected_statuses = self._TRANSITION_SOURCES.get(status)
        if expected_statuses is None:
            raise KubernetesCodingError("sandbox_lease_transition_invalid")
        updated = self._leases.transition(
            lease_id=lease.id,
            workspace_id=lease.workspace_id,
            owner_user_id=lease.owner_user_id,
            expected_version=lease.version,
            expected_statuses=tuple(item.value for item in expected_statuses),
            status=status.value,
            checkpoint_ref=checkpoint_ref,
            candidate_artifact_id=candidate_artifact_id,
        )
        if updated is None:
            raise KubernetesCodingError("sandbox_lease_fenced")
        return updated

    def _persist_checkpoint(self, lease: SandboxTaskLease) -> str:
        with self._client(lease) as client:
            response = client.get("/internal/v1/candidate")
        self._raise_runner(response)
        digest = response.headers.get("X-Rhetorica-Artifact-Sha256", "")
        return self._artifacts.persist(
            payload=response.content,
            digest=digest,
            workspace_id=lease.workspace_id,
            owner_user_id=lease.owner_user_id,
            run_id=lease.run_id,
            task_id=lease.task_id,
            kind="checkpoint",
        )

    def _close_transition(
        self,
        lease: SandboxTaskLease,
        principal: ExecutionPrincipal,
    ) -> SandboxTaskLease:
        try:
            return self._transition(lease, SandboxLeaseStatus.CLOSED)
        except KubernetesCodingError:
            current = self._lease(lease.id, principal)
            if current.status != SandboxLeaseStatus.CLOSED:
                raise
            return current

    def _task_token(self, lease_id: str) -> str:
        return hmac.new(self._secret, lease_id.encode(), hashlib.sha256).hexdigest()

    def _job_name(self, lease_id: str) -> str:
        instance = re.sub(r"[^a-z0-9-]", "-", self._instance.lower()).strip("-")
        return f"{instance[:35]}-coding-{lease_id.replace('-', '')[:12]}"[:63].rstrip("-")

    @staticmethod
    def _label_digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:32]

    @staticmethod
    def _raise_runner(response: httpx.Response) -> None:
        if response.is_error:
            detail = "coding_runner_error"
            with suppress(ValueError):
                payload = response.json()
                if isinstance(payload, dict):
                    detail = str(payload.get("detail", detail))
            raise KubernetesCodingError(detail)

    @staticmethod
    def _require_principal(*, principal: ExecutionPrincipal, run_id: str, task_id: str) -> None:
        if principal.run_id != run_id or principal.task_id != task_id:
            raise PermissionError("sandbox_principal_scope_mismatch")
