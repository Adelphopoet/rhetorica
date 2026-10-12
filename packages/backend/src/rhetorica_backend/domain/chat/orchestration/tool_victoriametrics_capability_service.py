"""VictoriaMetrics-family orchestration tools."""

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
from rhetorica_backend.domain.chat.orchestration.agent_connector_policy import (
    AgentConnectorPolicy,
)
from rhetorica_backend.domain.chat.victoriametrics import (
    VictoriaMetricsRequestError,
    VictoriaMetricsService,
)
from rhetorica_backend.infra.postgres.victoriametrics_connector_repository import (
    VictoriaMetricsConnectorRepository,
)

if TYPE_CHECKING:
    from rhetorica_backend.contracts.admin_integrations import VictoriaMetricsConnectionConfig
    from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


class ToolVictoriaMetricsCapabilityService:
    """Capability-family service for VictoriaMetrics intent tools."""

    def __init__(
        self,
        *,
        victoriametrics_connector_repository: VictoriaMetricsConnectorRepository | None,
        victoriametrics_service: VictoriaMetricsService | None = None,
        bounded_int: Callable[..., int],
        tool_error_from_exception: Callable[..., dict[str, Any]],
    ) -> None:
        self._victoriametrics_connector_repository = victoriametrics_connector_repository
        self._victoriametrics_service = victoriametrics_service or VictoriaMetricsService()
        self._bounded_int = bounded_int
        self._tool_error_from_exception = tool_error_from_exception

    def execute_list_victoriametrics_metric_templates(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error
        if connection is None:
            return self._missing_connector_error()
        return self._ok(
            connector_name=connector_name,
            payload=self._victoriametrics_service.list_templates(connection),
            state=state,
        )

    def execute_query_victoriametrics_instant(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error
        template_id = self._required_str(
            arguments.get("template_id"),
            "victoriametrics_template_id_missing",
        )
        if isinstance(template_id, dict):
            return template_id
        if connection is None:
            return self._missing_connector_error()
        return self._call(
            lambda: self._victoriametrics_service.query_instant(
                connection,
                template_id=template_id,
                filters=self._string_dict(arguments.get("filters")),
                time=self._optional_str(arguments.get("time")),
                series_offset=self._bounded_int(
                    arguments.get("series_offset"),
                    default=0,
                    minimum=0,
                    maximum=100000,
                ),
                series_limit=self._bounded_int(
                    arguments.get("series_limit"),
                    default=connection.max_series,
                    minimum=1,
                    maximum=connection.max_series,
                ),
            ),
            connector_name=connector_name,
            state=state,
            warning="victoriametrics_instant_query_failed",
        )

    def execute_query_victoriametrics_range(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error
        template_id = self._required_str(
            arguments.get("template_id"),
            "victoriametrics_template_id_missing",
        )
        start = self._required_str(arguments.get("start"), "victoriametrics_start_missing")
        end = self._required_str(arguments.get("end"), "victoriametrics_end_missing")
        for value in (template_id, start, end):
            if isinstance(value, dict):
                return value
        if not isinstance(template_id, str) or not isinstance(start, str) or not isinstance(end, str):
            return build_tool_error(
                error_code="victoriametrics_invalid_time_or_template",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="VictoriaMetrics template_id, start, and end must be strings.",
                retry_recommended=False,
                can_continue=True,
            )
        if connection is None:
            return self._missing_connector_error()
        return self._call(
            lambda: self._victoriametrics_service.query_range(
                connection,
                template_id=template_id,
                filters=self._string_dict(arguments.get("filters")),
                start=start,
                end=end,
                step=self._optional_str(arguments.get("step")),
                series_offset=self._bounded_int(
                    arguments.get("series_offset"),
                    default=0,
                    minimum=0,
                    maximum=100000,
                ),
                series_limit=self._bounded_int(
                    arguments.get("series_limit"),
                    default=connection.max_series,
                    minimum=1,
                    maximum=connection.max_series,
                ),
                point_offset=self._bounded_int(
                    arguments.get("point_offset"),
                    default=0,
                    minimum=0,
                    maximum=1_000_000,
                ),
                point_limit=self._bounded_int(
                    arguments.get("point_limit"),
                    default=connection.max_datapoints,
                    minimum=1,
                    maximum=connection.max_datapoints,
                ),
            ),
            connector_name=connector_name,
            state=state,
            warning="victoriametrics_range_query_failed",
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
        except VictoriaMetricsRequestError as exc:
            state.warnings.append(warning)
            return self._victoriametrics_request_error_result(exc, warning)
        except ValueError as exc:
            state.warnings.append(warning)
            return build_tool_error(
                error_code=warning,
                error_category=ErrorCategory.RECOVERABLE,
                user_message=str(exc),
                retry_recommended=False,
                can_continue=True,
            )
        except Exception as exc:
            state.warnings.append(warning)
            return self._tool_error_from_exception(
                exc,
                default_error_code=warning,
                can_continue=True,
            )
        return self._ok(connector_name=connector_name, payload=payload, state=state)

    def _resolve_connection(
        self,
        *,
        request: ChatAskRequest,
        connector_name: Any,
        state: ToolOrchestrationState,
    ) -> tuple[VictoriaMetricsConnectionConfig | None, str, dict[str, Any] | None]:
        if self._victoriametrics_connector_repository is None:
            state.warnings.append("victoriametrics_connector_missing")
            return None, "", self._missing_connector_error()

        decision = AgentConnectorPolicy.resolve_connector_name(
            requested_name=connector_name,
            allowed_names=request.agent_allowed_victoriametrics_connector_names,
            family="victoriametrics",
        )
        if not decision.allowed:
            if decision.warning:
                state.warnings.append(decision.warning)
            return (
                None,
                "",
                build_tool_error(
                    error_code=decision.warning or "agent_victoriametrics_connector_not_allowed",
                    error_category=ErrorCategory.RECOVERABLE,
                    user_message="This agent cannot use the requested VictoriaMetrics connector.",
                    retry_recommended=False,
                    can_continue=True,
                ),
            )

        connection = self._victoriametrics_connector_repository.get_connection(
            workspace_id=request.workspace_id,
            name=decision.connector_name,
            visible_to_default_assistant=(
                True if request.agent_allowed_victoriametrics_connector_names is None else None
            ),
        )
        if connection is None:
            state.warnings.append("victoriametrics_connector_missing")
            return None, "", self._missing_connector_error()
        resolved_name = decision.connector_name
        if resolved_name is None:
            items = [
                item
                for item in self._victoriametrics_connector_repository.list(
                    request.workspace_id,
                    limit=100,
                )
                if item.is_active and item.visible_to_default_assistant
            ]
            resolved_name = items[0].name if items else "victoriametrics"
        return connection, resolved_name, None

    def _ok(
        self,
        *,
        connector_name: str,
        payload: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        state.metadata_sources.add("victoriametrics")
        return {
            "ok": True,
            "connector_name": connector_name,
            "victoriametrics": payload,
            "metadata_sources": sorted(state.metadata_sources),
        }

    @staticmethod
    def _missing_connector_error() -> dict[str, Any]:
        return build_tool_error(
            error_code="victoriametrics_connector_missing",
            error_category=ErrorCategory.RECOVERABLE,
            user_message="Requested VictoriaMetrics connector is unavailable.",
            retry_recommended=False,
            can_continue=True,
        )

    @staticmethod
    def _victoriametrics_request_error_result(
        exc: VictoriaMetricsRequestError,
        default_error_code: str,
    ) -> dict[str, Any]:
        result = build_tool_error(
            error_code=default_error_code,
            error_category=(
                ErrorCategory.TRANSIENT
                if is_transient_http_status(exc.status_code)
                else ErrorCategory.RECOVERABLE
            ),
            user_message="VictoriaMetrics API request failed.",
            retry_recommended=is_transient_http_status(exc.status_code),
            can_continue=True,
        )
        result["victoriametrics_error"] = exc.to_payload()
        return result

    @staticmethod
    def _required_str(value: Any, error_code: str) -> str | dict[str, Any]:
        if not isinstance(value, str) or not value.strip():
            return build_tool_error(
                error_code=error_code,
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Required VictoriaMetrics argument is missing.",
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
    def _string_dict(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {
            key.strip(): item.strip()
            for key, item in value.items()
            if isinstance(key, str) and key.strip() and isinstance(item, str) and item.strip()
        }
