from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from rhetorica_backend.infra.postgres.base import Base
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship


class ChatConversationOrm(Base):
    """Conversation container for multi-turn chat sessions."""

    __tablename__ = "chat_conversation"
    __table_args__ = (
        CheckConstraint(
            "llm_input_tokens >= 0",
            name="ck_chat_conversation_llm_input_tokens_non_negative",
        ),
        CheckConstraint(
            "llm_output_tokens >= 0",
            name="ck_chat_conversation_llm_output_tokens_non_negative",
        ),
        CheckConstraint(
            "llm_cached_input_tokens >= 0",
            name="ck_chat_conv_cached_input_nonneg",
        ),
        CheckConstraint(
            "llm_cache_write_input_tokens >= 0",
            name="ck_chat_conv_cache_write_nonneg",
        ),
        CheckConstraint(
            "llm_cache_usage_reported_calls >= 0",
            name="ck_chat_conv_cache_reported_nonneg",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    agent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_profile.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(255))
    conversation_state: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    llm_input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    llm_output_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    llm_cached_input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    llm_cache_write_input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    llm_cache_usage_reported_calls: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    owner = relationship("UserAccountOrm")
    messages: Mapped[list[ChatMessageOrm]] = relationship(
        "ChatMessageOrm",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )
    tool_evidence: Mapped[list[ChatToolEvidenceOrm]] = relationship(
        "ChatToolEvidenceOrm",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )
    tool_artifacts: Mapped[list[ChatToolArtifactOrm]] = relationship(
        "ChatToolArtifactOrm",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class ChatLlmCredentialBindingOrm(Base):
    """Durable conversation-scoped reference to one connector credential."""

    __tablename__ = "chat_llm_credential_binding"
    __table_args__ = (
        CheckConstraint(
            "credential_index >= 0",
            name="ck_chat_llm_credential_binding_index_non_negative",
        ),
        CheckConstraint(
            "credential_set_version > 0",
            name="ck_chat_llm_credential_binding_version_positive",
        ),
        Index(
            "ix_chat_llm_credential_binding_connector",
            "llm_connector_id",
        ),
    )

    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_conversation.id", ondelete="CASCADE"),
        primary_key=True,
    )
    llm_connector_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("llm_connector_config.id", ondelete="CASCADE"),
        primary_key=True,
    )
    credential_index: Mapped[int] = mapped_column(Integer, nullable=False)
    credential_set_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatMessageOrm(Base):
    """Single message within a conversation."""

    __tablename__ = "chat_message"
    __table_args__ = (
        UniqueConstraint("conversation_id", "created_at", "id", name="uq_chat_message_order"),
        Index(
            "ix_chat_message_conversation_source",
            "conversation_id",
            "source_provider",
            "source_message_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_conversation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )  # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_provider: Mapped[str | None] = mapped_column(String(32))
    source_message_id: Mapped[str | None] = mapped_column(String(128))

    # Execution metadata (optional, for assistant messages)
    generated_sql: Mapped[str | None] = mapped_column(Text)
    execution_id: Mapped[str | None] = mapped_column(String(36))
    agent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_profile.id", ondelete="SET NULL"), index=True
    )
    context_tables: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    metadata_sources: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    claims: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    evidence: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    claim_evidence_links: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    uncertainty_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    capability_trace: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    optimization_trace: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    selected_connector: Mapped[str | None] = mapped_column(String(255))
    analysis_document: Mapped[dict[str, object] | None] = mapped_column(JSON)
    visualization_spec: Mapped[dict[str, object] | None] = mapped_column(JSON)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    feedback: Mapped[str | None] = mapped_column(String(32))
    feedback_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feedback_reason: Mapped[str | None] = mapped_column(String(32))
    feedback_comment: Mapped[str | None] = mapped_column(Text)
    feedback_what_went_wrong: Mapped[str | None] = mapped_column(Text)
    feedback_expected_answer: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    conversation: Mapped[ChatConversationOrm] = relationship(
        "ChatConversationOrm",
        back_populates="messages",
    )


class ChatAnswerTraceOrm(Base):
    """Content-free link between a persisted assistant answer and Langfuse trace."""

    __tablename__ = "chat_answer_trace"

    assistant_message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_message.id", ondelete="CASCADE"), primary_key=True
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_conversation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Snapshot of the actual backend ledger for this assistant turn.  It must
    # not be reconstructed from conversation-wide, de-duplicated evidence.
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    tool_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LangfuseReviewOutboxOrm(Base):
    """Retryable projection of a saved answer feedback event to Langfuse."""

    __tablename__ = "langfuse_review_outbox"
    __table_args__ = (
        UniqueConstraint("assistant_message_id", "event_type", name="uq_langfuse_review_outbox_event"),
        Index("ix_langfuse_review_outbox_delivery", "delivery_state", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    assistant_message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_message.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    delivery_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    claim_token: Mapped[str | None] = mapped_column(String(36))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatToolEvidenceOrm(Base):
    """Durable raw artifact and model-owned note for one exact tool call."""

    __tablename__ = "chat_tool_evidence"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "evidence_key",
            name="uq_chat_tool_evidence_conversation_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_conversation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evidence_key: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(512), nullable=False)
    arguments: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    conversation: Mapped[ChatConversationOrm] = relationship(
        "ChatConversationOrm",
        back_populates="tool_evidence",
    )


class ChatToolArtifactOrm(Base):
    """Temporary conversation-scoped raw tool result with an opaque handle."""

    __tablename__ = "chat_tool_artifact"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "artifact_id",
            name="uq_chat_tool_artifact_conversation_artifact",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_conversation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artifact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(512), nullable=False)
    arguments: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    conversation: Mapped[ChatConversationOrm] = relationship(
        "ChatConversationOrm",
        back_populates="tool_artifacts",
    )


class ChatAgentSessionOrm(Base):
    """Persistent OpenAI Agents SDK session for one chat conversation."""

    __tablename__ = "chat_agent_session"
    __table_args__ = (
        CheckConstraint(
            "next_sequence >= 0",
            name="ck_chat_agent_session_next_sequence_non_negative",
        ),
    )

    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_conversation.id", ondelete="CASCADE"),
        primary_key=True,
    )
    next_sequence: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatAgentSessionItemOrm(Base):
    """Ordered JSON item persisted by the OpenAI Agents SDK Session protocol."""

    __tablename__ = "chat_agent_session_item"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_chat_agent_session_item_sequence",
        ),
        CheckConstraint(
            "sequence >= 0",
            name="ck_chat_agent_session_item_sequence_non_negative",
        ),
        Index(
            "ix_chat_agent_session_item_conversation_sequence",
            "conversation_id",
            "sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_agent_session.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    item_type: Mapped[str] = mapped_column(String(64), nullable=False)
    item_data: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
