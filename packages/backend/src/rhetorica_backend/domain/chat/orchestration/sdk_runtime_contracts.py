"""Shared SDK lifecycle contracts and exact turn counter."""

from typing import Any

from ..llm.prompt_builder import PromptBuilder
from .context_projection import ContextProjection, ModelCallManifest
from .loop_limit_awareness import should_force_finalization


class AgentsSdkCapabilityError(RuntimeError):
    """Configured connector cannot satisfy the required Agents SDK contract."""


class _TransportStop(Exception):
    def __init__(self, payload: Any) -> None:
        super().__init__("transport requested runtime stop")
        self.payload = payload


class _SdkCompactionRestart(Exception):
    """Restart the public SDK run from a compacted, self-contained checkpoint."""

    def __init__(self, checkpoint: dict[str, Any], *, items: list[Any] | None = None) -> None:
        super().__init__("restart Agents SDK run from compacted checkpoint")
        self.checkpoint = checkpoint
        self.items = items if items is not None else [checkpoint]


class _SdkTurnControl:
    """Exact SDK model-call counter shared by hooks, tools and input filtering."""

    def __init__(self, *, max_turns: int) -> None:
        self.max_turns = min(max_turns, 100)
        self.completed_turns = 0
        self.pending_tool_calls = 0
        self.compaction_calls = 0
        self.compacted_boundaries: set[str] = set()
        self.projection = ContextProjection()
        self.manifest = ModelCallManifest()
        self.latest_manifest: dict[str, Any] | None = None
        self.prefix_change_reason: str | None = None
        self.time_context = PromptBuilder.build_time_context()
        self.noticed_steps: set[int] = set()
        self.tool_mode = "direct"
        self.estimate_ratio = 1.0

    @property
    def current_iteration(self) -> int:
        return self.completed_turns

    @property
    def tools_enabled(self) -> bool:
        return not should_force_finalization(
            completed_turns=self.completed_turns - (1 if self.pending_tool_calls else 0),
            max_iterations=self.max_turns,
        )
