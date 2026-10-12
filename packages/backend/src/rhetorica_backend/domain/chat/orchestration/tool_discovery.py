"""Deterministic bounded catalog lookup for the opt-in SDK dispatch surface."""

import json
import re
from typing import Any

from .context_projection import stable_hash


class ToolDiscoveryPolicy:
    def search(
        self, definitions: list[dict[str, Any]], *, query: str = "", offset: int = 0, limit: int = 5
    ) -> dict[str, Any]:
        terms = set(re.findall(r"[^\W_]+", query.casefold()))
        matches = []
        for definition in definitions:
            function = definition["function"]
            name_terms = set(re.findall(r"[^\W_]+", function["name"].casefold()))
            haystack = set(
                re.findall(
                    r"[^\W_]+",
                    (function["name"] + " " + function.get("description", "")).casefold(),
                )
            )
            overlap = terms & haystack
            if terms and not overlap and query != function["name"]:
                continue
            # Lexical ranking only; no model invocation or inferred tool selection.
            rank = (query == function["name"], len(overlap), len(terms & name_terms))
            matches.append((rank, function))
        matches.sort(key=lambda item: (*(-int(value) for value in item[0]), item[1]["name"]))
        selected = matches[max(0, offset) : max(0, offset) + max(1, min(limit, 10))]
        page: list[dict[str, Any]] = []
        for _, function in selected:
            entry = {
                "tool_id": function["name"],
                "description": function.get("description", ""),
                "parameters": function["parameters"],
                "schema_hash": stable_hash(function["parameters"]),
            }
            if len(json.dumps([*page, entry], ensure_ascii=False)) > 12000:
                if not page:
                    page.append({
                        "tool_id": function["name"],
                        "error": "schema_exceeds_discovery_page_budget",
                    })
                break
            page.append(entry)
        end = max(0, offset) + len(page)
        return {
            "catalog_version": stable_hash(
                sorted(definitions, key=lambda item: item["function"]["name"])
            ),
            "tools": page,
            "total": len(matches),
            "next_offset": end if end < len(matches) else None,
        }


DISCOVER_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Exact tool ID or short keywords in its name/description, ranked by lexical overlap. Empty lists the catalog. Load mcp_connectors before discovering MCP catalog tools.",
        },
        "offset": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
    },
    "additionalProperties": False,
}
INVOKE_SCHEMA = {
    "type": "object",
    "properties": {
        "tool_id": {"type": "string"},
        "arguments": {"type": "object", "additionalProperties": True},
    },
    "required": ["tool_id", "arguments"],
    "additionalProperties": False,
}
