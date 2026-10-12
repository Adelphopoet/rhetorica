"""Explicit capabilities of a connection, independent of its HTTP endpoint."""

from typing import Any, Literal

from openai.types.shared.reasoning_effort import ReasoningEffort
from pydantic import BaseModel, ConfigDict


class ProviderRuntimeCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime_role: Literal["user", "developer", "system"] = "user"
    requires_last_user: bool = True
    reasoning_replay: bool | None = None
    parallel_tool_calls: bool = False
    compaction_reasoning_effort: ReasoningEffort = None
    usage_format: Literal["openai", "deepseek", "anthropic"] = "openai"

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "ProviderRuntimeCapabilities":
        role = settings.get("runtime_context_role", "user")
        return cls(
            runtime_role=role,
            requires_last_user=settings.get("runtime_requires_last_user", role == "user"),
            reasoning_replay=settings.get(
                "runtime_reasoning_replay",
                True if (
                    settings.get("chat_template_enable_thinking") is True
                    or settings.get("thinking") is True
                ) else None,
            ),
            parallel_tool_calls=settings.get("runtime_parallel_tool_calls", False),
            compaction_reasoning_effort=settings.get("runtime_compaction_reasoning_effort"),
            usage_format=settings.get("runtime_usage_format", "openai"),
        )
