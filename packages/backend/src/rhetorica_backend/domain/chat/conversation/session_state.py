"""Operational conversation state and compact runtime context restoration.

User intent stays in the recent message history.  This module deliberately does
not infer business semantics from user text or generated SQL: those heuristics
can turn an assistant detour into durable, misleading memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_RESET_PATTERNS = (
    "начнем заново",
    "начнём заново",
    "new chat",
    "start over",
    "reset context",
)

_OPERATIONAL_TRANSITIONS = {
    "last_successful_execution_updated",
    "reset_by_user_command",
    "state_revision_incremented",
}


@dataclass
class ToolActivationManifest:
    """Exact model-selected tool activations persisted between turns."""

    group_names: list[str] = field(default_factory=list)
    mcp_remote_tool_names: dict[str, list[str]] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "group_names": list(self.group_names),
            "mcp_tools": [
                {
                    "connector_name": connector_name,
                    "remote_tool_names": list(remote_tool_names),
                }
                for connector_name, remote_tool_names in self.mcp_remote_tool_names.items()
            ],
        }


@dataclass
class OperationalConversationState:
    """Durable execution bookkeeping that carries no inferred user intent."""

    last_successful_execution_id: str | None = None
    state_revision: int = 0
    transitions: list[str] = field(default_factory=list)
    tool_activation: ToolActivationManifest | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "last_successful_execution_id": self.last_successful_execution_id,
            "state_revision": self.state_revision,
            "transitions": list(self.transitions),
        }
        if self.tool_activation is not None:
            payload["tool_activation"] = self.tool_activation.to_payload()
        return payload


def build_operational_state_from_messages(
    messages: list[Any],
) -> OperationalConversationState:
    """Extract only execution bookkeeping from a completed turn."""
    state = OperationalConversationState()
    for message in messages:
        execution_id = _optional_str(getattr(message, "execution_id", None))
        warnings = getattr(message, "warnings", None)
        if execution_id and not _execution_failed(warnings):
            state.last_successful_execution_id = execution_id
            state.transitions.append("last_successful_execution_updated")
        capability_trace = getattr(message, "capability_trace", None)
        if not isinstance(capability_trace, dict):
            continue
        runtime_context = capability_trace.get("runtime_context")
        if isinstance(runtime_context, dict) and "tool_activation" in runtime_context:
            state.tool_activation = tool_activation_from_payload(
                runtime_context.get("tool_activation")
            )
    return state


def operational_state_from_payload(
    payload: dict[str, Any] | None,
) -> OperationalConversationState:
    """Read operational fields while intentionally dropping legacy semantic keys."""
    if not isinstance(payload, dict):
        return OperationalConversationState()
    return OperationalConversationState(
        last_successful_execution_id=_optional_str(payload.get("last_successful_execution_id")),
        state_revision=(
            payload["state_revision"] if isinstance(payload.get("state_revision"), int) else 0
        ),
        transitions=[
            transition
            for transition in _string_items(payload.get("transitions"))
            if transition in _OPERATIONAL_TRANSITIONS
        ],
        tool_activation=(
            tool_activation_from_payload(payload.get("tool_activation"))
            if "tool_activation" in payload
            else None
        ),
    )


def merge_operational_states(
    *,
    persisted: OperationalConversationState,
    update: OperationalConversationState,
    increment_revision: bool = True,
) -> OperationalConversationState:
    """Merge bounded operational bookkeeping without semantic accumulation."""
    return OperationalConversationState(
        last_successful_execution_id=(
            update.last_successful_execution_id or persisted.last_successful_execution_id
        ),
        state_revision=(
            persisted.state_revision + 1 if increment_revision else persisted.state_revision
        ),
        transitions=[
            *persisted.transitions[-20:],
            *update.transitions,
            *(["state_revision_incremented"] if increment_revision else []),
        ],
        tool_activation=(
            update.tool_activation
            if update.tool_activation is not None
            else persisted.tool_activation
        ),
    )


def tool_activation_from_payload(payload: Any) -> ToolActivationManifest:
    """Parse exact names only; aliases, fuzzy matching, and intent inference are forbidden."""
    if not isinstance(payload, dict):
        return ToolActivationManifest()
    mcp_remote_tool_names: dict[str, list[str]] = {}
    mcp_tools = payload.get("mcp_tools")
    if isinstance(mcp_tools, list):
        for item in mcp_tools:
            if not isinstance(item, dict):
                continue
            connector_name = _optional_str(item.get("connector_name"))
            if connector_name is None:
                continue
            remote_tool_names = _merge_unique(
                mcp_remote_tool_names.get(connector_name, []),
                _string_items(item.get("remote_tool_names")),
            )
            if remote_tool_names:
                mcp_remote_tool_names[connector_name] = remote_tool_names
    return ToolActivationManifest(
        group_names=_merge_unique([], _string_items(payload.get("group_names"))),
        mcp_remote_tool_names=mcp_remote_tool_names,
    )


def contains_reset_command(content: str) -> bool:
    """Return whether the user explicitly resets conversation working state."""
    normalized = content.strip().lower()
    return any(pattern in normalized for pattern in _RESET_PATTERNS)


def _execution_failed(warnings: Any) -> bool:
    return isinstance(warnings, list) and "sql_execution_failed" in warnings


def _merge_unique(first: list[str], second: list[str]) -> list[str]:
    merged: list[str] = []
    for item in [*first, *second]:
        if item and item not in merged:
            merged.append(item)
    return merged


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
