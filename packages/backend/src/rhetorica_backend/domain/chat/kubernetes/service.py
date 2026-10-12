"""Read-only Kubernetes API helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast
from urllib.parse import quote

import httpx
from rhetorica_backend.contracts.admin_integrations import KubernetesConnectionConfig

DEFAULT_USER_AGENT = "Rhetorica Kubernetes Connector"
MAX_ERROR_BODY_CHARS = 1200
MAX_LABELS = 20


class KubernetesRequestError(RuntimeError):
    """Structured Kubernetes HTTP failure safe to expose to orchestration."""

    def __init__(
        self,
        *,
        status_code: int,
        request_path: str,
        request_params: dict[str, Any],
        response_body: str,
    ) -> None:
        super().__init__(f"Kubernetes API request failed with HTTP {status_code} for {request_path}")
        self.status_code = status_code
        self.request_path = request_path
        self.request_params = request_params
        self.response_body = response_body

    @classmethod
    def from_response(
        cls,
        response: httpx.Response,
        *,
        request_path: str,
        request_params: dict[str, Any],
    ) -> KubernetesRequestError:
        return cls(
            status_code=response.status_code,
            request_path=request_path,
            request_params=request_params,
            response_body=response.text[:MAX_ERROR_BODY_CHARS],
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "request_path": self.request_path,
            "request_params": self.request_params,
            "response_body": self.response_body,
        }


class KubernetesService:
    """Thin read-only Kubernetes REST wrapper with bounded normalized output."""

    def __init__(self, client_factory: Callable[..., httpx.Client] = httpx.Client) -> None:
        self._client_factory = client_factory

    def list_pods(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
        limit: int,
        label_selector: str | None = None,
        field_selector: str | None = None,
    ) -> dict[str, Any]:
        payload = self._request_json(
            connection,
            f"/api/v1/namespaces/{self._quote(namespace)}/pods",
            params=self._list_params(limit, label_selector, field_selector),
        )
        return self._list_payload(payload, limit=limit, normalizer=self._pod_summary)

    def get_namespace_status(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
    ) -> dict[str, Any]:
        payload = self._request_json(
            connection,
            f"/api/v1/namespaces/{self._quote(namespace)}",
        )
        status = self._dict(payload.get("status"))
        return {
            "metadata": self._metadata(payload),
            "phase": status.get("phase"),
            "conditions": self._conditions(status),
        }

    def get_pod(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
        pod_name: str,
    ) -> dict[str, Any]:
        payload = self._request_json(
            connection,
            f"/api/v1/namespaces/{self._quote(namespace)}/pods/{self._quote(pod_name)}",
        )
        return self._pod_detail(payload)

    def read_pod_logs(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
        pod_name: str,
        container: str | None = None,
        tail_lines: int | None = None,
        since_seconds: int | None = None,
        previous: bool = False,
        timestamps: bool = True,
        max_chars: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        bounded_chars = self._bounded_int(
            max_chars,
            default=connection.log_max_chars,
            minimum=1000,
            maximum=50000,
        )
        bounded_offset = self._bounded_int(offset, default=0, minimum=0, maximum=5_000_000)
        params: dict[str, Any] = {"timestamps": timestamps, "previous": previous}
        if container:
            params["container"] = container
        if tail_lines is not None:
            params["tailLines"] = self._bounded_int(tail_lines, default=200, minimum=1, maximum=10000)
        if since_seconds is not None:
            params["sinceSeconds"] = self._bounded_int(
                since_seconds, default=3600, minimum=1, maximum=604800
            )
        content = self._request_text(
            connection,
            f"/api/v1/namespaces/{self._quote(namespace)}/pods/{self._quote(pod_name)}/log",
            params=params,
        )
        content_length = len(content)
        chunk = content[bounded_offset : bounded_offset + bounded_chars]
        next_offset = (
            bounded_offset + bounded_chars
            if bounded_offset + bounded_chars < content_length
            else None
        )
        return {
            "namespace": namespace,
            "pod_name": pod_name,
            "container": container,
            "content": chunk,
            "content_length": content_length,
            "offset": bounded_offset,
            "max_chars": bounded_chars,
            "next_offset": next_offset,
            "truncated": next_offset is not None,
        }

    def list_deployments(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
        limit: int,
        label_selector: str | None = None,
    ) -> dict[str, Any]:
        payload = self._request_json(
            connection,
            f"/apis/apps/v1/namespaces/{self._quote(namespace)}/deployments",
            params=self._list_params(limit, label_selector, None),
        )
        return self._list_payload(payload, limit=limit, normalizer=self._deployment_summary)

    def get_deployment(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
        deployment_name: str,
    ) -> dict[str, Any]:
        payload = self._request_json(
            connection,
            (
                f"/apis/apps/v1/namespaces/{self._quote(namespace)}/deployments/"
                f"{self._quote(deployment_name)}"
            ),
        )
        return self._deployment_detail(payload)

    def get_rollout_status(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
        deployment_name: str,
    ) -> dict[str, Any]:
        deployment = self.get_deployment(
            connection,
            namespace=namespace,
            deployment_name=deployment_name,
        )
        spec_replicas = int(deployment.get("spec", {}).get("replicas") or 0)
        status = deployment.get("status", {})
        updated = int(status.get("updated_replicas") or 0)
        available = int(status.get("available_replicas") or 0)
        observed_generation = status.get("observed_generation")
        generation = deployment.get("metadata", {}).get("generation")
        complete = (
            observed_generation == generation
            and updated >= spec_replicas
            and available >= spec_replicas
        )
        return {
            "namespace": namespace,
            "deployment_name": deployment_name,
            "complete": complete,
            "generation": generation,
            "observed_generation": observed_generation,
            "replicas": spec_replicas,
            "updated_replicas": updated,
            "available_replicas": available,
            "conditions": status.get("conditions", []),
        }

    def list_replicasets(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
        limit: int,
        label_selector: str | None = None,
    ) -> dict[str, Any]:
        payload = self._request_json(
            connection,
            f"/apis/apps/v1/namespaces/{self._quote(namespace)}/replicasets",
            params=self._list_params(limit, label_selector, None),
        )
        return self._list_payload(payload, limit=limit, normalizer=self._replicaset_summary)

    def list_statefulsets(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
        limit: int,
        label_selector: str | None = None,
    ) -> dict[str, Any]:
        payload = self._request_json(
            connection,
            f"/apis/apps/v1/namespaces/{self._quote(namespace)}/statefulsets",
            params=self._list_params(limit, label_selector, None),
        )
        return self._list_payload(payload, limit=limit, normalizer=self._statefulset_summary)

    def list_jobs(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
        limit: int,
        label_selector: str | None = None,
    ) -> dict[str, Any]:
        payload = self._request_json(
            connection,
            f"/apis/batch/v1/namespaces/{self._quote(namespace)}/jobs",
            params=self._list_params(limit, label_selector, None),
        )
        return self._list_payload(payload, limit=limit, normalizer=self._job_summary)

    def get_job(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
        job_name: str,
    ) -> dict[str, Any]:
        payload = self._request_json(
            connection,
            f"/apis/batch/v1/namespaces/{self._quote(namespace)}/jobs/{self._quote(job_name)}",
        )
        return self._job_detail(payload)

    def list_ingresses(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
        limit: int,
        label_selector: str | None = None,
    ) -> dict[str, Any]:
        payload = self._request_json(
            connection,
            f"/apis/networking.k8s.io/v1/namespaces/{self._quote(namespace)}/ingresses",
            params=self._list_params(limit, label_selector, None),
        )
        return self._list_payload(payload, limit=limit, normalizer=self._ingress_summary)

    def list_services(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
        limit: int,
        label_selector: str | None = None,
    ) -> dict[str, Any]:
        payload = self._request_json(
            connection,
            f"/api/v1/namespaces/{self._quote(namespace)}/services",
            params=self._list_params(limit, label_selector, None),
        )
        return self._list_payload(payload, limit=limit, normalizer=self._service_summary)

    def list_endpoints(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
        limit: int,
        label_selector: str | None = None,
    ) -> dict[str, Any]:
        payload = self._request_json(
            connection,
            f"/api/v1/namespaces/{self._quote(namespace)}/endpoints",
            params=self._list_params(limit, label_selector, None),
        )
        return self._list_payload(payload, limit=limit, normalizer=self._endpoint_summary)

    def list_events(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
        limit: int,
        field_selector: str | None = None,
    ) -> dict[str, Any]:
        payload = self._request_json(
            connection,
            f"/api/v1/namespaces/{self._quote(namespace)}/events",
            params=self._list_params(limit, None, field_selector),
        )
        return self._list_payload(payload, limit=limit, normalizer=self._event_summary)

    def list_resource_quotas(
        self,
        connection: KubernetesConnectionConfig,
        *,
        namespace: str,
        limit: int,
    ) -> dict[str, Any]:
        payload = self._request_json(
            connection,
            f"/api/v1/namespaces/{self._quote(namespace)}/resourcequotas",
            params=self._list_params(limit, None, None),
        )
        return self._list_payload(payload, limit=limit, normalizer=self._quota_summary)

    def _request_json(
        self,
        connection: KubernetesConnectionConfig,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self._request_any(connection, path, params=params)
        return payload if isinstance(payload, dict) else {}

    def _request_text(
        self,
        connection: KubernetesConnectionConfig,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> str:
        payload = self._request_any(connection, path, params=params, text=True)
        return payload if isinstance(payload, str) else ""

    def _request_any(
        self,
        connection: KubernetesConnectionConfig,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        text: bool = False,
    ) -> Any:
        request_params = {key: value for key, value in (params or {}).items() if value is not None}
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        if connection.bearer_token:
            headers["Authorization"] = f"Bearer {connection.bearer_token}"
        with self._client_factory(timeout=connection.timeout_seconds, verify=connection.verify_tls) as client:
            response = client.get(
                f"{connection.base_url.rstrip('/')}{path}",
                params=request_params,
                headers=headers,
            )
        if response.status_code >= 400:
            raise KubernetesRequestError.from_response(
                response,
                request_path=path,
                request_params=request_params,
            )
        if text:
            return response.text
        return response.json()

    def _list_params(
        self,
        limit: int,
        label_selector: str | None,
        field_selector: str | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if label_selector:
            params["labelSelector"] = label_selector
        if field_selector:
            params["fieldSelector"] = field_selector
        return params

    @classmethod
    def _list_payload(
        cls,
        payload: dict[str, Any],
        *,
        limit: int,
        normalizer: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        items = cls._list(payload.get("items"))
        normalized = [normalizer(item) for item in items[:limit] if isinstance(item, dict)]
        metadata = cls._dict(payload.get("metadata"))
        return {
            "items": normalized,
            "count": len(normalized),
            "empty": len(normalized) == 0,
            "limit": limit,
            "resource_version": metadata.get("resourceVersion"),
            "continue_token": metadata.get("continue"),
            "truncated": bool(metadata.get("continue")),
        }

    @classmethod
    def _metadata(cls, payload: dict[str, Any]) -> dict[str, Any]:
        meta = cls._dict(payload.get("metadata"))
        labels = cls._dict(meta.get("labels"))
        return {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "generation": meta.get("generation"),
            "creation_timestamp": meta.get("creationTimestamp"),
            "labels": dict(list(labels.items())[:MAX_LABELS]),
        }

    def _pod_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        status = self._dict(item.get("status"))
        spec = self._dict(item.get("spec"))
        return {
            "metadata": self._metadata(item),
            "phase": status.get("phase"),
            "pod_ip": status.get("podIP"),
            "node_name": spec.get("nodeName"),
            "restart_count": self._restart_count(status),
            "container_statuses": self._container_statuses(status),
        }

    def _pod_detail(self, item: dict[str, Any]) -> dict[str, Any]:
        status = self._dict(item.get("status"))
        spec = self._dict(item.get("spec"))
        return {
            "metadata": self._metadata(item),
            "spec": {
                "service_account_name": spec.get("serviceAccountName"),
                "node_name": spec.get("nodeName"),
                "restart_policy": spec.get("restartPolicy"),
                "containers": self._containers(spec),
            },
            "status": {
                "phase": status.get("phase"),
                "pod_ip": status.get("podIP"),
                "host_ip": status.get("hostIP"),
                "conditions": status.get("conditions", []),
                "container_statuses": self._container_statuses(status),
                "start_time": status.get("startTime"),
            },
        }

    def _deployment_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        status = self._dict(item.get("status"))
        spec = self._dict(item.get("spec"))
        return {
            "metadata": self._metadata(item),
            "replicas": spec.get("replicas"),
            "updated_replicas": status.get("updatedReplicas"),
            "available_replicas": status.get("availableReplicas"),
            "ready_replicas": status.get("readyReplicas"),
            "conditions": self._conditions(status),
        }

    def _deployment_detail(self, item: dict[str, Any]) -> dict[str, Any]:
        summary = self._deployment_summary(item)
        status = self._dict(item.get("status"))
        spec = self._dict(item.get("spec"))
        return {
            **summary,
            "spec": {
                "replicas": spec.get("replicas"),
                "strategy": spec.get("strategy"),
                "selector": spec.get("selector"),
            },
            "status": {
                "observed_generation": status.get("observedGeneration"),
                "replicas": status.get("replicas"),
                "updated_replicas": status.get("updatedReplicas"),
                "ready_replicas": status.get("readyReplicas"),
                "available_replicas": status.get("availableReplicas"),
                "unavailable_replicas": status.get("unavailableReplicas"),
                "conditions": self._conditions(status),
            },
        }

    def _replicaset_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        status = self._dict(item.get("status"))
        spec = self._dict(item.get("spec"))
        metadata = self._dict(item.get("metadata"))
        return {
            "metadata": self._metadata(item),
            "replicas": spec.get("replicas"),
            "ready_replicas": status.get("readyReplicas"),
            "available_replicas": status.get("availableReplicas"),
            "owner_references": metadata.get("ownerReferences", []),
        }

    def _statefulset_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        status = self._dict(item.get("status"))
        spec = self._dict(item.get("spec"))
        return {
            "metadata": self._metadata(item),
            "replicas": spec.get("replicas"),
            "service_name": spec.get("serviceName"),
            "ready_replicas": status.get("readyReplicas"),
            "current_replicas": status.get("currentReplicas"),
            "updated_replicas": status.get("updatedReplicas"),
            "available_replicas": status.get("availableReplicas"),
            "conditions": self._conditions(status),
        }

    def _job_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        status = self._dict(item.get("status"))
        spec = self._dict(item.get("spec"))
        return {
            "metadata": self._metadata(item),
            "parallelism": spec.get("parallelism"),
            "completions": spec.get("completions"),
            "active": status.get("active"),
            "succeeded": status.get("succeeded"),
            "failed": status.get("failed"),
            "conditions": self._conditions(status),
        }

    def _job_detail(self, item: dict[str, Any]) -> dict[str, Any]:
        return self._job_summary(item)

    def _ingress_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        spec = self._dict(item.get("spec"))
        status = self._dict(item.get("status"))
        rules = self._list(spec.get("rules"))
        return {
            "metadata": self._metadata(item),
            "class_name": spec.get("ingressClassName"),
            "hosts": [rule.get("host") for rule in rules if isinstance(rule, dict)],
            "load_balancer": status.get("loadBalancer"),
        }

    def _service_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        spec = self._dict(item.get("spec"))
        status = self._dict(item.get("status"))
        return {
            "metadata": self._metadata(item),
            "type": spec.get("type"),
            "cluster_ip": spec.get("clusterIP"),
            "external_name": spec.get("externalName"),
            "ports": self._service_ports(spec),
            "selector": spec.get("selector", {}),
            "load_balancer": status.get("loadBalancer"),
        }

    def _endpoint_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        subsets = self._list(item.get("subsets"))
        ready_addresses = 0
        not_ready_addresses = 0
        ports: list[dict[str, Any]] = []
        target_refs: list[dict[str, Any]] = []
        for subset in subsets:
            if not isinstance(subset, dict):
                continue
            addresses = self._list(subset.get("addresses"))
            not_ready = self._list(subset.get("notReadyAddresses"))
            ready_addresses += len(addresses)
            not_ready_addresses += len(not_ready)
            ports.extend(self._endpoint_ports(subset))
            target_refs.extend(self._endpoint_target_refs(addresses + not_ready))
        return {
            "metadata": self._metadata(item),
            "ready_addresses": ready_addresses,
            "not_ready_addresses": not_ready_addresses,
            "ports": ports[:20],
            "target_refs": target_refs[:20],
        }

    def _event_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        involved = self._dict(item.get("involvedObject"))
        return {
            "metadata": self._metadata(item),
            "type": item.get("type"),
            "reason": item.get("reason"),
            "message": item.get("message"),
            "count": item.get("count"),
            "first_timestamp": item.get("firstTimestamp"),
            "last_timestamp": item.get("lastTimestamp"),
            "involved_object": {
                "kind": involved.get("kind"),
                "name": involved.get("name"),
                "namespace": involved.get("namespace"),
            },
        }

    def _quota_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        status = self._dict(item.get("status"))
        return {
            "metadata": self._metadata(item),
            "hard": status.get("hard", {}),
            "used": status.get("used", {}),
        }

    @staticmethod
    def _service_ports(spec: dict[str, Any]) -> list[dict[str, Any]]:
        ports = KubernetesService._list(spec.get("ports"))
        result: list[dict[str, Any]] = []
        for item in ports:
            if not isinstance(item, dict):
                continue
            result.append({
                "name": item.get("name"),
                "protocol": item.get("protocol"),
                "port": item.get("port"),
                "target_port": item.get("targetPort"),
            })
        return result

    @staticmethod
    def _endpoint_ports(subset: dict[str, Any]) -> list[dict[str, Any]]:
        ports = KubernetesService._list(subset.get("ports"))
        result: list[dict[str, Any]] = []
        for item in ports:
            if not isinstance(item, dict):
                continue
            result.append({
                "name": item.get("name"),
                "protocol": item.get("protocol"),
                "port": item.get("port"),
            })
        return result

    @staticmethod
    def _endpoint_target_refs(addresses: list[Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for address in addresses:
            if not isinstance(address, dict):
                continue
            target_ref = KubernetesService._dict(address.get("targetRef"))
            if not target_ref:
                continue
            result.append({
                "kind": target_ref.get("kind"),
                "name": target_ref.get("name"),
                "namespace": target_ref.get("namespace"),
            })
        return result

    @staticmethod
    def _containers(spec: dict[str, Any]) -> list[dict[str, Any]]:
        containers = KubernetesService._list(spec.get("containers"))
        result: list[dict[str, Any]] = []
        for container in containers:
            if not isinstance(container, dict):
                continue
            result.append({
                "name": container.get("name"),
                "image": container.get("image"),
                "ports": container.get("ports", []),
                "resources": container.get("resources", {}),
            })
        return result

    @staticmethod
    def _container_statuses(status: dict[str, Any]) -> list[dict[str, Any]]:
        statuses = status.get("containerStatuses")
        if not isinstance(statuses, list):
            return []
        result: list[dict[str, Any]] = []
        for item in statuses:
            if not isinstance(item, dict):
                continue
            result.append({
                "name": item.get("name"),
                "ready": item.get("ready"),
                "restart_count": item.get("restartCount"),
                "state": item.get("state"),
                "last_state": item.get("lastState"),
                "image": item.get("image"),
            })
        return result

    @classmethod
    def _restart_count(cls, status: dict[str, Any]) -> int:
        return sum(
            int(item.get("restart_count") or 0)
            for item in cls._container_statuses(status)
            if isinstance(item.get("restart_count"), int)
        )

    @staticmethod
    def _conditions(status: dict[str, Any]) -> list[dict[str, Any]]:
        conditions = status.get("conditions")
        if not isinstance(conditions, list):
            return []
        return [
            {
                "type": item.get("type"),
                "status": item.get("status"),
                "reason": item.get("reason"),
                "message": item.get("message"),
                "last_update_time": item.get("lastUpdateTime"),
                "last_transition_time": item.get("lastTransitionTime"),
            }
            for item in conditions
            if isinstance(item, dict)
        ]

    @staticmethod
    def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _quote(value: str) -> str:
        return quote(value, safe="")

    @staticmethod
    def _dict(value: Any) -> dict[str, Any]:
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    @staticmethod
    def _list(value: Any) -> list[Any]:
        return cast(list[Any], value) if isinstance(value, list) else []
