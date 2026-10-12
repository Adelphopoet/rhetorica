"""Conversation management service - handles conversation history and context."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from rhetorica_backend.domain.chat.conversation.session_state import (
    build_operational_state_from_messages,
    contains_reset_command,
    merge_operational_states,
    operational_state_from_payload,
)
from rhetorica_backend.domain.chat.token_usage import ConversationTokenUsage
from rhetorica_backend.domain.chat.tool_artifacts import ConversationToolArtifactRecord
from rhetorica_backend.domain.chat.tool_evidence import ToolEvidenceRecord
from rhetorica_backend.domain.files.ports import ArtifactRepository
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository


class ConversationManager:
    """Manages conversation lifecycle and history."""

    def __init__(
        self,
        chat_repository: ChatRepository | None = None,
        current_user_id: str | None = None,
        artifact_repository: ArtifactRepository | None = None,
        agent_session_store: Any | None = None,
    ):
        self._chat_repository = chat_repository
        self._current_user_id = current_user_id
        self._artifact_repository = artifact_repository
        self._agent_session_store = agent_session_store
        self._logger = logging.getLogger(__name__)

    def attach_files_to_message(
        self,
        *,
        artifact_ids: list[str],
        message_id: str,
        conversation_id: str,
        relation: str,
    ) -> list[Any]:
        if not artifact_ids:
            return []
        if self._artifact_repository is None or self._current_user_id is None:
            raise RuntimeError("file_processing_unavailable")
        return self._artifact_repository.attach_to_message(
            artifact_ids=artifact_ids,
            message_id=message_id,
            conversation_id=conversation_id,
            owner_user_id=self._current_user_id,
            relation=relation,
        )

    def create_conversation(
        self,
        workspace_id: str,
        title: str,
        agent_id: str | None = None,
    ) -> Any:
        """
        Create new conversation.

        Args:
            workspace_id: Workspace identifier.
            title: Conversation title.

        Returns:
            Created conversation object.
        """
        if self._chat_repository is None:
            self._logger.warning("Chat repository not available, cannot create conversation")
            return None

        return self._chat_repository.create_conversation(
            workspace_id=workspace_id,
            title=title,
            owner_user_id=self._current_user_id,
            agent_id=agent_id,
        )

    def list_source_message_ids(
        self,
        *,
        conversation_id: str,
        source_provider: str,
    ) -> set[str]:
        if self._chat_repository is None:
            return set()
        method = getattr(self._chat_repository, "list_source_message_ids", None)
        if not callable(method):
            return set()
        return method(
            conversation_id=conversation_id,
            source_provider=source_provider,
            owner_user_id=self._current_user_id,
        )

    def get_conversation_with_messages(
        self,
        conversation_id: str,
        message_limit: int = 20,
    ) -> Any | None:
        """
        Get conversation with message history.

        Args:
            conversation_id: Conversation identifier.
            message_limit: Maximum number of messages to retrieve.

        Returns:
            Conversation with messages or None if not found.
        """
        if self._chat_repository is None:
            self._logger.warning("Chat repository not available, cannot get conversation")
            return None

        return self._chat_repository.get_conversation_with_messages(
            conversation_id=conversation_id,
            message_limit=message_limit,
            owner_user_id=self._current_user_id,
        )

    def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        generated_sql: str | None = None,
        execution_id: str | None = None,
        context_tables: list[str] | None = None,
        metadata_sources: list[str] | None = None,
        claims: list[dict[str, object]] | None = None,
        evidence: list[dict[str, object]] | None = None,
        claim_evidence_links: list[dict[str, object]] | None = None,
        uncertainty_flags: list[str] | None = None,
        capability_trace: dict[str, object] | None = None,
        optimization_trace: dict[str, object] | None = None,
        selected_connector: str | None = None,
        analysis_document: dict[str, object] | None = None,
        visualization_spec: dict[str, object] | None = None,
        warnings: list[str] | None = None,
        agent_id: str | None = None,
        source_provider: str | None = None,
        source_message_id: str | None = None,
    ) -> Any | None:
        """
        Save message to conversation.

        Args:
            conversation_id: Conversation identifier.
            role: Message role (user/assistant).
            content: Message content.
            generated_sql: Generated SQL if any.
            execution_id: Execution ID if SQL was executed.
            context_tables: Context tables referenced.
            metadata_sources: Metadata sources used.
            selected_connector: Selected database connector.
            warnings: Any warnings generated.
        """
        if self._chat_repository is None:
            self._logger.warning("Chat repository not available, cannot save message")
            return None

        return self._chat_repository.save_message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            generated_sql=generated_sql,
            execution_id=execution_id,
            context_tables=context_tables or [],
            metadata_sources=metadata_sources or [],
            claims=claims or [],
            evidence=evidence or [],
            claim_evidence_links=claim_evidence_links or [],
            uncertainty_flags=uncertainty_flags or [],
            capability_trace=capability_trace or {},
            optimization_trace=optimization_trace or {},
            selected_connector=selected_connector,
            analysis_document=analysis_document,
            visualization_spec=visualization_spec,
            warnings=warnings or [],
            owner_user_id=self._current_user_id,
            agent_id=agent_id,
            source_provider=source_provider,
            source_message_id=source_message_id,
        )

    def save_answer_trace(
        self,
        *,
        assistant_message_id: str,
        conversation_id: str,
        trace_id: str,
        tool_call_count: int = 0,
        tool_error_count: int = 0,
    ) -> None:
        if self._chat_repository is None:
            return
        self._chat_repository.save_answer_trace(
            assistant_message_id=assistant_message_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            tool_call_count=tool_call_count,
            tool_error_count=tool_error_count,
        )

    def reset_conversation_state(self, conversation_id: str) -> dict[str, object]:
        """Clear operational state and agent-owned evidence without deleting history."""
        if self._chat_repository is None:
            return {}
        state = self._chat_repository.update_conversation_state(
            conversation_id=conversation_id,
            conversation_state={
                "state_revision": 0,
                "transitions": ["reset_by_user_command"],
            },
            owner_user_id=self._current_user_id,
        )
        self._clear_tool_memory(conversation_id)
        return state

    def get_conversation_state(self, conversation_id: str) -> dict[str, Any]:
        """Load compact persisted conversation state."""
        if self._chat_repository is None:
            return {}
        state = self._chat_repository.get_conversation_state(
            conversation_id=conversation_id,
            owner_user_id=self._current_user_id,
        )
        return state if isinstance(state, dict) else {}

    def update_conversation_state_from_turn(
        self,
        *,
        conversation_id: str,
        messages: list[Any],
        llm_usages: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """Persist bounded operational state from the current completed turn."""
        if self._chat_repository is None:
            return {}
        raw_persisted_state = self._chat_repository.get_conversation_state(
            conversation_id=conversation_id,
            owner_user_id=self._current_user_id,
        )
        reset_requested = any(
            str(getattr(message, "role", "") or "") == "user"
            and contains_reset_command(str(getattr(message, "content", "") or ""))
            for message in messages
        )
        if reset_requested:
            persisted = operational_state_from_payload(None)
        else:
            persisted = operational_state_from_payload(raw_persisted_state)
        update = build_operational_state_from_messages(messages)
        if reset_requested:
            update.transitions.insert(0, "reset_by_user_command")
        merged = merge_operational_states(
            persisted=persisted,
            update=update,
        ).to_payload()
        if llm_usages:
            merged["llm_calls"] = llm_usages
        saved = self._chat_repository.update_conversation_state(
            conversation_id=conversation_id,
            conversation_state=merged,
            owner_user_id=self._current_user_id,
        )
        if reset_requested:
            self._clear_tool_memory(conversation_id)
        return saved

    def add_token_usage(
        self,
        *,
        conversation_id: str,
        llm_usages: list[dict[str, object]] | None,
    ) -> ConversationTokenUsage:
        """Add current-flow usage without reading persisted diagnostic state."""
        token_usage = ConversationTokenUsage.from_llm_usages(llm_usages)
        if self._chat_repository is not None and token_usage.total_tokens:
            self._chat_repository.add_token_usage(
                conversation_id=conversation_id,
                token_usage=token_usage,
                owner_user_id=self._current_user_id,
            )
        return token_usage

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
    ) -> ConversationToolArtifactRecord:
        """Retain a bounded raw result under the current user boundary."""
        if self._chat_repository is None:
            raise RuntimeError("tool_artifact_store_unavailable")
        return self._chat_repository.save_tool_artifact(
            conversation_id=conversation_id,
            artifact_id=artifact_id,
            tool_name=tool_name,
            arguments=arguments,
            payload=payload,
            content_sha256=content_sha256,
            source_char_count=source_char_count,
            observed_at=observed_at,
            expires_at=expires_at,
            max_records=max_records,
            owner_user_id=self._current_user_id,
        )

    def get_tool_artifact(
        self,
        *,
        conversation_id: str,
        artifact_id: str,
    ) -> ConversationToolArtifactRecord | None:
        """Load a non-expired raw result under the current user boundary."""
        if self._chat_repository is None:
            return None
        return self._chat_repository.get_tool_artifact(
            conversation_id=conversation_id,
            artifact_id=artifact_id,
            owner_user_id=self._current_user_id,
        )

    def list_tool_artifacts(
        self,
        *,
        conversation_id: str,
        limit: int,
    ) -> list[ConversationToolArtifactRecord]:
        """List exact recent artifact handles under the current user boundary."""
        if self._chat_repository is None:
            return []
        return self._chat_repository.list_tool_artifacts(
            conversation_id=conversation_id,
            limit=limit,
            owner_user_id=self._current_user_id,
        )

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
        observed_at: Any,
    ) -> ToolEvidenceRecord:
        """Persist one agent-owned tool artifact under the current user boundary."""
        if self._chat_repository is None:
            raise RuntimeError("tool_evidence_store_unavailable")
        return self._chat_repository.upsert_tool_evidence(
            conversation_id=conversation_id,
            tool_name=tool_name,
            arguments=arguments,
            payload=payload,
            note=note,
            content_sha256=content_sha256,
            source_char_count=source_char_count,
            observed_at=observed_at,
            owner_user_id=self._current_user_id,
        )

    def get_tool_evidence(
        self,
        *,
        conversation_id: str,
        evidence_key: str,
    ) -> ToolEvidenceRecord | None:
        """Load one agent-owned tool artifact under the current user boundary."""
        if self._chat_repository is None:
            return None
        return self._chat_repository.get_tool_evidence(
            conversation_id=conversation_id,
            evidence_key=evidence_key,
            owner_user_id=self._current_user_id,
        )

    def delete_tool_evidence(
        self,
        *,
        conversation_id: str,
        evidence_key: str,
    ) -> bool:
        """Delete one agent-owned note under the current user boundary."""
        if self._chat_repository is None:
            return False
        return self._chat_repository.delete_tool_evidence(
            conversation_id=conversation_id,
            evidence_key=evidence_key,
            owner_user_id=self._current_user_id,
        )

    def _clear_tool_memory(self, conversation_id: str) -> None:
        if self._chat_repository is None:
            return
        for method_name in ("clear_tool_evidence", "clear_tool_artifacts"):
            method = getattr(self._chat_repository, method_name, None)
            if callable(method):
                method(
                    conversation_id=conversation_id,
                    owner_user_id=self._current_user_id,
                )
        if self._agent_session_store is not None:
            self._agent_session_store.clear_session(conversation_id)
