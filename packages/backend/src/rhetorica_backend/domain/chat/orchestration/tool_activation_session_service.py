"""Restore exact model-selected tool activations for a conversation turn."""

from __future__ import annotations

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.conversation.session_state import ToolActivationManifest
from rhetorica_backend.domain.mcp_connectors import McpToolCatalogService
from rhetorica_backend.infra.postgres.mcp_connector_repository import McpConnectorRepository

from .state import ToolOrchestrationState


class ToolActivationSessionService:
    """Rebuild persisted schemas through current catalogs and access policy."""

    def __init__(self, *, mcp_repository: McpConnectorRepository | None) -> None:
        self._mcp_catalog = (
            McpToolCatalogService(repository=mcp_repository)
            if mcp_repository is not None
            else None
        )

    def restore(
        self,
        *,
        request: ChatAskRequest,
        state: ToolOrchestrationState,
        manifest: ToolActivationManifest,
    ) -> None:
        for group_name in manifest.group_names:
            tool_names = state.available_tool_groups.get(group_name)
            if tool_names is None:
                state.capability_state.policy_decisions.append(
                    f"persisted_tool_group_omitted:{group_name}"
                )
                continue
            if any(name not in state.deferred_tool_definitions for name in tool_names):
                state.capability_state.policy_decisions.append(
                    f"persisted_tool_group_incomplete:{group_name}"
                )
                continue
            for name in tool_names:
                state.loaded_tool_definitions[name] = state.deferred_tool_definitions[name]
                if name not in state.capability_state.available_tools:
                    state.capability_state.available_tools.append(name)
            state.loaded_tool_group_names.append(group_name)

        if (
            self._mcp_catalog is None
            or "mcp_connectors" not in state.available_tool_groups
        ):
            return
        for connector_name, remote_tool_names in manifest.mcp_remote_tool_names.items():
            exposed_by_remote: dict[str, str] = {}
            restored_remote_names: list[str] = []
            for remote_tool_name in remote_tool_names:
                outcome = self._mcp_catalog.load_tools(
                    workspace_id=request.workspace_id,
                    connector_name=connector_name,
                    remote_tool_names=[remote_tool_name],
                    allowed_connector_names=request.agent_allowed_mcp_connector_names,
                )
                loaded_tools = outcome.payload.get("loaded_tools")
                if not outcome.payload.get("ok") or not isinstance(loaded_tools, list):
                    state.capability_state.policy_decisions.append(
                        f"persisted_mcp_tool_omitted:{connector_name}:{remote_tool_name}"
                    )
                    continue
                item = loaded_tools[0] if loaded_tools else None
                definition = outcome.definitions[0] if outcome.definitions else None
                if not isinstance(item, dict):
                    continue
                if not isinstance(definition, dict):
                    continue
                remote_name = item.get("remote_tool_name")
                exposed_name = item.get("exposed_tool_name")
                if not isinstance(remote_name, str) or not isinstance(exposed_name, str):
                    continue
                state.loaded_mcp_tool_definitions[exposed_name] = definition
                if exposed_name not in state.capability_state.available_tools:
                    state.capability_state.available_tools.append(exposed_name)
                restored_remote_names.append(remote_name)
                exposed_by_remote[remote_name] = exposed_name
            if restored_remote_names:
                state.loaded_mcp_remote_tool_names[connector_name] = restored_remote_names
                state.loaded_mcp_exposed_tool_names[connector_name] = exposed_by_remote
