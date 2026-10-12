"""Agent-owned, conversation-scoped tool evidence contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolEvidenceRecord:
    """One model-owned note backed by an exact tool result artifact."""

    id: str
    conversation_id: str
    evidence_key: str
    tool_name: str
    arguments: dict[str, Any]
    payload: dict[str, Any]
    note: str
    content_sha256: str
    source_char_count: int
    observed_at: datetime
    created_at: datetime
    updated_at: datetime


class ToolEvidenceStore(Protocol):
    """Persistence boundary available to the agent memory tools."""

    def upsert_tool_evidence(
        self,
        *,
        conversation_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        payload: dict[str, Any],
        note: str,
        content_sha256: str,
        source_char_count: int,
        observed_at: datetime,
    ) -> ToolEvidenceRecord: ...

    def get_tool_evidence(
        self,
        *,
        conversation_id: str,
        evidence_key: str,
    ) -> ToolEvidenceRecord | None: ...

    def delete_tool_evidence(
        self,
        *,
        conversation_id: str,
        evidence_key: str,
    ) -> bool: ...


def build_tool_evidence_key(*, tool_name: str, arguments: dict[str, Any]) -> str:
    """Build a stable replacement key from exact, canonical tool arguments."""
    canonical_arguments = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    signature = f"{tool_name}\n{canonical_arguments}".encode()
    return hashlib.sha256(signature).hexdigest()
