"""Compact tool results before appending them to the LLM prompt."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.domain.chat.orchestration.prompt_artifact_serializer import (
    PromptArtifactDiagnostics,
    PromptArtifactEnvelope,
    PromptArtifactSerializer,
    PromptContextBudgetPolicy,
)

from ..settings import get_chat_settings
from .tool_output_policy import ToolOutputPolicy


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
        settings = get_chat_settings()
        self._optimized_outputs = settings.tool_output_policy_enabled
        self._serializer = serializer or PromptArtifactSerializer()
        self._evidence_serializer = serializer or PromptArtifactSerializer(
            budget_policy=PromptContextBudgetPolicy(
                max_payload_chars=16_000,
                max_tokens=8_000,
            )
        )
        self._mcp_serializer = serializer or PromptArtifactSerializer(
            budget_policy=PromptContextBudgetPolicy(max_payload_chars=10_000, max_tokens=5_000)
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
        if (
            self._optimized_outputs
            and tool_name in {"list_mcp_tools", "list_tool_groups"}
            and tool_result.get("ok")
        ):
            return ToolOutputPolicy().catalog_page(tool_name=tool_name, raw=tool_result)
        if tool_name in self._ARTIFACT_ACCESS_TOOL_NAMES:
            # These backend-owned tools already return bounded payloads. Preserve their
            # existing result contract so artifact content remains consumable by the LLM.
            envelope = self._serializer.serialize(
                artifact_type="tool_artifact_access",
                artifact=tool_result,
            )
            return (
                ToolOutputPolicy().apply(envelope, tool_result)
                if self._optimized_outputs
                else envelope
            )
        serializer = (
            self._serializer if tool_name in self._CONTROL_TOOL_NAMES else self._evidence_serializer
        )
        is_mcp = "remote_tool_name" in tool_result
        if is_mcp and self._optimized_outputs:
            serializer = self._mcp_serializer
            tool_result = dict(tool_result)
            handle = dict(tool_result.get("tool_artifact", {}))
            raw_result = tool_result.get("result")
            raw_result = raw_result if isinstance(raw_result, dict) else {}
            if "structuredContent" in raw_result:
                handle["json_pointer"] = "/result/structuredContent"
            elif "structured_content" in raw_result:
                handle["json_pointer"] = "/result/structured_content"
            elif raw_result.get("content"):
                handle["json_pointer"] = "/result/content"
            handle["read_hint"] = (
                "read_tool_artifact(format=json, json_pointer=..., fields=[...]); follow next_offset."
            )
            tool_result["tool_artifact"] = handle
        envelope = serializer.serialize(
            artifact_type=(
                "tool_control" if tool_name in self._CONTROL_TOOL_NAMES else "evidence_summary"
            ),
            artifact={"tool_name": tool_name, **tool_result},
        )
        return (
            ToolOutputPolicy(max_chars=12_000 if is_mcp else 16_000).apply(envelope, tool_result)
            if self._optimized_outputs
            else envelope
        )

    def diagnostics(self, *, envelope: PromptArtifactEnvelope) -> PromptArtifactDiagnostics:
        """Expose prompt artifact diagnostics without leaking raw tool payloads."""
        return self._serializer.diagnostics(envelope=envelope)
