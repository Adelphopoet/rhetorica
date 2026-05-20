from __future__ import annotations

from datetime import UTC, datetime

from rhetorica_backend.infra.postgres.chat_models import ChatConversationOrm, ChatMessageOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from sqlalchemy import func, select


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


class ChatRepository:
    """Persistence gateway for chat conversations and messages."""

    def __init__(self, db: DatabaseRuntime):
        self._db = db
        self._user_repository = UserAccountRepository(db=db)

    def create_conversation(
        self,
        workspace_id: str,
        title: str | None = None,
        owner_user_id: str | None = None,
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
                title=title,
                conversation_state={},
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_domain_conversation(row)

    def get_conversation(
        self,
        conversation_id: str,
        owner_user_id: str | None = None,
    ) -> ChatConversationItem | None:
        """Get conversation by ID without messages."""
        with self._db.session() as session:
            stmt = select(ChatConversationOrm).where(ChatConversationOrm.id == conversation_id)
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
            stmt = select(ChatConversationOrm).where(ChatConversationOrm.id == conversation_id)
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                return None

            # Then load messages with ordering and limit
            stmt = (
                select(ChatMessageOrm)
                .where(ChatMessageOrm.conversation_id == conversation_id)
                .order_by(ChatMessageOrm.created_at.desc())
                .limit(message_limit)
            )
            message_rows = session.execute(stmt).scalars().all()

            # Convert to domain messages (in reverse order for chronological)
            domain_messages = [self._to_domain_message(msg) for msg in reversed(message_rows)]

            return self._to_domain_conversation(row, messages=domain_messages)

    def get_messages(self, conversation_id: str, limit: int = 20) -> list[ChatMessageItem]:
        """Get recent messages from a conversation in chronological order."""
        with self._db.session() as session:
            stmt = (
                select(ChatMessageOrm)
                .where(ChatMessageOrm.conversation_id == conversation_id)
                .order_by(ChatMessageOrm.created_at.desc())
                .limit(limit)
            )
            rows = session.execute(stmt).scalars().all()
        # Reverse to get chronological order
        return [self._to_domain_message(row) for row in reversed(rows)]

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
                )
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return self._to_domain_message(row)

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
    ) -> ChatMessageItem:
        """Save a new message to a conversation."""
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            stmt = select(ChatConversationOrm).where(ChatConversationOrm.id == conversation_id)
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            conversation = session.execute(stmt).scalar_one_or_none()
            if conversation is None:
                raise ConversationNotFoundError
            row = ChatMessageOrm(
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
                created_at=now,
            )
            session.add(row)
            conversation.updated_at = now
            session.commit()
            session.refresh(row)
            return self._to_domain_message(row)

    def get_conversation_state(
        self,
        conversation_id: str,
        owner_user_id: str | None = None,
    ) -> dict[str, object]:
        """Return persisted semantic/runtime conversation state."""
        with self._db.session() as session:
            stmt = select(ChatConversationOrm).where(ChatConversationOrm.id == conversation_id)
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
            stmt = select(ChatConversationOrm).where(ChatConversationOrm.id == conversation_id)
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                raise ConversationNotFoundError
            row.conversation_state = dict(conversation_state)
            row.updated_at = now
            session.commit()
            return dict(row.conversation_state or {})

    def update_message_feedback(
        self,
        conversation_id: str,
        message_id: str,
        feedback: str,
        owner_user_id: str | None = None,
        feedback_reason: str | None = None,
        feedback_comment: str | None = None,
    ) -> ChatMessageItem | None:
        """Update feedback for an assistant message."""
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            stmt = select(ChatConversationOrm).where(ChatConversationOrm.id == conversation_id)
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            conversation = session.execute(stmt).scalar_one_or_none()
            if conversation is None:
                return None
            stmt = (
                select(ChatMessageOrm)
                .join(ChatConversationOrm, ChatConversationOrm.id == ChatMessageOrm.conversation_id)
                .where(
                    ChatMessageOrm.conversation_id == conversation_id,
                    ChatMessageOrm.id == message_id,
                )
            )
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                return None

            row.feedback = feedback
            row.feedback_updated_at = now
            row.feedback_reason = feedback_reason
            row.feedback_comment = feedback_comment
            conversation.updated_at = now
            session.commit()
            session.refresh(row)
            return self._to_domain_message(row)

    def update_conversation_title(
        self,
        conversation_id: str,
        title: str,
        owner_user_id: str | None = None,
    ) -> ChatConversationItem | None:
        """Update conversation title."""
        with self._db.session() as session:
            stmt = select(ChatConversationOrm).where(ChatConversationOrm.id == conversation_id)
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
        """Delete a conversation and all its messages."""
        with self._db.session() as session:
            stmt = select(ChatConversationOrm).where(ChatConversationOrm.id == conversation_id)
            if owner_user_id is not None:
                stmt = stmt.where(ChatConversationOrm.owner_user_id == owner_user_id)
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

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
                .where(ChatConversationOrm.workspace_id == workspace_id)
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
            title=row.title,
            created_at=row.created_at,
            updated_at=row.updated_at,
            messages=domain_messages,
            message_count=message_count or 0,
            conversation_state=row.conversation_state,
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
        )

    def _resolve_owner_user_id(
        self,
        workspace_id: str,
        owner_user_id: str | None,
    ) -> str:
        if owner_user_id is not None:
            return owner_user_id
        return self._user_repository.ensure_bootstrap_user(workspace_id).id
