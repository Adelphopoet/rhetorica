"""Registry for orchestration tool capabilities."""

from __future__ import annotations

from collections.abc import Iterable

from rhetorica_backend.domain.chat.orchestration.tool_capability import ToolCapability


class ToolCapabilityRegistry:
    """Resolve public tool names to executable capability objects."""

    def __init__(self, capabilities: Iterable[ToolCapability]) -> None:
        self._capabilities = {capability.name: capability for capability in capabilities}

    def get(self, tool_name: str) -> ToolCapability | None:
        return self._capabilities.get(tool_name)

    def names(self) -> list[str]:
        return list(self._capabilities)
