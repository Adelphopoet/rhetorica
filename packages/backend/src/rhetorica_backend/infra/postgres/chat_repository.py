from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rhetorica_backend.domain.chat.token_usage import ConversationTokenUsage
from rhetorica_backend.domain.chat.tool_artifacts import ConversationToolArtifactRecord
from rhetorica_backend.domain.chat.tool_evidence import (
    ToolEvidenceRecord,
    build_tool_evidence_key,
)
from rhetorica_backend.domain.files.models import Artifact
from rhetorica_backend.infra.postgres.chat_models import (
    ChatAnswerTraceOrm,
    ChatConversationOrm,
    ChatMessageOrm,
    ChatToolArtifactOrm,
    ChatToolEvidenceOrm,
)
from rhetorica_backend.infra.postgres.file_artifact_repository import FileArtifactRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session


class ConversationNotFoundError(ValueError):
    """Conversation lookup failed."""


class ChatMessageItem:
    """Domain representation of a chat message."""

    def __init__(
        self,
        message_id: str,
        conversation_id: str,
        role: str,
        content: str,
        created_at: datetime,
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
        feedback: str | None = None,
        feedback_updated_at: datetime | None = None,
        feedback_reason: str | None = None,
        feedback_comment: str | None = None,
        feedback_what_went_wrong: str | None = None,
        feedback_expected_answer: str | None = None,
        agent_id: str | None = None,
        files: list[Artifact] | None = None,
        source_provider: str | None = None,
        source_message_id: str | None = None,
    ):
        self.id = message_id
        self.conversation_id = conversation_id
        self.role = role
        self.content = content
        self.created_at = created_at
        self.generated_sql = generated_sql
        self.execution_id = execution_id
        self.context_tables = context_tables or []
        self.metadata_sources = metadata_sources or []
        self.claims = claims or []
        self.evidence = evidence or []
        self.claim_evidence_links = claim_evidence_links or []
        self.uncertainty_flags = uncertainty_flags or []
        self.capability_trace = capability_trace or {}
        self.optimization_trace = optimization_trace or {}
        self.selected_connector = selected_connector
        self.analysis_document = analysis_document
        self.visualization_spec = visualization_spec
        self.warnings = warnings or []
        self.feedback = feedback
        self.feedback_updated_at = feedback_updated_at
        self.feedback_reason = feedback_reason
        self.feedback_comment = feedback_comment
        self.feedback_what_went_wrong = feedback_what_went_wrong
        self.feedback_expected_answer = feedback_expected_answer
        self.agent_id = agent_id
        self.files = files or []
        self.source_provider = source_provider
        self.source_message_id = source_message_id


class ChatConversationItem:
    """Domain representation of a chat conversation."""

    def __init__(
        self,
        conversation_id: str,
        workspace_id: str,
        owner_user_id: str,
        title: str | None,
        created_at: datetime,
        updated_at: datetime,
        messages: list[ChatMessageItem] | None = None,
        message_count: int = 0,
        conversation_state: dict[str, object] | None = None,
        agent_id: str | None = None,
        token_usage: ConversationTokenUsage | None = None,
    ):
        self.id = conversation_id
        self.workspace_id = workspace_id
        self.owner_user_id = owner_user_id
        self.title = title
        self.created_at = created_at
        self.updated_at = updated_at
        self.messages = messages or []
        self.message_count = max(message_count, len(self.messages))
        self.conversation_state = conversation_state or {}
        self.agent_id = agent_id
        self.token_usage = token_usage or ConversationTokenUsage()


