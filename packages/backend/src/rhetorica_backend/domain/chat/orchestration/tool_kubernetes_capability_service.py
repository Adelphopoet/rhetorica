"""Kubernetes-family orchestration tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.error_contract import (
    ErrorCategory,
    build_tool_error,
    is_transient_http_status,
    retry_idempotent,
)
from rhetorica_backend.domain.chat.kubernetes import KubernetesRequestError, KubernetesService
from rhetorica_backend.domain.chat.orchestration.agent_connector_policy import (
    AgentConnectorPolicy,
)
from rhetorica_backend.infra.postgres.kubernetes_connector_repository import (
    KubernetesConnectorRepository,
)

if TYPE_CHECKING:
    from rhetorica_backend.contracts.admin_integrations import KubernetesConnectionConfig
    from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


class ToolKubernetesCapabilityService:
    """Capability-family service for read-only Kubernetes tools."""

    def __init__(
        self,
        *,
        kubernetes_connector_repository: KubernetesConnectorRepository | None,
        kubernetes_service: KubernetesService | None = None,
        bounded_int: Callable[..., int],
        tool_error_from_exception: Callable[..., dict[str, Any]],
    ) -> None:
        self._kubernetes_connector_repository = kubernetes_connector_repository
        self._kubernetes_service = kubernetes_service or KubernetesService()
        self._bounded_int = bounded_int
        self._tool_error_from_exception = tool_error_from_exception

    def execute_list_kubernetes_pods(self, **kwargs: Any) -> dict[str, Any]:
        return self._list_call(
            kwargs,
            lambda connection, args, namespace, limit: self._kubernetes_service.list_pods(
                connection,
                namespace=namespace,
                limit=limit,
                label_selector=self._optional_str(args.get("label_selector")),
                field_selector=self._optional_str(args.get("field_selector")),
            ),
            warning="kubernetes_pods_failed",
        )

    def execute_get_kubernetes_namespace_status(self, **kwargs: Any) -> dict[str, Any]:
        return self._namespace_call(
            kwargs,
            lambda connection, _args, namespace: self._kubernetes_service.get_namespace_status(
                connection,
                namespace=namespace,
            ),
            warning="kubernetes_namespace_status_failed",
        )

    def execute_get_kubernetes_pod(self, **kwargs: Any) -> dict[str, Any]:
        return self._named_call(
            kwargs,
            "pod_name",
            lambda connection, args, namespace, name: self._kubernetes_service.get_pod(
                connection,
                namespace=namespace,
                pod_name=name,
            ),
            warning="kubernetes_get_pod_failed",
        )

    def execute_read_kubernetes_pod_logs(self, **kwargs: Any) -> dict[str, Any]:
        return self._named_call(
            kwargs,
            "pod_name",
            lambda connection, args, namespace, name: self._kubernetes_service.read_pod_logs(
                connection,
                namespace=namespace,
                pod_name=name,
                container=self._optional_str(args.get("container")),
                tail_lines=self._optional_int(args.get("tail_lines")),
                since_seconds=self._optional_int(args.get("since_seconds")),
                previous=args.get("previous") is True,
                timestamps=args.get("timestamps") is not False,
                max_chars=self._bounded_int(
                    args.get("max_chars"),
                    default=connection.log_max_chars,
                    minimum=1000,
                    maximum=50000,
                ),
                offset=self._bounded_int(args.get("offset"), default=0, minimum=0, maximum=5_000_000),
            ),
            warning="kubernetes_pod_logs_failed",
        )

    def execute_list_kubernetes_deployments(self, **kwargs: Any) -> dict[str, Any]:
        return self._list_call(
            kwargs,
            lambda connection, args, namespace, limit: self._kubernetes_service.list_deployments(
                connection,
                namespace=namespace,
                limit=limit,
                label_selector=self._optional_str(args.get("label_selector")),
            ),
            warning="kubernetes_deployments_failed",
        )

    def execute_get_kubernetes_deployment(self, **kwargs: Any) -> dict[str, Any]:
        return self._named_call(
            kwargs,
            "deployment_name",
            lambda connection, args, namespace, name: self._kubernetes_service.get_deployment(
                connection,
                namespace=namespace,
                deployment_name=name,
            ),
            warning="kubernetes_get_deployment_failed",
        )

    def execute_get_kubernetes_rollout_status(self, **kwargs: Any) -> dict[str, Any]:
        return self._named_call(
            kwargs,
            "deployment_name",
            lambda connection, args, namespace, name: self._kubernetes_service.get_rollout_status(
                connection,
                namespace=namespace,
                deployment_name=name,
            ),
            warning="kubernetes_rollout_status_failed",
        )

    def execute_list_kubernetes_replicasets(self, **kwargs: Any) -> dict[str, Any]:
        return self._list_call(
            kwargs,
            lambda connection, args, namespace, limit: self._kubernetes_service.list_replicasets(
                connection,
                namespace=namespace,
                limit=limit,
                label_selector=self._optional_str(args.get("label_selector")),
            ),
            warning="kubernetes_replicasets_failed",
        )

    def execute_list_kubernetes_statefulsets(self, **kwargs: Any) -> dict[str, Any]:
        return self._list_call(
            kwargs,
            lambda connection, args, namespace, limit: self._kubernetes_service.list_statefulsets(
                connection,
                namespace=namespace,
                limit=limit,
                label_selector=self._optional_str(args.get("label_selector")),
            ),
            warning="kubernetes_statefulsets_failed",
        )

    def execute_list_kubernetes_jobs(self, **kwargs: Any) -> dict[str, Any]:
        return self._list_call(
            kwargs,
            lambda connection, args, namespace, limit: self._kubernetes_service.list_jobs(
                connection,
                namespace=namespace,
                limit=limit,
                label_selector=self._optional_str(args.get("label_selector")),
            ),
            warning="kubernetes_jobs_failed",
        )

    def execute_get_kubernetes_job(self, **kwargs: Any) -> dict[str, Any]:
        return self._named_call(
            kwargs,
            "job_name",
            lambda connection, args, namespace, name: self._kubernetes_service.get_job(
                connection,
                namespace=namespace,
                job_name=name,
            ),
            warning="kubernetes_get_job_failed",
        )

    def execute_list_kubernetes_ingresses(self, **kwargs: Any) -> dict[str, Any]:
        return self._list_call(
            kwargs,
            lambda connection, args, namespace, limit: self._kubernetes_service.list_ingresses(
                connection,
                namespace=namespace,
                limit=limit,
                label_selector=self._optional_str(args.get("label_selector")),
            ),
            warning="kubernetes_ingresses_failed",
        )

    def execute_list_kubernetes_services(self, **kwargs: Any) -> dict[str, Any]:
        return self._list_call(
            kwargs,
            lambda connection, args, namespace, limit: self._kubernetes_service.list_services(
                connection,
                namespace=namespace,
                limit=limit,
                label_selector=self._optional_str(args.get("label_selector")),
            ),
            warning="kubernetes_services_failed",
        )

    def execute_list_kubernetes_endpoints(self, **kwargs: Any) -> dict[str, Any]:
        return self._list_call(
            kwargs,
            lambda connection, args, namespace, limit: self._kubernetes_service.list_endpoints(
                connection,
                namespace=namespace,
                limit=limit,
                label_selector=self._optional_str(args.get("label_selector")),
            ),
            warning="kubernetes_endpoints_failed",
        )

    def execute_list_kubernetes_events(self, **kwargs: Any) -> dict[str, Any]:
        return self._list_call(
            kwargs,
            lambda connection, args, namespace, limit: self._kubernetes_service.list_events(
                connection,
                namespace=namespace,
                limit=limit,
                field_selector=self._optional_str(args.get("field_selector")),
            ),
            warning="kubernetes_events_failed",
        )

    def execute_list_kubernetes_resource_quotas(self, **kwargs: Any) -> dict[str, Any]:
        return self._list_call(
            kwargs,
            lambda connection, args, namespace, limit: self._kubernetes_service.list_resource_quotas(
                connection,
                namespace=namespace,
                limit=limit,
            ),
            warning="kubernetes_resource_quotas_failed",
        )

    def _namespace_call(
        self,
        kwargs: dict[str, Any],
        operation: Callable[[KubernetesConnectionConfig, dict[str, Any], str], dict[str, Any]],
        *,
        warning: str,
    ) -> dict[str, Any]:
        request = kwargs["request"]
        arguments = kwargs["arguments"]
        state = kwargs["state"]
        connection, connector_name, namespace, error = self._resolve_connection_and_namespace(
            request=request,
            arguments=arguments,
            state=state,
        )
        if error is not None:
            return error
        if connection is None:
            return self._missing_connector_error()
        return self._call(
            lambda: operation(connection, arguments, namespace),
            connector_name=connector_name,
            state=state,
            warning=warning,
        )

    def _list_call(
        self,
        kwargs: dict[str, Any],
        operation: Callable[[KubernetesConnectionConfig, dict[str, Any], str, int], dict[str, Any]],
        *,
        warning: str,
    ) -> dict[str, Any]:
        request = kwargs["request"]
        arguments = kwargs["arguments"]
        state = kwargs["state"]
        connection, connector_name, namespace, error = self._resolve_connection_and_namespace(
            request=request,
            arguments=arguments,
            state=state,
        )
        if error is not None:
            return error
        if connection is None:
            return self._missing_connector_error()
        limit = self._bounded_int(
            arguments.get("limit"),
            default=connection.default_limit,
            minimum=1,
            maximum=connection.max_limit,
        )
        return self._call(
            lambda: operation(connection, arguments, namespace, limit),
            connector_name=connector_name,
            state=state,
            warning=warning,
        )

    def _named_call(
        self,
        kwargs: dict[str, Any],
        name_arg: str,
        operation: Callable[[KubernetesConnectionConfig, dict[str, Any], str, str], dict[str, Any]],
        *,
        warning: str,
    ) -> dict[str, Any]:
        request = kwargs["request"]
        arguments = kwargs["arguments"]
        state = kwargs["state"]
        connection, connector_name, namespace, error = self._resolve_connection_and_namespace(
            request=request,
            arguments=arguments,
            state=state,
        )
        if error is not None:
            return error
        name = self._required_str(arguments.get(name_arg), f"kubernetes_{name_arg}_missing")
        if isinstance(name, dict):
            return name
        if connection is None:
            return self._missing_connector_error()
        return self._call(
            lambda: operation(connection, arguments, namespace, name),
            connector_name=connector_name,
            state=state,
            warning=warning,
        )

    def _call(
        self,
        operation: Callable[[], dict[str, Any]],
        *,
        connector_name: str,
        state: ToolOrchestrationState,
        warning: str,
    ) -> dict[str, Any]:
        try:
            payload = retry_idempotent(operation, max_attempts=2)
        except KubernetesRequestError as exc:
            state.warnings.append(warning)
            return self._kubernetes_request_error_result(exc, warning)
        except Exception as exc:
            state.warnings.append(warning)
            return self._tool_error_from_exception(
                exc,
                default_error_code=warning,
                can_continue=True,
            )
        state.metadata_sources.add("kubernetes")
        return {
            "ok": True,
            "connector_name": connector_name,
            "kubernetes": payload,
            "metadata_sources": sorted(state.metadata_sources),
        }

    def _resolve_connection_and_namespace(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> tuple[KubernetesConnectionConfig | None, str, str, dict[str, Any] | None]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return None, "", "", error
        if connection is None:
            return None, "", "", self._missing_connector_error()
        namespace = self._required_str(arguments.get("namespace"), "kubernetes_namespace_missing")
        if isinstance(namespace, dict):
            return None, "", "", namespace
        if namespace not in connection.namespace_allowlist:
            state.warnings.append("kubernetes_namespace_not_allowed")
            result = build_tool_error(
                error_code="kubernetes_namespace_not_allowed",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Requested Kubernetes namespace is not in connector allowlist.",
                retry_recommended=False,
                can_continue=True,
            )
            result["kubernetes"] = {
                "connector_name": connector_name,
                "requested_namespace": namespace,
                "namespace_allowlist": list(connection.namespace_allowlist),
            }
            return (
                None,
                "",
                "",
                result,
            )
        return connection, connector_name, namespace, None

    def _resolve_connection(
        self,
        *,
        request: ChatAskRequest,
        connector_name: Any,
        state: ToolOrchestrationState,
    ) -> tuple[KubernetesConnectionConfig | None, str, dict[str, Any] | None]:
        if self._kubernetes_connector_repository is None:
            state.warnings.append("kubernetes_connector_missing")
            return None, "", self._missing_connector_error()

        decision = AgentConnectorPolicy.resolve_connector_name(
            requested_name=connector_name,
            allowed_names=request.agent_allowed_kubernetes_connector_names,
            family="kubernetes",
        )
        if not decision.allowed:
            if decision.warning:
                state.warnings.append(decision.warning)
            return (
                None,
                "",
                build_tool_error(
                    error_code=decision.warning or "agent_kubernetes_connector_not_allowed",
                    error_category=ErrorCategory.RECOVERABLE,
                    user_message="This agent cannot use the requested Kubernetes connector.",
                    retry_recommended=False,
                    can_continue=True,
                ),
            )

        connection = self._kubernetes_connector_repository.get_connection(
            workspace_id=request.workspace_id,
            name=decision.connector_name,
            visible_to_default_assistant=(
                True if request.agent_allowed_kubernetes_connector_names is None else None
            ),
        )
        if connection is None:
            state.warnings.append("kubernetes_connector_missing")
            return None, "", self._missing_connector_error()
        resolved_name = decision.connector_name
        if resolved_name is None:
            items = [
                item
                for item in self._kubernetes_connector_repository.list(request.workspace_id, limit=100)
                if item.is_active and item.visible_to_default_assistant
            ]
            resolved_name = items[0].name if items else "kubernetes"
        return connection, resolved_name, None

    @staticmethod
    def _missing_connector_error() -> dict[str, Any]:
        return build_tool_error(
            error_code="kubernetes_connector_missing",
            error_category=ErrorCategory.RECOVERABLE,
            user_message="Requested Kubernetes connector is unavailable.",
            retry_recommended=False,
            can_continue=True,
        )

    @staticmethod
    def _kubernetes_request_error_result(
        exc: KubernetesRequestError,
        default_error_code: str,
    ) -> dict[str, Any]:
        result = build_tool_error(
            error_code=default_error_code,
            error_category=(
                ErrorCategory.TRANSIENT
                if is_transient_http_status(exc.status_code)
                else ErrorCategory.RECOVERABLE
            ),
            user_message="Kubernetes API request failed.",
            retry_recommended=is_transient_http_status(exc.status_code),
            can_continue=True,
        )
        result["kubernetes_error"] = exc.to_payload()
        return result

    @staticmethod
    def _required_str(value: Any, error_code: str) -> str | dict[str, Any]:
        if not isinstance(value, str) or not value.strip():
            return build_tool_error(
                error_code=error_code,
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Required Kubernetes argument is missing.",
                retry_recommended=False,
                can_continue=True,
            )
        return value.strip()

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
