from __future__ import annotations

from typing import Any

import httpx
import pytest
from rhetorica_backend.contracts.admin_integrations import KubernetesConnectionConfig
from rhetorica_backend.domain.chat.kubernetes import KubernetesRequestError, KubernetesService


def _connection() -> KubernetesConnectionConfig:
    return KubernetesConnectionConfig(
        provider="kubernetes",
        base_url="https://k8s.example.com/",
        bearer_token="secret-token",
        use_when="Use for read-only Kubernetes diagnostics.",
        verify_tls=False,
        namespace_allowlist=["prod"],
        default_limit=5,
        max_limit=10,
        log_max_chars=1000,
        timeout_seconds=7,
    )


def _service(handler) -> KubernetesService:
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs: Any) -> httpx.Client:
        return httpx.Client(transport=transport, **kwargs)

    return KubernetesService(client_factory=client_factory)


def test_kubernetes_service_uses_get_only_and_masks_secret_fields() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer secret-token"
        assert request.url.path == "/api/v1/namespaces/prod/pods"
        assert request.url.params["limit"] == "2"
        return httpx.Response(
            200,
            json={
                "metadata": {"continue": "next"},
                "items": [
                    {
                        "metadata": {
                            "name": "api-1",
                            "namespace": "prod",
                            "labels": {"app": "api"},
                        },
                        "spec": {
                            "nodeName": "node-1",
                            "imagePullSecrets": [{"name": "pull-secret"}],
                            "volumes": [{"secret": {"secretName": "app-secret"}}],
                            "containers": [
                                {
                                    "name": "api",
                                    "image": "repo/api:1",
                                    "env": [{"name": "TOKEN", "value": "leak"}],
                                }
                            ],
                        },
                        "status": {
                            "phase": "Running",
                            "podIP": "10.0.0.1",
                            "containerStatuses": [{"name": "api", "restartCount": 3}],
                        },
                    }
                ],
            },
        )

    payload = _service(handler).list_pods(
        _connection(),
        namespace="prod",
        limit=2,
        label_selector="app=api",
    )

    assert {request.method for request in calls} == {"GET"}
    assert payload["truncated"] is True
    assert payload["items"][0]["restart_count"] == 3
    serialized = str(payload)
    assert "secret" not in serialized.lower()
    assert "TOKEN" not in serialized
    assert "leak" not in serialized


def test_kubernetes_service_reads_namespace_status_and_empty_lists() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v1/namespaces/prod":
            return httpx.Response(
                200,
                json={
                    "metadata": {"name": "prod", "creationTimestamp": "2026-06-05T00:00:00Z"},
                    "status": {"phase": "Active"},
                },
            )
        assert request.url.path == "/apis/apps/v1/namespaces/prod/statefulsets"
        return httpx.Response(200, json={"metadata": {"resourceVersion": "42"}, "items": []})

    service = _service(handler)

    namespace = service.get_namespace_status(_connection(), namespace="prod")
    statefulsets = service.list_statefulsets(_connection(), namespace="prod", limit=5)

    assert calls == ["/api/v1/namespaces/prod", "/apis/apps/v1/namespaces/prod/statefulsets"]
    assert namespace["phase"] == "Active"
    assert statefulsets["empty"] is True
    assert statefulsets["resource_version"] == "42"


def test_kubernetes_service_lists_services_and_endpoints_without_secrets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/namespaces/prod/services":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "metadata": {"name": "catalog-api", "namespace": "prod"},
                            "spec": {
                                "type": "ClusterIP",
                                "clusterIP": "10.0.0.10",
                                "selector": {"app": "catalog-api"},
                                "ports": [{"name": "http", "port": 8585, "targetPort": "http"}],
                            },
                        }
                    ]
                },
            )
        assert request.url.path == "/api/v1/namespaces/prod/endpoints"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "metadata": {
                            "name": "catalog-api",
                            "namespace": "prod",
                            "annotations": {"secret-token": "leak"},
                        },
                        "subsets": [
                            {
                                "addresses": [
                                    {
                                        "ip": "10.1.1.1",
                                        "targetRef": {
                                            "kind": "Pod",
                                            "namespace": "prod",
                                            "name": "catalog-api-1",
                                        },
                                    }
                                ],
                                "notReadyAddresses": [
                                    {
                                        "ip": "10.1.1.2",
                                        "targetRef": {
                                            "kind": "Pod",
                                            "namespace": "prod",
                                            "name": "catalog-api-2",
                                        },
                                    }
                                ],
                                "ports": [{"name": "http", "port": 8585}],
                            }
                        ],
                    }
                ]
            },
        )

    service = _service(handler)

    services = service.list_services(_connection(), namespace="prod", limit=5)
    endpoints = service.list_endpoints(_connection(), namespace="prod", limit=5)

    assert services["items"][0]["ports"][0]["port"] == 8585
    assert endpoints["items"][0]["ready_addresses"] == 1
    assert endpoints["items"][0]["not_ready_addresses"] == 1
    assert endpoints["items"][0]["target_refs"][0]["name"] == "catalog-api-1"
    serialized = str({"services": services, "endpoints": endpoints})
    assert "secret-token" not in serialized
    assert "leak" not in serialized


def test_kubernetes_pod_logs_are_chunked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/namespaces/prod/pods/api-1/log"
        assert request.url.params["tailLines"] == "50"
        return httpx.Response(200, text="x" * 2500)

    payload = _service(handler).read_pod_logs(
        _connection(),
        namespace="prod",
        pod_name="api-1",
        tail_lines=50,
        max_chars=1000,
        offset=1000,
    )

    assert payload["content"] == "x" * 1000
    assert payload["offset"] == 1000
    assert payload["next_offset"] == 2000
    assert payload["truncated"] is True


def test_kubernetes_service_raises_structured_http_errors() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    with pytest.raises(KubernetesRequestError) as exc_info:
        _service(handler).get_pod(_connection(), namespace="prod", pod_name="api-1")

    payload = exc_info.value.to_payload()
    assert payload["status_code"] == 403
    assert payload["request_path"] == "/api/v1/namespaces/prod/pods/api-1"
    assert payload["response_body"] == "forbidden"
