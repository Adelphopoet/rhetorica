"""Hard model-facing output bounds, applied after full artifact registration."""

import json
from dataclasses import dataclass
from typing import Any

from ..llm.token_budget import LlmTokenEstimator
from .prompt_artifact_serializer import PromptArtifactEnvelope


@dataclass(frozen=True)
class ToolOutputPolicy:
    """Bound the complete serialized envelope, including escaping and metadata."""

    max_chars: int = 16_000
    catalog_description_chars: int = 180

    def catalog_page(self, *, tool_name: str, raw: dict[str, Any]) -> PromptArtifactEnvelope:
        """Keep a bounded ordered page of exact names, with explicit continuation."""
        is_mcp = tool_name == "list_mcp_tools"
        key = "tools" if is_mcp else "groups"
        source = raw.get(key, [])
        source = source if isinstance(source, list) else []
        fields = (
            ("remote_tool_name", "exposed_tool_name", "description")
            if is_mcp
            else ("group_name", "description", "tool_count")
        )
        entries = [
            {
                field: str(item.get(field) or "")[: self.catalog_description_chars]
                if field == "description"
                else item.get(field)
                for field in fields
            }
            for item in source
        ]
        descriptions_truncated = any(
            len(str(item.get("description") or "")) > self.catalog_description_chars
            for item in source
        )
        offset = raw.get("offset", 0) if is_mcp else 0
        total = raw.get("total", offset + len(entries)) if is_mcp else len(entries)
        handle = raw.get("tool_artifact", {})

        def page(count: int) -> PromptArtifactEnvelope:
            payload: dict[str, Any] = {
                "ok": True,
                "tool_name": tool_name,
                "tool_artifact": handle,
                key: entries[:count],
            }
            if is_mcp:
                next_offset = offset + count
                payload.update(
                    connector_name=raw.get("connector_name"),
                    offset=offset,
                    limit=count,
                    total=total,
                    has_more=next_offset < total,
                    next_offset=next_offset if next_offset < total else None,
                    next_action_hint="Call list_mcp_tools with the same connector_name and next_offset as offset.",
                )
            else:
                payload.update(group_count=total)
                if count < total:
                    payload["catalog_page"] = {
                        "offset": 0,
                        "next_offset": count,
                        "total": total,
                        "json_pointer": "/groups",
                        "fields": list(fields),
                        "next_action_hint": "Read the remaining groups from tool_artifact with read_tool_artifact(format=json, json_pointer=/groups, offset=next_offset, fields=...).",
                    }
            return PromptArtifactEnvelope(
                artifact_type="tool_control",
                compact_encoding=True,
                compact_payload=payload,
                token_estimate=0,
                char_estimate=0,
                truncated=count < len(entries) or descriptions_truncated,
                truncation_flags=(
                    (["catalog_page_bounded"] if count < len(entries) else [])
                    + (["catalog_descriptions_truncated"] if descriptions_truncated else [])
                ),
            )

        # Fit the complete envelope without cutting identifiers or reordering names.
        low, high = 0, len(entries)
        while low < high:
            middle = (low + high + 1) // 2
            if len(page(middle).stable_json()) <= self.max_chars:
                low = middle
            else:
                high = middle - 1
        envelope = page(low)
        if low == 0 and entries:
            # A single pathological identifier cannot be split safely. The exact
            # original catalog remains addressable, without a non-advancing page.
            envelope = envelope.model_copy(
                update={
                    "compact_payload": {
                        "ok": True,
                        "tool_name": tool_name,
                        "tool_artifact": handle,
                        "json_pointer": "/" + key,
                        "next_action_hint": "Catalog entry exceeds the output budget. Read exact identifier fields from tool_artifact.",
                    }
                }
            )
        bounded = self.apply(envelope, raw)
        return bounded.model_copy(
            update={
                "token_estimate": LlmTokenEstimator(None).count_json(bounded.compact_payload),
                "char_estimate": len(bounded.stable_json()),
            }
        )

    def apply(
        self, envelope: PromptArtifactEnvelope, raw: dict[str, Any]
    ) -> PromptArtifactEnvelope:
        if len(envelope.stable_json()) <= self.max_chars:
            return envelope
        handle = raw.get("tool_artifact")
        if not isinstance(handle, dict):
            handle = {"artifact_id": raw["artifact_id"]} if raw.get("artifact_id") else {}
        # Keep an exact retrievable handle even if a deeply nested MCP preview
        # exceeds the serializer's budget after JSON escaping.
        payload = {
            "ok": raw.get("ok", True),
            "tool_artifact": {
                key: handle[key]
                for key in ("artifact_id", "durable", "durable_reason")
                if key in handle
            },
            "next_action_hint": "Inspect the artifact, then read selected fields or a page. Preview omitted due to size.",
        }
        for key in (
            "tool_name",
            "execution_id",
            "execution_status",
            "result_state",
            "row_count",
            "rows_truncated",
            "prompt_rows_returned",
            "error_code",
            "error_category",
            "retryable",
            "retry_recommended",
            "can_continue",
        ):
            if key in raw and isinstance(raw[key], str | bool | int):
                payload[key] = raw[key][:200] if isinstance(raw[key], str) else raw[key]
        return envelope.model_copy(
            update={
                "compact_payload": payload,
                "token_estimate": LlmTokenEstimator(None).count_json(payload),
                "char_estimate": len(json.dumps(payload, ensure_ascii=False)),
                "truncated": True,
                "truncation_flags": ["tool_output_hard_limit"],
            }
        )
