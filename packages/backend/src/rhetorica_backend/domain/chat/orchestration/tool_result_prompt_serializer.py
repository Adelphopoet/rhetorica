"""Compact tool results before appending them to the LLM prompt."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.domain.chat.orchestration.prompt_artifact_serializer import (
    PromptArtifactDiagnostics,
    PromptArtifactEnvelope,
    PromptArtifactSerializer,
    PromptContextBudgetPolicy,
)


class ToolResultPromptSerializer:
    """Keeps full tool results in backend state while sending compact views to the LLM."""

    _CONTROL_TOOL_NAMES = frozenset({
        "list_tool_groups",
        "load_tool_group",
        "unload_tool_group",
        "list_mcp_tools",
        "load_mcp_tools",
        "unload_mcp_tools",
    })
    _ARTIFACT_ACCESS_TOOL_NAMES = frozenset({
        "inspect_tool_artifact",
        "read_tool_artifact",
        "find_tool_artifact_text",
    })

    def __init__(self, serializer: PromptArtifactSerializer | None = None) -> None:
        self._serializer = serializer or PromptArtifactSerializer()
        self._evidence_serializer = serializer or PromptArtifactSerializer(
            budget_policy=PromptContextBudgetPolicy(
                max_payload_chars=16_000,
                max_tokens=8_000,
            )
        )

    def serialize(self, *, tool_name: str, tool_result: dict[str, Any]) -> str:
        """Return typed compact JSON for prompt injection."""
        return self.serialize_envelope(
            tool_name=tool_name,
            tool_result=tool_result,
        ).stable_json()

    def serialize_envelope(
        self,
        *,
        tool_name: str,
        tool_result: dict[str, Any],
    ) -> PromptArtifactEnvelope:
        """Return the typed envelope so callers can persist diagnostics."""
        if tool_name in self._ARTIFACT_ACCESS_TOOL_NAMES:
            # These backend-owned tools already return bounded payloads. Preserve their
            # existing result contract so artifact content remains consumable by the LLM.
            return self._serializer.serialize(
                artifact_type="tool_artifact_access",
                artifact=tool_result,
            )
        serializer = (
            self._serializer if tool_name in self._CONTROL_TOOL_NAMES else self._evidence_serializer
        )
        return serializer.serialize(
            artifact_type=(
                "tool_control" if tool_name in self._CONTROL_TOOL_NAMES else "evidence_summary"
            ),
            artifact={"tool_name": tool_name, **tool_result},
        )

    def diagnostics(self, *, envelope: PromptArtifactEnvelope) -> PromptArtifactDiagnostics:
        """Expose prompt artifact diagnostics without leaking raw tool payloads."""
        return self._serializer.diagnostics(envelope=envelope)
