"""Protocol-aware context window operations; recent tool batches stay verbatim."""

import copy
import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from rhetorica_backend.contracts.workspace_skills import WorkspaceSkillVersionBinding


class ContextCheckpointV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = "v2"
    goal: str = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    next_step: str = ""
    artifact_references: list[str] = Field(default_factory=list)
    ledger_artifact_id: str | None = None
    turn_time_context: str | None = None
    workspace_skill_version_bindings: list[WorkspaceSkillVersionBinding] = Field(
        default_factory=list
    )
    workspace_skill_read_addresses: list[dict[str, object]] = Field(default_factory=list)


@dataclass(frozen=True)
class ContextWindow:
    """Locate completed batches by call ID; never cut through a pending batch."""

    protected_batches: int = 3
    protected_tool_call_ids: frozenset[str] = frozenset()

    @staticmethod
    def _assistant_batch_start(items: list[dict[str, Any]], index: int) -> int:
        while index:
            previous = items[index - 1]
            if previous.get("type") != "reasoning" and previous.get("role") != "assistant":
                break
            index -= 1
        return index

    def _protected_indexes(self, items: list[dict[str, Any]]) -> set[int]:
        protected: set[int] = set()
        pending: set[str] = set()
        start = 0
        retain = False
        for index, item in enumerate(items):
            if item.get("type") == "function_call":
                if not pending:
                    start = self._assistant_batch_start(items, index)
                call_id = item.get("call_id")
                if not isinstance(call_id, str):
                    continue
                pending.add(call_id)
                retain = retain or call_id in self.protected_tool_call_ids
            elif item.get("type") == "function_call_output":
                pending.discard(item.get("call_id"))
                if not pending:
                    if retain:
                        protected.update(range(start, index + 1))
                    retain = False
        return protected

    def boundary(self, items: list[dict[str, Any]]) -> int:
        pending: set[str] = set()
        batch_start: int | None = None
        completed: list[int] = []
        for index, item in enumerate(items):
            if item.get("type") == "function_call":
                call_id = item.get("call_id")
                if not isinstance(call_id, str) or call_id in pending:
                    return 0
                if batch_start is None:
                    # Reasoning belongs to the following assistant/tool batch.
                    batch_start = self._assistant_batch_start(items, index)
                pending.add(call_id)
            elif item.get("type") == "function_call_output":
                call_id = item.get("call_id")
                if call_id not in pending:
                    return 0
                pending.remove(call_id)
                if not pending:
                    completed.append(batch_start if batch_start is not None else index)
                    batch_start = None
        if len(completed) < self.protected_batches:
            return (
                completed[0]
                if completed
                else (batch_start if batch_start is not None else len(items))
            )
        return completed[-self.protected_batches]

    def split(
        self, items: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        boundary = self.boundary(items)
        protected_indexes = self._protected_indexes(items)
        last_user = next(
            (
                i
                for i in range(len(items) - 1, -1, -1)
                if items[i].get("role") == "user"
                and not str(items[i].get("content", "")).startswith("<runtime_context>")
            ),
            -1,
        )
        # A notice prepared for the upcoming call has not been seen by the model
        # yet. Retain terminal service events even when there are no tool batches.
        service_tail = len(items)
        while service_tail and str(items[service_tail - 1].get("content", "")).startswith(
            "<runtime_context>"
        ):
            service_tail -= 1
        protected = [
            item
            for i, item in enumerate(items[:boundary])
            if i == last_user
            or i >= service_tail
            or i in protected_indexes
            or item.get("role") in {"system", "developer"}
        ]
        prefix = [
            item
            for i, item in enumerate(items[:boundary])
            if i != last_user
            and i < service_tail
            and i not in protected_indexes
            and item.get("role") not in {"system", "developer"}
        ]
        return prefix, [*protected, *items[boundary:]]

    def trim(self, items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        result = copy.deepcopy(items)
        trimmed = 0
        protected_indexes = self._protected_indexes(items)
        for index, item in enumerate(result[: self.boundary(items)]):
            if index in protected_indexes:
                continue
            output = item.get("output")
            if (
                item.get("type") != "function_call_output"
                or not isinstance(output, str)
                or len(output) < 2000
            ):
                continue
            try:
                envelope = json.loads(output)
                payload = envelope.get("compact_payload", envelope)
                handle = payload.get("tool_artifact")
                if not isinstance(handle, dict) or not handle.get("artifact_id"):
                    continue
            except (ValueError, AttributeError):
                continue
            item["output"] = json.dumps(
                {
                    "ok": payload.get("ok", True),
                    "tool_artifact": handle,
                    "truncated": True,
                    "reason": "context_window_trim",
                    "next_action_hint": "Read artifact fields for exact evidence.",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            trimmed += 1
        return result, trimmed
