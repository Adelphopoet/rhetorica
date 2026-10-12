"""Immutable service events, separate from the SDK and user transcript."""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


def stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


@dataclass
class ContextProjection:
    events: list[dict[str, Any]] = field(default_factory=list)

    def append(self, items: list[Any], message: dict[str, Any]) -> None:
        event = {
            "offset": len(items),
            "anchor": stable_hash(items[-1]) if items else "",
            "message": message,
        }
        if event not in self.events:
            self.events.append(event)

    def project(self, items: list[Any]) -> list[Any]:
        result: list[Any] = []
        by_offset: dict[int, list[dict[str, Any]]] = {}
        for event in self.events:
            offset = event["offset"]
            if 0 <= offset <= len(items) and event["anchor"] == (
                stable_hash(items[offset - 1]) if offset else ""
            ):
                by_offset.setdefault(offset, []).append(event["message"])
        result.extend(by_offset.get(0, []))
        for offset, item in enumerate(items, 1):
            result.append(item)
            result.extend(by_offset.get(offset, []))
        return result


@dataclass
class ModelCallManifest:
    previous: dict[str, Any] | None = None

    def build(
        self,
        *,
        instructions: str,
        tools: list[dict[str, Any]],
        items: list[Any],
        estimated_tokens: int,
        budget_tokens: int,
        reason: str | None,
        instructions_version: str | None = None,
    ) -> dict[str, Any]:
        hashes = [stable_hash(item) for item in items]
        current = {
            "version": "context-v2",
            "instructions_hash": stable_hash(instructions),
            "tools_hash": stable_hash(tools),
            "item_hashes": hashes,
        }
        previous = self.previous
        prefix = "initial"
        if previous:
            if current["instructions_hash"] != previous["instructions_hash"]:
                prefix = "instructions_changed"
            elif current["tools_hash"] != previous["tools_hash"]:
                prefix = "tool_schemas_changed"
            elif hashes[: len(previous["item_hashes"])] == previous["item_hashes"]:
                prefix = "append_only"
            else:
                prefix = reason or "history_changed"
        self.previous = current
        return {key: value for key, value in current.items() if key != "item_hashes"} | {
            "history_hash": stable_hash(hashes),
            "instructions_version": instructions_version,
            "prefix_change": prefix,
            "context_action": reason,
            "input_items": len(items),
            "instructions_chars": len(instructions),
            "tools_chars": len(json.dumps(tools, ensure_ascii=False, separators=(",", ":"))),
            "history_chars": len(json.dumps(
                items, default=str, ensure_ascii=False, separators=(",", ":")
            )),
            "estimated_input_tokens": estimated_tokens,
            "input_budget_tokens": budget_tokens,
        }
