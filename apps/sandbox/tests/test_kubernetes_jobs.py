from __future__ import annotations

from typing import cast

from rhetorica_sandbox.kubernetes_jobs import (
    InClusterKubernetesApi,
    KubernetesJobConfig,
    KubernetesJobSandboxController,
)


def test_runner_job_is_lean_ephemeral_and_receives_no_application_secrets() -> None:
    controller = KubernetesJobSandboxController(
        api=cast(InClusterKubernetesApi, object()),
        config=KubernetesJobConfig(
            namespace="rhetorica",
            instance="rhetorica",
            runner_image="registry.example/sandbox:sha",
            runner_image_pull_policy="IfNotPresent",
            runner_secret_name="rhetorica-secret",
        ),
        internal_token="test-token",
    )

    manifest = controller.build_job_manifest(job_name="rhetorica-sbx-123")
    spec = manifest["spec"]
    pod_spec = spec["template"]["spec"]
    runner = pod_spec["containers"][0]

    assert spec["backoffLimit"] == 0
    assert spec["ttlSecondsAfterFinished"] == 60
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["restartPolicy"] == "Never"
    assert "serviceAccountName" not in pod_spec
    assert "envFrom" not in runner
    env_by_name = {item["name"]: item for item in runner["env"]}
    assert env_by_name["SANDBOX_INTERNAL_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "rhetorica-secret",
        "key": "SANDBOX_INTERNAL_TOKEN",
    }
    assert env_by_name["POD_UID"]["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.uid"
    for forbidden in (
        "DATABASE_URL",
        "LLM_API_KEY",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "AUTH_JWT_SECRET",
    ):
        assert forbidden not in env_by_name

    assert runner["resources"]["requests"] == {
        "cpu": "400m",
        "memory": "1Gi",
        "ephemeral-storage": "512Mi",
    }
    assert runner["resources"]["limits"] == {
        "cpu": "425m",
        "memory": "1152Mi",
        "ephemeral-storage": "1Gi",
    }
    sessions = next(item for item in pod_spec["volumes"] if item["name"] == "sessions")
    assert sessions["emptyDir"] == {"sizeLimit": "768Mi"}
