"""Structured protocol-repair helpers for tool orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProtocolRepairDecision:
    """Structured next-action decision after a completed assistant turn."""

    next_action: str
    answer_markdown: str
    uncertainty_flags: list[str]
    tool_name: str | None
    arguments: dict[str, Any]

    @property
    def requests_tool_call(self) -> bool:
        """Whether protocol repair decided to continue via a tool."""
        return self.next_action == "tool_call" and self.tool_name is not None

    @property
    def requests_finalization(self) -> bool:
        """Whether protocol repair decided to finalize the turn."""
        return self.next_action in {"final_answer", "needs_user_clarification"}


def protocol_repair_json_schema(tool_names: list[str]) -> dict[str, Any]:
    """JSON schema for explicit next-action repair turn."""

    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "next_action",
            "answer_markdown",
            "uncertainty_flags",
            "tool_request",
        ],
        "properties": {
            "next_action": {
                "type": "string",
                "enum": [
                    "tool_call",
                    "final_answer",
                    "needs_user_clarification",
                ],
            },
            "answer_markdown": {"type": "string"},
            "uncertainty_flags": {
                "type": "array",
                "items": {"type": "string"},
            },
            "tool_request": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tool_name", "arguments"],
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "enum": ["", *tool_names],
                    },
                    "arguments": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
            },
        },
    }


def parse_protocol_repair_response(
    raw_content: str,
    *,
    tool_names: set[str],
) -> ProtocolRepairDecision | None:
    """Parse structured repair response."""

    parsed = _parse_protocol_repair_payload(raw_content)
    if parsed is None:
        return None

    next_action = parsed.get("next_action")
    answer_markdown = parsed.get("answer_markdown")
    uncertainty_flags = parsed.get("uncertainty_flags")
    tool_request = parsed.get("tool_request")

    if next_action not in {
        "tool_call",
        "final_answer",
        "needs_user_clarification",
    }:
        return None
    if not isinstance(answer_markdown, str):
        return None
    if not isinstance(uncertainty_flags, list) or not all(
        isinstance(item, str) for item in uncertainty_flags
    ):
        return None
    if not isinstance(tool_request, dict):
        return None

    tool_name = tool_request.get("tool_name")
    arguments = tool_request.get("arguments")
    if not isinstance(tool_name, str):
        return None
    if tool_name and tool_name not in tool_names:
        return None
    if not isinstance(arguments, dict):
        return None

    return ProtocolRepairDecision(
        next_action=next_action,
        answer_markdown=answer_markdown,
        uncertainty_flags=uncertainty_flags,
        tool_name=tool_name or None,
        arguments=arguments,
    )


def _parse_protocol_repair_payload(raw_content: str) -> dict[str, Any] | None:
    raw = raw_content.strip()
    if raw.startswith("```"):
        lines = [
            line
            for line in raw.splitlines()
            if not line.strip().startswith("```")
        ]
        raw = "\n".join(lines).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        left = raw.find("{")
        right = raw.rfind("}")
        if left == -1 or right == -1 or right <= left:
            return None
        try:
            parsed = json.loads(raw[left : right + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    return parsed
