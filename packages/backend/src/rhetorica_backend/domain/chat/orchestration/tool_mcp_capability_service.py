"""Deterministic MCP catalog capabilities controlled by the LLM."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.error_contract import ErrorCategory, build_tool_error
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.mcp_connectors import McpToolCatalogService
from rhetorica_backend.infra.postgres.mcp_connector_repository import (
    McpConnectorRepository,
)


class ToolMcpCapabilityService:
    """List and load exact MCP tools without relevance heuristics."""

    TOOL_NAMES = ("list_mcp_tools", "load_mcp_tools", "unload_mcp_tools")

    def __init__(self, *, repository: McpConnectorRepository) -> None:
        self._catalog = McpToolCatalogService(repository=repository)

    def list_tools(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        _ = state
        connector_name = arguments.get("connector_name")
        offset = arguments.get("offset", 0)
        limit = arguments.get("limit", McpToolCatalogService.DEFAULT_PAGE_SIZE)
        if (
            not isinstance(connector_name, str)
            or not connector_name
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or not isinstance(limit, int)
            or isinstance(limit, bool)
        ):
            return self._arguments_error(
                "connector_name must be a non-empty exact name; offset and limit must be integers."
            )
        return self._catalog.list_tools(
            workspace_id=request.workspace_id,
            connector_name=connector_name,
            allowed_connector_names=request.agent_allowed_mcp_connector_names,
            offset=offset,
            limit=limit,
        )

    def load_tools(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connector_name = arguments.get("connector_name")
        remote_tool_names = arguments.get("remote_tool_names")
        if (
            not isinstance(connector_name, str)
            or not connector_name
            or not isinstance(remote_tool_names, list)
            or any(not isinstance(name, str) for name in remote_tool_names)
        ):
            return self._arguments_error(
                "connector_name and remote_tool_names with exact string values are required."
            )
        outcome = self._catalog.load_tools(
            workspace_id=request.workspace_id,
            connector_name=connector_name,
            remote_tool_names=remote_tool_names,
            allowed_connector_names=request.agent_allowed_mcp_connector_names,
        )
        if not outcome.payload.get("ok"):
            return outcome.payload

        for definition in outcome.definitions:
            function = definition.get("function")
            if not isinstance(function, dict):
                continue
            exposed_name = function.get("name")
            if not isinstance(exposed_name, str) or not exposed_name:
                continue
            state.loaded_mcp_tool_definitions[exposed_name] = definition
            if exposed_name not in state.capability_state.available_tools:
                state.capability_state.available_tools.append(exposed_name)
        loaded_tools = outcome.payload.get("loaded_tools")
        if isinstance(loaded_tools, list):
            connector_remote_names = state.loaded_mcp_remote_tool_names.setdefault(
                connector_name, []
            )
            connector_exposed_names = state.loaded_mcp_exposed_tool_names.setdefault(
                connector_name, {}
            )
            for item in loaded_tools:
                if not isinstance(item, dict):
                    continue
                remote_name = item.get("remote_tool_name")
                exposed_name = item.get("exposed_tool_name")
                if not isinstance(remote_name, str) or not isinstance(exposed_name, str):
                    continue
                if remote_name not in connector_remote_names:
                    connector_remote_names.append(remote_name)
                connector_exposed_names[remote_name] = exposed_name
        return outcome.payload

    def unload_tools(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        _ = request
        connector_name = arguments.get("connector_name")
        remote_tool_names = arguments.get("remote_tool_names")
        if (
            set(arguments) != {"connector_name", "remote_tool_names"}
            or not isinstance(connector_name, str)
            or not connector_name
            or not isinstance(remote_tool_names, list)
            or not remote_tool_names
            or any(not isinstance(name, str) or not name for name in remote_tool_names)
            or len(set(remote_tool_names)) != len(remote_tool_names)
        ):
            return self._arguments_error(
                "connector_name and unique remote_tool_names with exact string values are required."
            )

        active_remote_names = state.loaded_mcp_remote_tool_names.get(connector_name, [])
        missing = [name for name in remote_tool_names if name not in active_remote_names]
        if missing:
            error = build_tool_error(
                error_code="mcp_tools_not_loaded",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="One or more exact MCP tools are not loaded in this session.",
                retry_recommended=False,
                can_continue=True,
            )
            error["connector_name"] = connector_name
            error["missing_remote_tool_names"] = missing
            return error

        exposed_by_remote = state.loaded_mcp_exposed_tool_names.get(connector_name, {})
        unloaded: list[dict[str, str]] = []
        for remote_name in remote_tool_names:
            exposed_name = exposed_by_remote.pop(remote_name, None)
            if exposed_name is not None:
                state.loaded_mcp_tool_definitions.pop(exposed_name, None)
                if exposed_name in state.capability_state.available_tools:
                    state.capability_state.available_tools.remove(exposed_name)
                unloaded.append({
                    "remote_tool_name": remote_name,
                    "exposed_tool_name": exposed_name,
                })
            active_remote_names.remove(remote_name)
        if not active_remote_names:
            state.loaded_mcp_remote_tool_names.pop(connector_name, None)
            state.loaded_mcp_exposed_tool_names.pop(connector_name, None)
        return {
            "ok": True,
            "connector_name": connector_name,
            "unloaded_tools": unloaded,
        }

    @staticmethod
    def _arguments_error(message: str) -> dict[str, Any]:
        return build_tool_error(
            error_code="mcp_discovery_arguments_invalid",
            error_category=ErrorCategory.RECOVERABLE,
            user_message=message,
            retry_recommended=False,
            can_continue=True,
        )
