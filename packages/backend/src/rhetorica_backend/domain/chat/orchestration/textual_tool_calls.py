"""Helpers for hidden textual tool-call fallback."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

THINKING_TAG_PATTERN = re.compile(
    r"<(think|thinking)>(.*?)</\1>",
    flags=re.IGNORECASE | re.DOTALL,
)
TOOL_CALL_TAG_PATTERN = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    flags=re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class TextualToolCall:
    """Tool call embedded in assistant text."""

    tool_name: str
    arguments: dict[str, Any]


def _tool_call_from_payload(payload: object) -> TextualToolCall | None:
    if isinstance(payload, list):
        if not payload:
            return None
        payload = payload[0]

    if not isinstance(payload, dict):
        return None

    tool_name = payload.get("tool_name")
    arguments = payload.get("arguments")

    if not isinstance(tool_name, str) or not tool_name.strip():
        alt_name = payload.get("name")
        alt_arguments = payload.get("parameters")
        if isinstance(alt_name, str) and alt_name.strip():
            tool_name = alt_name
            arguments = alt_arguments

    if not isinstance(tool_name, str) or not tool_name.strip():
        return None
    if not isinstance(arguments, dict):
        arguments = {}

    return TextualToolCall(
        tool_name=tool_name.strip(),
        arguments=arguments,
    )


def extract_textual_tool_call(
    content: str,
) -> tuple[TextualToolCall | None, str]:
    """Extract the first hidden tool-call tag and return cleaned content."""

    textual_tool_call: TextualToolCall | None = None

    def _replace(match: re.Match[str]) -> str:
        nonlocal textual_tool_call
        if textual_tool_call is not None:
            return " "

        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            return " "

        tool_call = _tool_call_from_payload(parsed)
        if tool_call is None:
            return " "

        textual_tool_call = tool_call
        return " "

    visible_content = TOOL_CALL_TAG_PATTERN.sub(_replace, content)
    if textual_tool_call is not None:
        return textual_tool_call, visible_content

    explicit_candidate = THINKING_TAG_PATTERN.sub(" ", visible_content).strip()
    if explicit_candidate:
        try:
            parsed_candidate = json.loads(explicit_candidate)
        except json.JSONDecodeError:
            parsed_candidate = None
        tool_call = _tool_call_from_payload(parsed_candidate)
        if tool_call is not None:
            return tool_call, ""

    return textual_tool_call, visible_content
