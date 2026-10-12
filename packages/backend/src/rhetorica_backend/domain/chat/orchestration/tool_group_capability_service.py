"""Model-controlled catalog and loading for deferred ordinary tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rhetorica_backend.domain.chat.error_contract import ErrorCategory, build_tool_error

from .tool_group_registry import TOOL_GROUP_BY_NAME

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


class ToolGroupCapabilityService:
    """Expose exact namespace discovery without backend relevance routing."""

    @staticmethod
    def list_groups(
        *,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
        **_: Any,
    ) -> dict[str, Any]:
        if arguments:
            return ToolGroupCapabilityService._error(
                code="tool_group_list_arguments_invalid",
                message="list_tool_groups does not accept arguments.",
            )
        groups = [
            {
                "group_name": group_name,
                "description": TOOL_GROUP_BY_NAME[group_name].description,
                "tool_names": list(tool_names),
                "tool_count": len(tool_names),
            }
            for group_name, tool_names in state.available_tool_groups.items()
        ]
        return {
            "ok": True,
            "groups": groups,
            "group_count": len(groups),
            "selection_policy": "model_controlled_exact_name",
        }

    @staticmethod
    def load_group(
        *,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
        **_: Any,
    ) -> dict[str, Any]:
        if set(arguments) != {"group_name"}:
            return ToolGroupCapabilityService._error(
                code="tool_group_load_arguments_invalid",
                message="load_tool_group requires only group_name.",
            )
        group_name = arguments.get("group_name")
        if not isinstance(group_name, str) or not group_name.strip():
            return ToolGroupCapabilityService._error(
                code="tool_group_name_invalid",
                message="group_name must be a non-empty exact name from list_tool_groups.",
            )

        tool_names = state.available_tool_groups.get(group_name)
        if tool_names is None:
            return ToolGroupCapabilityService._error(
                code="tool_group_unavailable",
                message=f"Tool group '{group_name}' is unavailable for this turn.",
            )

        missing = [
            name for name in tool_names if name not in state.deferred_tool_definitions
        ]
        if missing:
            return ToolGroupCapabilityService._error(
                code="tool_group_definition_missing",
                message=f"Tool group '{group_name}' cannot be loaded atomically.",
            )

        already_loaded = all(name in state.loaded_tool_definitions for name in tool_names)
        for name in tool_names:
            state.loaded_tool_definitions[name] = state.deferred_tool_definitions[name]
            if name not in state.capability_state.available_tools:
                state.capability_state.available_tools.append(name)
        if group_name not in state.loaded_tool_group_names:
            state.loaded_tool_group_names.append(group_name)
        return {
            "ok": True,
            "group_name": group_name,
            "loaded_tool_names": list(tool_names),
            "already_loaded": already_loaded,
        }

    @staticmethod
    def unload_group(
        *,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
        **_: Any,
    ) -> dict[str, Any]:
        if set(arguments) != {"group_name"}:
            return ToolGroupCapabilityService._error(
                code="tool_group_unload_arguments_invalid",
                message="unload_tool_group requires only group_name.",
            )
        group_name = arguments.get("group_name")
        if not isinstance(group_name, str) or not group_name.strip():
            return ToolGroupCapabilityService._error(
                code="tool_group_name_invalid",
                message="group_name must be a non-empty exact available group name.",
            )
        tool_names = state.available_tool_groups.get(group_name)
        if tool_names is None:
            return ToolGroupCapabilityService._error(
                code="tool_group_unavailable",
                message=f"Tool group '{group_name}' is unavailable for this turn.",
            )
        already_unloaded = group_name not in state.loaded_tool_group_names
        for name in tool_names:
            state.loaded_tool_definitions.pop(name, None)
            if name in state.capability_state.available_tools:
                state.capability_state.available_tools.remove(name)
        if group_name in state.loaded_tool_group_names:
            state.loaded_tool_group_names.remove(group_name)
        return {
            "ok": True,
            "group_name": group_name,
            "unloaded_tool_names": list(tool_names),
            "already_unloaded": already_unloaded,
        }

    @staticmethod
    def _error(*, code: str, message: str) -> dict[str, Any]:
        return build_tool_error(
            error_code=code,
            error_category=ErrorCategory.RECOVERABLE,
            user_message=message,
            retry_recommended=False,
            can_continue=True,
        )