class ChatRepository:
    """Persistence gateway for chat conversations and messages."""

    def __init__(self, db: DatabaseRuntime):
        self._db = db
        self._user_repository = UserAccountRepository(db=db)
        self._file_artifacts = FileArtifactRepository(db=db)

    def create_conversation(
        self,
        workspace_id: str,
        title: str | None = None,
        owner_user_id: str | None = None,
        agent_id: str | None = None,
    ) -> ChatConversationItem:
        """Create a new conversation."""
        now = datetime.now(tz=UTC)
        resolved_owner_user_id = self._resolve_owner_user_id(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
        )
        with self._db.session() as session:
            row = ChatConversationOrm(
                workspace_id=workspace_id,
                owner_user_id=resolved_owner_user_id,
                agent_id=agent_id,
                title=title,
                conversation_state={},
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_domain_conversation(row)

    def save_answer_trace(
        self,
        *,
        assistant_message_id: str,
        conversation_id: str,
        trace_id: str,
        tool_call_count: int = 0,
        tool_error_count: int = 0,
    ) -> None:
        """Persist the content-free Langfuse trace link for an assistant answer."""
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(ChatAnswerTraceOrm, assistant_message_id)
            if row is None:
                session.add(ChatAnswerTraceOrm(
                    assistant_message_id=assistant_message_id,
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    tool_call_count=max(0, tool_call_count),
                    tool_error_count=max(0, tool_error_count),
                    created_at=now,
                ))
            else:
                row.trace_id = trace_id
                row.tool_call_count = max(0, tool_call_count)
                row.tool_error_count = max(0, tool_error_count)
            session.commit()

    def get_conversation(
        self,
        conversation_id: str,
        owner_user_id: str | None = None,
    ) -> ChatConversationItem | None:
        """Get conversation by ID without messages."""
        with self._db.session() as session:
            stmt = select(ChatConversationOrm).where(
                ChatConversationOrm.id == conversation_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return self._to_domain_conversation(row)

    def get_conversation_with_messages(
        self,
        conversation_id: str,
        message_limit: int = 20,
        owner_user_id: str | None = None,
    ) -> ChatConversationItem | None:
        """Get conversation with recent messages."""
        with self._db.session() as session:
            stmt = select(ChatConversationOrm).where(
                ChatConversationOrm.id == conversation_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                return None

            # Then load messages with ordering and limit
            stmt = (
                select(ChatMessageOrm)
                .join(ChatConversationOrm, ChatConversationOrm.id == ChatMessageOrm.conversation_id)
                .where(
                    ChatMessageOrm.conversation_id == conversation_id,
                    ChatConversationOrm.deleted_at.is_(None),
                )
                .order_by(ChatMessageOrm.created_at.desc())
                .limit(message_limit)
            )
            message_rows = session.execute(stmt).scalars().all()

            # Convert to domain messages (in reverse order for chronological)
            domain_messages = [self._to_domain_message(msg) for msg in reversed(message_rows)]
            self._hydrate_message_files(domain_messages)

            return self._to_domain_conversation(row, messages=domain_messages)

    def get_messages(self, conversation_id: str, limit: int = 20) -> list[ChatMessageItem]:
        """Get recent messages from a conversation in chronological order."""
        with self._db.session() as session:
            stmt = (
                select(ChatMessageOrm)
                .join(ChatConversationOrm, ChatConversationOrm.id == ChatMessageOrm.conversation_id)
                .where(
                    ChatMessageOrm.conversation_id == conversation_id,
                    ChatConversationOrm.deleted_at.is_(None),
                )
                .order_by(ChatMessageOrm.created_at.desc())
                .limit(limit)
            )
            rows = session.execute(stmt).scalars().all()
        # Reverse to get chronological order
        messages = [self._to_domain_message(row) for row in reversed(rows)]
        self._hydrate_message_files(messages)
        return messages

    def get_message(
        self,
        conversation_id: str,
        message_id: str,
        owner_user_id: str | None = None,
    ) -> ChatMessageItem | None:
        """Get message by ID within a conversation."""
        with self._db.session() as session:
            stmt = (
                select(ChatMessageOrm)
                .join(ChatConversationOrm, ChatConversationOrm.id == ChatMessageOrm.conversation_id)
                .where(
                    ChatMessageOrm.conversation_id == conversation_id,
                    ChatMessageOrm.id == message_id,
                    ChatConversationOrm.deleted_at.is_(None),
                )
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        message = self._to_domain_message(row)
        self._hydrate_message_files([message])
        return message

    def get_execution_message(
        self,
        *,
        conversation_id: str,
        execution_id: str,
        owner_user_id: str,
    ) -> ChatMessageItem | None:
        with self._db.session() as session:
            row = session.execute(
                select(ChatMessageOrm)
                .join(ChatConversationOrm, ChatConversationOrm.id == ChatMessageOrm.conversation_id)
                .where(
                    ChatMessageOrm.conversation_id == conversation_id,
                    ChatMessageOrm.execution_id == execution_id,
                    ChatConversationOrm.owner_user_id == owner_user_id,
                    ChatConversationOrm.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
        return self._to_domain_message(row) if row is not None else None

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
        owner_user_id: str | None = None,
        agent_id: str | None = None,
        source_provider: str | None = None,
        source_message_id: str | None = None,
    ) -> ChatMessageItem:
        """Save a new message to a conversation."""
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            stmt = select(ChatConversationOrm).where(
                ChatConversationOrm.id == conversation_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            conversation = session.execute(stmt).scalar_one_or_none()
            if conversation is None:
                raise ConversationNotFoundError
            row = ChatMessageOrm(
                conversation_id=conversation_id,
                role=role,
                content=content,
                source_provider=source_provider,
                source_message_id=source_message_id,
                generated_sql=generated_sql,
                execution_id=execution_id,
                agent_id=agent_id,
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
                created_at=now,
            )
            session.add(row)
            conversation.updated_at = now
            session.commit()
            session.refresh(row)
            return self._to_domain_message(row)

    def list_source_message_ids(
        self,
        *,
        conversation_id: str,
        source_provider: str,
        owner_user_id: str | None = None,
    ) -> set[str]:
        """Return external message ids already persisted in an accessible conversation."""
        with self._db.session() as session:
            stmt = (
                select(ChatMessageOrm.source_message_id)
                .join(
                    ChatConversationOrm,
                    ChatConversationOrm.id == ChatMessageOrm.conversation_id,
                )
                .where(
                    ChatMessageOrm.conversation_id == conversation_id,
                    ChatMessageOrm.source_provider == source_provider,
                    ChatMessageOrm.source_message_id.is_not(None),
                    ChatConversationOrm.deleted_at.is_(None),
                )
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            values = session.execute(stmt).scalars().all()
        return {value for value in values if isinstance(value, str) and value}

    def get_conversation_state(
        self,
        conversation_id: str,
        owner_user_id: str | None = None,
    ) -> dict[str, object]:
        """Return persisted semantic/runtime conversation state."""
        with self._db.session() as session:
            stmt = select(ChatConversationOrm).where(
                ChatConversationOrm.id == conversation_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                raise ConversationNotFoundError
            return dict(row.conversation_state or {})

    def update_conversation_state(
        self,
        conversation_id: str,
        conversation_state: dict[str, object],
        owner_user_id: str | None = None,
    ) -> dict[str, object]:
        """Persist semantic/runtime conversation state."""
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            stmt = select(ChatConversationOrm).where(
                ChatConversationOrm.id == conversation_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                raise ConversationNotFoundError
            row.conversation_state = dict(conversation_state)
            row.updated_at = now
            session.commit()
            return dict(row.conversation_state or {})

    def add_token_usage(
        self,
        *,
        conversation_id: str,
        token_usage: ConversationTokenUsage,
        owner_user_id: str | None = None,
    ) -> None:
        """Atomically increment provider-reported token totals."""
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            stmt = update(ChatConversationOrm).where(
                ChatConversationOrm.id == conversation_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            result = session.execute(
                stmt.values(
                    llm_input_tokens=(
                        ChatConversationOrm.llm_input_tokens + token_usage.input_tokens
                    ),
                    llm_output_tokens=(
                        ChatConversationOrm.llm_output_tokens + token_usage.output_tokens
                    ),
                    llm_cached_input_tokens=(
                        ChatConversationOrm.llm_cached_input_tokens
                        + token_usage.cached_input_tokens
                    ),
                    llm_cache_write_input_tokens=(
                        ChatConversationOrm.llm_cache_write_input_tokens
                        + token_usage.cache_write_input_tokens
                    ),
                    llm_cache_usage_reported_calls=(
                        ChatConversationOrm.llm_cache_usage_reported_calls
                        + token_usage.cache_usage_reported_calls
                    ),
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                session.rollback()
                raise ConversationNotFoundError
            session.commit()

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
        owner_user_id: str | None = None,
    ) -> ToolEvidenceRecord:
        """Replace one exact tool-call note while preserving verified raw evidence."""
        now = datetime.now(tz=UTC)
        evidence_key = build_tool_evidence_key(
            tool_name=tool_name,
            arguments=arguments,
        )
        with self._db.session() as session:
            conversation_stmt = select(ChatConversationOrm).where(
                ChatConversationOrm.id == conversation_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
            if owner_user_id is not None:
                conversation_stmt = conversation_stmt.where(
                    ChatConversationOrm.owner_user_id == owner_user_id
                )
            conversation = session.execute(conversation_stmt).scalar_one_or_none()
            if conversation is None:
                raise ConversationNotFoundError

            row = session.execute(
                select(ChatToolEvidenceOrm).where(
                    ChatToolEvidenceOrm.conversation_id == conversation_id,
                    ChatToolEvidenceOrm.evidence_key == evidence_key,
                )
            ).scalar_one_or_none()
            if row is None:
                row = ChatToolEvidenceOrm(
                    conversation_id=conversation_id,
                    evidence_key=evidence_key,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            row.tool_name = tool_name
            row.arguments = dict(arguments)
            row.payload = dict(payload)
            row.note = note
            row.content_sha256 = content_sha256
            row.source_char_count = source_char_count
            row.observed_at = observed_at
            row.updated_at = now
            conversation.updated_at = now
            session.commit()
            session.refresh(row)
            return self._to_domain_tool_evidence(row)

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
        owner_user_id: str | None = None,
    ) -> ConversationToolArtifactRecord:
        """Retain one raw result and deterministically bound the conversation ledger."""
        now = datetime.now(tz=UTC)
        bounded_max_records = max(1, min(max_records, 1_000))
        with self._db.session() as session:
            conversation_stmt = select(ChatConversationOrm).where(
                ChatConversationOrm.id == conversation_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
            if owner_user_id is not None:
                conversation_stmt = conversation_stmt.where(
                    ChatConversationOrm.owner_user_id == owner_user_id
                )
            conversation = session.execute(conversation_stmt).scalar_one_or_none()
            if conversation is None:
                raise ConversationNotFoundError

            session.execute(
                delete(ChatToolArtifactOrm).where(
                    ChatToolArtifactOrm.conversation_id == conversation_id,
                    ChatToolArtifactOrm.expires_at <= now,
                )
            )
            row = session.execute(
                select(ChatToolArtifactOrm).where(
                    ChatToolArtifactOrm.conversation_id == conversation_id,
                    ChatToolArtifactOrm.artifact_id == artifact_id,
                )
            ).scalar_one_or_none()
            if row is None:
                row = ChatToolArtifactOrm(
                    conversation_id=conversation_id,
                    artifact_id=artifact_id,
                    created_at=now,
                )
                session.add(row)
            row.tool_name = tool_name
            row.arguments = dict(arguments)
            row.payload = dict(payload)
            row.content_sha256 = content_sha256
            row.source_char_count = source_char_count
            row.observed_at = observed_at
            row.expires_at = expires_at
            session.flush()

            retained_ids = list(
                session.execute(
                    select(ChatToolArtifactOrm.id)
                    .where(ChatToolArtifactOrm.conversation_id == conversation_id)
                    .order_by(
                        ChatToolArtifactOrm.observed_at.desc(),
                        ChatToolArtifactOrm.created_at.desc(),
                    )
                    .limit(bounded_max_records)
                ).scalars()
            )
            session.execute(
                delete(ChatToolArtifactOrm).where(
                    ChatToolArtifactOrm.conversation_id == conversation_id,
                    ChatToolArtifactOrm.id.not_in(retained_ids),
                )
            )
            conversation.updated_at = now
            session.commit()
            session.refresh(row)
            return self._to_domain_tool_artifact(row)

    def get_tool_artifact(
        self,
        *,
        conversation_id: str,
        artifact_id: str,
        owner_user_id: str | None = None,
    ) -> ConversationToolArtifactRecord | None:
        """Load a non-expired artifact under the conversation ownership boundary."""
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            stmt = (
                select(ChatToolArtifactOrm)
                .join(
                    ChatConversationOrm,
                    ChatConversationOrm.id == ChatToolArtifactOrm.conversation_id,
                )
                .where(
                    ChatToolArtifactOrm.conversation_id == conversation_id,
                    ChatToolArtifactOrm.artifact_id == artifact_id,
                    ChatToolArtifactOrm.expires_at > now,
                    ChatConversationOrm.deleted_at.is_(None),
                )
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            row = session.execute(stmt).scalar_one_or_none()
        return self._to_domain_tool_artifact(row) if row is not None else None

    def list_tool_artifacts(
        self,
        *,
        conversation_id: str,
        owner_user_id: str | None = None,
        limit: int = 20,
    ) -> list[ConversationToolArtifactRecord]:
        """List recent non-expired handles without exposing unrelated conversations."""
        now = datetime.now(tz=UTC)
        bounded_limit = max(1, min(limit, 100))
        with self._db.session() as session:
            stmt = (
                select(ChatToolArtifactOrm)
                .join(
                    ChatConversationOrm,
                    ChatConversationOrm.id == ChatToolArtifactOrm.conversation_id,
                )
                .where(
                    ChatToolArtifactOrm.conversation_id == conversation_id,
                    ChatToolArtifactOrm.expires_at > now,
                    ChatConversationOrm.deleted_at.is_(None),
                )
                .order_by(
                    ChatToolArtifactOrm.observed_at.desc(),
                    ChatToolArtifactOrm.created_at.desc(),
                )
                .limit(bounded_limit)
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            rows = session.execute(stmt).scalars().all()
        return [self._to_domain_tool_artifact(row) for row in rows]

    def list_tool_evidence(
        self,
        *,
        conversation_id: str,
        owner_user_id: str | None = None,
        limit: int = 12,
    ) -> list[ToolEvidenceRecord]:
        """List the latest model-owned evidence notes for prompt cataloging."""
        bounded_limit = max(1, min(limit, 50))
        with self._db.session() as session:
            stmt = (
                select(ChatToolEvidenceOrm)
                .join(
                    ChatConversationOrm,
                    ChatConversationOrm.id == ChatToolEvidenceOrm.conversation_id,
                )
                .where(
                    ChatToolEvidenceOrm.conversation_id == conversation_id,
                    ChatConversationOrm.deleted_at.is_(None),
                )
                .order_by(ChatToolEvidenceOrm.updated_at.desc())
                .limit(bounded_limit)
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            rows = session.execute(stmt).scalars().all()
        return [self._to_domain_tool_evidence(row) for row in rows]

    def get_tool_evidence(
        self,
        *,
        conversation_id: str,
        evidence_key: str,
        owner_user_id: str | None = None,
    ) -> ToolEvidenceRecord | None:
        """Load one durable tool artifact by its conversation-scoped key."""
        with self._db.session() as session:
            stmt = (
                select(ChatToolEvidenceOrm)
                .join(
                    ChatConversationOrm,
                    ChatConversationOrm.id == ChatToolEvidenceOrm.conversation_id,
                )
                .where(
                    ChatToolEvidenceOrm.conversation_id == conversation_id,
                    ChatToolEvidenceOrm.evidence_key == evidence_key,
                    ChatConversationOrm.deleted_at.is_(None),
                )
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            row = session.execute(stmt).scalar_one_or_none()
        return self._to_domain_tool_evidence(row) if row is not None else None

    def delete_tool_evidence(
        self,
        *,
        conversation_id: str,
        evidence_key: str,
        owner_user_id: str | None = None,
    ) -> bool:
        """Forget one model-owned note after checking conversation ownership."""
        with self._db.session() as session:
            stmt = (
                select(ChatToolEvidenceOrm)
                .join(
                    ChatConversationOrm,
                    ChatConversationOrm.id == ChatToolEvidenceOrm.conversation_id,
                )
                .where(
                    ChatToolEvidenceOrm.conversation_id == conversation_id,
                    ChatToolEvidenceOrm.evidence_key == evidence_key,
                    ChatConversationOrm.deleted_at.is_(None),
                )
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def clear_tool_evidence(
        self,
        *,
        conversation_id: str,
        owner_user_id: str | None = None,
    ) -> int:
        """Clear agent-owned evidence on an explicit conversation reset."""
        with self._db.session() as session:
            conversation_stmt = select(ChatConversationOrm.id).where(
                ChatConversationOrm.id == conversation_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
            if owner_user_id is not None:
                conversation_stmt = conversation_stmt.where(
                    ChatConversationOrm.owner_user_id == owner_user_id
                )
            if session.execute(conversation_stmt).scalar_one_or_none() is None:
                return 0
            result = session.execute(
                delete(ChatToolEvidenceOrm).where(
                    ChatToolEvidenceOrm.conversation_id == conversation_id
                )
            )
            session.commit()
            return int(result.rowcount or 0)

    def clear_tool_artifacts(
        self,
        *,
        conversation_id: str,
        owner_user_id: str | None = None,
    ) -> int:
        """Clear temporary raw artifacts on explicit conversation reset."""
        with self._db.session() as session:
            conversation_stmt = select(ChatConversationOrm.id).where(
                ChatConversationOrm.id == conversation_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
            if owner_user_id is not None:
                conversation_stmt = conversation_stmt.where(
                    ChatConversationOrm.owner_user_id == owner_user_id
                )
            if session.execute(conversation_stmt).scalar_one_or_none() is None:
                return 0
            result = session.execute(
                delete(ChatToolArtifactOrm).where(
                    ChatToolArtifactOrm.conversation_id == conversation_id
                )
            )
            session.commit()
            return int(result.rowcount or 0)

    def update_message_feedback(
        self,
        conversation_id: str,
        message_id: str,
        feedback: str,
        owner_user_id: str | None = None,
        feedback_reason: str | None = None,
        feedback_comment: str | None = None,
        feedback_what_went_wrong: str | None = None,
        feedback_expected_answer: str | None = None,
    ) -> ChatMessageItem | None:
        """Update feedback for an assistant message."""
        with self._db.session() as session:
            item = self.update_message_feedback_in_session(
                session=session,
                conversation_id=conversation_id,
                message_id=message_id,
                feedback=feedback,
                owner_user_id=owner_user_id,
                feedback_reason=feedback_reason,
                feedback_comment=feedback_comment,
                feedback_what_went_wrong=feedback_what_went_wrong,
                feedback_expected_answer=feedback_expected_answer,
            )
            if item is None:
                return None
            session.commit()
            return item

    def update_message_feedback_in_session(
        self,
        *,
        session: Session,
        conversation_id: str,
        message_id: str,
        feedback: str,
        owner_user_id: str | None = None,
        feedback_reason: str | None = None,
        feedback_comment: str | None = None,
        feedback_what_went_wrong: str | None = None,
        feedback_expected_answer: str | None = None,
    ) -> ChatMessageItem | None:
        """Update feedback without committing so related projections can be atomic."""
        now = datetime.now(tz=UTC)
        conversation_stmt = select(ChatConversationOrm).where(
            ChatConversationOrm.id == conversation_id,
            ChatConversationOrm.deleted_at.is_(None),
        )
        if owner_user_id is not None:
            conversation_stmt = conversation_stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
        conversation = session.execute(conversation_stmt.with_for_update()).scalar_one_or_none()
        if conversation is None:
            return None
        message_stmt = (
            select(ChatMessageOrm)
            .join(ChatConversationOrm, ChatConversationOrm.id == ChatMessageOrm.conversation_id)
            .where(
                ChatMessageOrm.conversation_id == conversation_id,
                ChatMessageOrm.id == message_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
        )
        if owner_user_id is not None:
            message_stmt = message_stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
        row = session.execute(message_stmt.with_for_update()).scalar_one_or_none()
        if row is None:
            return None
        row.feedback = feedback
        row.feedback_updated_at = now
        row.feedback_reason = feedback_reason
        row.feedback_comment = feedback_comment
        row.feedback_what_went_wrong = feedback_what_went_wrong
        row.feedback_expected_answer = feedback_expected_answer
        conversation.updated_at = now
        session.flush()
        return self._to_domain_message(row)

    def update_conversation_title(
        self,
        conversation_id: str,
        title: str,
        owner_user_id: str | None = None,
    ) -> ChatConversationItem | None:
        """Update conversation title."""
        with self._db.session() as session:
            stmt = select(ChatConversationOrm).where(
                ChatConversationOrm.id == conversation_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                return None
            row.title = title
            row.updated_at = datetime.now(tz=UTC)
            session.commit()
            session.refresh(row)
            return self._to_domain_conversation(row)

    def delete_conversation(self, conversation_id: str, owner_user_id: str | None = None) -> bool:
        """Soft-delete an owned conversation while retaining all related data."""
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            stmt = update(ChatConversationOrm).where(
                ChatConversationOrm.id == conversation_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            result = session.execute(stmt.values(deleted_at=now, updated_at=now))
            session.commit()
            return result.rowcount == 1

    def _hydrate_message_files(self, messages: list[ChatMessageItem]) -> None:
        by_message = self._file_artifacts.list_message_refs(message.id for message in messages)
        for message in messages:
            message.files = by_message.get(message.id, [])

    def list_conversations(
        self,
        workspace_id: str,
        limit: int = 50,
        owner_user_id: str | None = None,
    ) -> list[ChatConversationItem]:
        """List conversations for a workspace, ordered by updated_at."""
        with self._db.session() as session:
            stmt = (
                select(
                    ChatConversationOrm,
                    func.count(ChatMessageOrm.id).label("message_count"),
                )
                .outerjoin(
                    ChatMessageOrm,
                    ChatMessageOrm.conversation_id == ChatConversationOrm.id,
                )
                .where(
                    ChatConversationOrm.workspace_id == workspace_id,
                    ChatConversationOrm.deleted_at.is_(None),
                )
                .group_by(ChatConversationOrm.id)
                .order_by(ChatConversationOrm.updated_at.desc())
                .limit(limit)
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            rows = session.execute(stmt).all()
        return [
            self._to_domain_conversation(row, message_count=message_count)
            for row, message_count in rows
        ]

    @staticmethod
    def _to_domain_conversation(
        row: ChatConversationOrm,
        messages: list[ChatMessageItem] | None = None,
        message_count: int | None = None,
    ) -> ChatConversationItem:
        # If messages are provided explicitly, use them
        # Otherwise, return conversation without messages (avoid lazy loading)
        domain_messages: list[ChatMessageItem] | None = None
        if messages is not None:
            domain_messages = messages
        # Note: We don't lazy-load messages here to avoid DetachedInstanceError

        return ChatConversationItem(
            conversation_id=row.id,
            workspace_id=row.workspace_id,
            owner_user_id=row.owner_user_id,
            agent_id=row.agent_id,
            title=row.title,
            created_at=row.created_at,
            updated_at=row.updated_at,
            messages=domain_messages,
            message_count=message_count or 0,
            conversation_state=row.conversation_state,
            token_usage=ConversationTokenUsage(
                input_tokens=row.llm_input_tokens,
                output_tokens=row.llm_output_tokens,
                cached_input_tokens=row.llm_cached_input_tokens,
                cache_write_input_tokens=row.llm_cache_write_input_tokens,
                cache_usage_reported_calls=row.llm_cache_usage_reported_calls,
            ),
        )

    @staticmethod
    def _to_domain_message(row: ChatMessageOrm) -> ChatMessageItem:
        return ChatMessageItem(
            message_id=row.id,
            conversation_id=row.conversation_id,
            role=row.role,
            content=row.content,
            created_at=row.created_at,
            generated_sql=row.generated_sql,
            execution_id=row.execution_id,
            agent_id=row.agent_id,
            context_tables=row.context_tables,
            metadata_sources=row.metadata_sources,
            claims=row.claims,
            evidence=row.evidence,
            claim_evidence_links=row.claim_evidence_links,
            uncertainty_flags=row.uncertainty_flags,
            capability_trace=row.capability_trace,
            optimization_trace=row.optimization_trace,
            selected_connector=row.selected_connector,
            analysis_document=row.analysis_document,
            visualization_spec=row.visualization_spec,
            warnings=row.warnings,
            feedback=row.feedback,
            feedback_updated_at=row.feedback_updated_at,
            feedback_reason=row.feedback_reason,
            feedback_comment=row.feedback_comment,
            feedback_what_went_wrong=row.feedback_what_went_wrong,
            feedback_expected_answer=row.feedback_expected_answer,
            source_provider=row.source_provider,
            source_message_id=row.source_message_id,
        )

    @staticmethod
    def _to_domain_tool_evidence(row: ChatToolEvidenceOrm) -> ToolEvidenceRecord:
        return ToolEvidenceRecord(
            id=row.id,
            conversation_id=row.conversation_id,
            evidence_key=row.evidence_key,
            tool_name=row.tool_name,
            arguments=dict(row.arguments or {}),
            payload=dict(row.payload or {}),
            note=row.note,
            content_sha256=row.content_sha256,
            source_char_count=row.source_char_count,
            observed_at=row.observed_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_domain_tool_artifact(
        row: ChatToolArtifactOrm,
    ) -> ConversationToolArtifactRecord:
        return ConversationToolArtifactRecord(
            id=row.id,
            conversation_id=row.conversation_id,
            artifact_id=row.artifact_id,
            tool_name=row.tool_name,
            arguments=dict(row.arguments or {}),
            payload=dict(row.payload or {}),
            content_sha256=row.content_sha256,
            source_char_count=row.source_char_count,
            observed_at=row.observed_at,
            expires_at=row.expires_at,
            created_at=row.created_at,
        )

    def _resolve_owner_user_id(
        self,
        workspace_id: str,
        owner_user_id: str | None,
    ) -> str:
        if owner_user_id is not None:
            return owner_user_id
        return self._user_repository.ensure_bootstrap_user(workspace_id).id
