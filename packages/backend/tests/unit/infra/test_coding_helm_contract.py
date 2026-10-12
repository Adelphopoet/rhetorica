from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[5]
CHART = ROOT / "deploy/helm/rhetorica"


def _coding_helm_command() -> list[str]:
    return [
        "helm",
        "template",
        "rhetorica",
        str(CHART),
        "--set",
        "codingRunner.enabled=true",
        "--set",
        "codingRunner.runner.imageDigest=registry.example/coding@sha256:" + "a" * 64,
        "--set",
        "secrets.codingControllerInternalToken=controller-token",
        "--set",
        "secrets.codingControllerSecret=controller-secret",
        "--set",
        "secrets.codingQueryGatewayInternalToken=gateway-token",
        "--set",
        "secrets.codingQueryGatewaySigningSecret=gateway-secret",
        "--set",
        "secrets.codingArtifactSigningSecret=artifact-secret",
    ]


def test_coding_helm_is_fail_closed_and_network_denied_by_default() -> None:
    controller = (
        ROOT / "deploy/helm/rhetorica/templates/coding-runner-controller.yaml"
    ).read_text()
    rbac = (ROOT / "deploy/helm/rhetorica/templates/coding-runner-rbac.yaml").read_text()
    network = (
        ROOT / "deploy/helm/rhetorica/templates/coding-runner-networkpolicy.yaml"
    ).read_text()
    gateway = (ROOT / "deploy/helm/rhetorica/templates/coding-query-gateway.yaml").read_text()

    assert "imageDigest must be immutable" in controller
    assert "runtimeClassName is required" in controller
    assert 'resources: ["jobs"]' in rbac
    assert 'verbs: ["create", "get", "list", "delete"]' in rbac
    assert 'resources: ["pods"]' in rbac
    assert "secrets" not in rbac
    assert "policyTypes: [Ingress, Egress]" in network
    assert "coding-brokers-only" in network
    assert "coding-dependencies-only" in network
    assert "coding-runtime" in network
    assert "coding-dependencies" in network
    assert "0.0.0.0/0" not in network
    assert "automountServiceAccountToken: false" in gateway
    assert "readOnlyRootFilesystem: true" in gateway
    assert "rhetorica_coding_runner.gateway:app" in gateway
    assert "CODING_QUERY_GATEWAY_PUBLIC_BASE_URL" in gateway
    assert "CODING_QUERY_GATEWAY_INTERNAL_TOKEN" in gateway
    assert "CODING_QUERY_GATEWAY_SIGNING_SECRET" in gateway
    assert "codingRunner.gateway.extraEnvFrom" in gateway
    assert "codingRunner.gateway.env" in gateway


@pytest.mark.skipif(not shutil.which("helm"), reason="Requires Helm rendering")
def test_coding_network_policies_allow_only_cluster_dns_resolution() -> None:
    rendered = subprocess.check_output(  # noqa: S603 - fixed local Helm inputs
        _coding_helm_command(),
        text=True,
    )
    resources = [item for item in yaml.safe_load_all(rendered) if item]
    policies = {
        item["metadata"]["name"]: item for item in resources if item["kind"] == "NetworkPolicy"
    }
    for suffix in ("coding-brokers-only", "coding-dependencies-only"):
        policy = next(value for key, value in policies.items() if key.endswith(suffix))
        dns_rules = [
            rule
            for rule in policy["spec"]["egress"]
            if rule.get("ports")
            == [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]
        ]
        assert len(dns_rules) == 1
        peer = dns_rules[0]["to"][0]
        assert peer["namespaceSelector"]["matchLabels"] == {
            "kubernetes.io/metadata.name": "kube-system"
        }
        assert peer["podSelector"]["matchLabels"] == {"k8s-app": "kube-dns"}


@pytest.mark.skipif(not shutil.which("helm"), reason="Requires Helm rendering")
def test_coding_gateway_rejects_multiple_replicas_without_shared_registry() -> None:
    result = subprocess.run(  # noqa: S603 - fixed local Helm inputs
        [*_coding_helm_command(), "--set", "codingRunner.gateway.replicaCount=2"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "replicaCount must be 1 until query handles use a shared store" in result.stderr


@pytest.mark.skipif(not shutil.which("helm"), reason="Requires Helm rendering")
def test_coding_gateway_receives_only_its_configured_secret_backend() -> None:
    rendered = subprocess.check_output(  # noqa: S603 - fixed local Helm inputs
        [
            *_coding_helm_command(),
            "--set",
            "codingRunner.gateway.env[0].name=RHETORICA_SECRET_STORE_BACKEND",
            "--set",
            "codingRunner.gateway.env[0].value=env",
            "--set",
            "codingRunner.gateway.extraEnvFrom[0].secretRef.name=rhetorica-connector-secrets",
        ],
        text=True,
    )
    resources = [item for item in yaml.safe_load_all(rendered) if item]
    gateway = next(
        item
        for item in resources
        if item["kind"] == "Deployment"
        and item["metadata"]["name"].endswith("-coding-query-gateway")
    )
    container = gateway["spec"]["template"]["spec"]["containers"][0]

    assert {"secretRef": {"name": "rhetorica-connector-secrets"}} in container["envFrom"]
    assert {"name": "RHETORICA_SECRET_STORE_BACKEND", "value": "env"} in container["env"]
    control_plane_keys = {
        item["valueFrom"]["secretKeyRef"]["key"] for item in container["env"] if "valueFrom" in item
    }
    assert control_plane_keys == {
        "CODING_QUERY_GATEWAY_INTERNAL_TOKEN",
        "CODING_QUERY_GATEWAY_SIGNING_SECRET",
    }
    assert [item["secretRef"]["name"] for item in container["envFrom"] if "secretRef" in item] == [
        "rhetorica-connector-secrets"
    ]
