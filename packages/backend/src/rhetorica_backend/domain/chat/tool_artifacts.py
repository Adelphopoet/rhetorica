"""Conversation-scoped retention contracts for raw tool result artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class ConversationToolArtifactRecord:
    """One bounded raw tool result available to later turns in a conversation."""

    id: str
    conversation_id: str
    artifact_id: str
    tool_name: str
    arguments: dict[str, Any]
    payload: dict[str, Any]
    content_sha256: str
    source_char_count: int
    observed_at: datetime
    expires_at: datetime
    created_at: datetime


class ConversationToolArtifactStore(Protocol):
    """Persistence boundary for temporary conversation tool artifacts."""

    def save_tool_artifact(
        self,
        *,
        conversation_id: str,
        artifact_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        payload: dict[str, Any],
        content_sha256: str,
        source_char_count: int,
        observed_at: datetime,
        expires_at: datetime,
        max_records: int,
    ) -> ConversationToolArtifactRecord: ...

    def get_tool_artifact(
        self,
        *,
        conversation_id: str,
        artifact_id: str,
    ) -> ConversationToolArtifactRecord | None: ...

    def list_tool_artifacts(
        self,
        *,
        conversation_id: str,
        limit: int,
    ) -> list[ConversationToolArtifactRecord]: ...
