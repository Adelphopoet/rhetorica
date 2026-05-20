"""Compact tool results before appending them to the LLM prompt."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.domain.chat.orchestration.prompt_artifact_serializer import (
    PromptArtifactDiagnostics,
    PromptArtifactEnvelope,
    PromptArtifactSerializer,
)


class ToolResultPromptSerializer:
    """Keeps full tool results in backend state while sending compact views to the LLM."""

    def __init__(self, serializer: PromptArtifactSerializer | None = None) -> None:
        self._serializer = serializer or PromptArtifactSerializer()

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
        return self._serializer.serialize(
            artifact_type="evidence_summary",
            artifact={"tool_name": tool_name, **tool_result},
        )

    def diagnostics(self, *, envelope: PromptArtifactEnvelope) -> PromptArtifactDiagnostics:
        """Expose prompt artifact diagnostics without leaking raw tool payloads."""
        return self._serializer.diagnostics(envelope=envelope)
