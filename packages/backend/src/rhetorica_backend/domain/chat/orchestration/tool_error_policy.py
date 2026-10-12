"""Shared tool error handling policy for chat orchestrators."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


class ToolErrorPolicy:
    """Encapsulates retry-stop rules for recoverable tool errors."""

    @staticmethod
    def should_stop_after_tool_error(
        tool_result: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> bool:
        if tool_result.get("ok") is not False:
            return False
        if tool_result.get("can_continue") is False:
            return True
        return tool_result.get("error_category") == "FATAL"

    @staticmethod
    def tool_result_user_message(tool_result: dict[str, Any]) -> str:
        user_message = tool_result.get("user_message")
        base = (
            user_message
            if isinstance(user_message, str) and user_message.strip()
            else "Unable to continue due to a fatal tool error."
        )
        preflight_errors = tool_result.get("preflight_errors")
        if not isinstance(preflight_errors, list):
            return base
        diagnostics: list[str] = []
        for item in preflight_errors:
            if not isinstance(item, dict):
                continue
            message = item.get("message")
            if isinstance(message, str) and message.strip():
                diagnostics.append(message.strip())
        if not diagnostics:
            return base
        return f"{base} Diagnostics: {'; '.join(diagnostics[:2])}"
