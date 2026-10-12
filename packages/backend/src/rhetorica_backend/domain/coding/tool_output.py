"""Bound prompt growth while retaining complete owner-scoped tool receipts."""

import json
from typing import Any

from rhetorica_backend.domain.chat.llm.token_budget import LlmTokenEstimator


class CodingToolOutputSerializer:
    """One deterministic output budget; full receipts remain available through a reader."""

    def __init__(self, *, max_tokens: int = 4_000):
        self._max_tokens = max_tokens
        self._estimator = LlmTokenEstimator(None)

    @staticmethod
    def encode(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def project(result: dict[str, Any], *, call_id: str | None) -> dict[str, Any]:
        """Keep typed evidence visible even when verbose output needs a separate read."""
        receipt = result.get("dbt_receipt")
        if not isinstance(receipt, dict):
            return result
        projected = {
            key: value for key, value in result.items() if value is not None and value != ""
        }
        if receipt.get("command") == "list" and result.get("exit_code") == 0:
            try:
                nodes = [
                    json.loads(line)
                    for line in result.get("stdout", "").splitlines()
                    if line.strip()
                ]
            except (ValueError, TypeError):
                nodes = []
            if nodes and all(isinstance(node, dict) and "unique_id" in node for node in nodes):
                projected.pop("stdout", None)
                projected["selected_nodes"] = nodes
        if call_id:
            projected["tool_result"] = {"call_id": call_id}
        return projected

    def serialize(self, result: dict[str, Any], *, call_id: str | None) -> str:
        result = self.project(result, call_id=call_id)
        raw = self.encode(result)
        if self._estimator.count_text(raw) <= self._max_tokens:
            return raw
        envelope = {
            key: result[key]
            for key in (
                "operation",
                "exit_code",
                "status",
                "reason_code",
                "truncated",
                "duration_ms",
                "dbt_receipt",
                "candidate_digest",
                "offset",
                "next_offset",
                "total_chars",
            )
            if key in result
        }
        envelope.update({
            "prompt_truncated": True,
            "tool_result": {
                "call_id": call_id,
                "total_chars": result.get("tool_result", {}).get("total_chars", len(raw))
                if result.get("prompt_truncated") is True
                else len(raw),
            },
            "instruction": "Preview only. read_tool_result(call_id, offset) returns the saved JSON.",
            "preview_head": "",
            "preview_tail": "",
        })
        if isinstance(result.get("checks"), list):
            envelope["checks"] = [
                {k: check[k] for k in ("dbt_receipt", "exit_code", "tool_result") if k in check}
                for check in result["checks"]
            ]
        # Tiny history projections keep status and receipt identity; detailed evidence
        # is retained in the journal and in larger current-turn tool responses.
        if self._estimator.count_text(self.encode(envelope)) > self._max_tokens:
            envelope.pop("checks", None)
            receipt = envelope.pop("dbt_receipt", None)
            if receipt:
                envelope["status"] = receipt.get("status")
                envelope["command"] = receipt.get("command")
        # Preview useful content, not the receipt's JSON wrapper. Re-projecting an
        # already bounded receipt preserves its original head/tail and retrieval ID.
        fields = (
            ("preview_head", "preview_tail")
            if result.get("prompt_truncated")
            else ("stdout", "content", "diff", "stderr")
        )
        parts = [
            f"{key}:\n{result[key]}"
            for key in fields
            if isinstance(result.get(key), str) and result[key]
        ]
        preview = "\n".join(parts) if parts else raw
        # Binary search also includes JSON escaping and envelope overhead in the budget.
        low, high = 0, len(preview)
        while low < high:
            length = (low + high + 1) // 2
            head = (length + 1) // 2
            tail = length // 2
            candidate = {
                **envelope,
                "preview_head": preview[:head],
                "preview_tail": preview[-tail:] if tail else "",
            }
            if self._estimator.count_text(self.encode(candidate)) <= self._max_tokens:
                low = length
            else:
                high = length - 1
        envelope["preview_head"] = preview[: (low + 1) // 2]
        envelope["preview_tail"] = preview[-(low // 2) :] if low // 2 else ""
        return self.encode(envelope)
