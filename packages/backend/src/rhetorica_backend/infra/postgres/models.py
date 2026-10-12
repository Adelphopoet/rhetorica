from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from rhetorica_backend.infra.postgres.base import Base
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column


class QueryExecutionHistoryOrm(Base):
    __tablename__ = "query_execution_history"

    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    engine: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    submitted_sql: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_sql: Mapped[str | None] = mapped_column(Text)

    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    row_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    row_count: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    query_id: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)

    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    violations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    preview_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    connection_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    snapshot_storage_key: Mapped[str | None] = mapped_column(String(1024))
    snapshot_columns: Mapped[list[str] | None] = mapped_column(JSON)
    snapshot_row_count: Mapped[int | None] = mapped_column(Integer)
    snapshot_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64))
    snapshot_owner_user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    snapshot_conversation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    snapshot_possibly_truncated: Mapped[bool | None] = mapped_column(Boolean)
    snapshot_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_delete_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserAccountOrm(Base):
    __tablename__ = "user_account"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "auth_provider",
            "auth_subject",
            name="uq_user_account_workspace_provider_subject",
        ),
        UniqueConstraint("workspace_id", "login", name="uq_user_account_workspace_login"),
        CheckConstraint(
            "api_rate_limit_per_hour IS NULL OR api_rate_limit_per_hour > 0",
            name="ck_user_account_api_rate_limit_positive",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    last_workspace_id: Mapped[str | None] = mapped_column(String(128))
    login: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    auth_provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    auth_subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(255))
    api_rate_limit_per_hour: Mapped[int | None] = mapped_column(Integer)
    is_bootstrap_user: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersonalAccessTokenOrm(Base):
    """Hash-only credential issued by a user for API and MCP access."""

    __tablename__ = "personal_access_token"
    __table_args__ = (
        UniqueConstraint("token_prefix", name="uq_personal_access_token_prefix"),
        UniqueConstraint("token_digest", name="uq_personal_access_token_digest"),
        CheckConstraint(
            "lifetime_days IN (90, 180, 365)",
            name="ck_personal_access_token_lifetime_days",
        ),
        Index(
            "ix_personal_access_token_owner_active",
            "user_id",
            "revoked_at",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user_account.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(48), nullable=False)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    lifetime_days: Mapped[int] = mapped_column(Integer, nullable=False)
    auto_renew: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OAuthClientOrm(Base):
    """Dynamically registered OAuth client; CIMD clients are resolved on demand."""

    __tablename__ = "oauth_client"
    __table_args__ = (
        Index(
            "ix_oauth_client_registration_source_created",
            "registration_source_digest",
            "created_at",
        ),
        Index("ix_oauth_client_last_used", "last_used_at"),
    )

    client_id: Mapped[str] = mapped_column(String(2048), primary_key=True)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_uri: Mapped[str | None] = mapped_column(Text)
    redirect_uris: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    grant_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    response_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    token_endpoint_auth_method: Mapped[str] = mapped_column(String(64), nullable=False)
    client_secret_prefix: Mapped[str | None] = mapped_column(String(48))
    client_secret_digest: Mapped[str | None] = mapped_column(String(64), unique=True)
    client_secret_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registration_source_digest: Mapped[str | None] = mapped_column(String(64))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OAuthAuthorizationTransactionOrm(Base):
    """One-time browser consent transaction created from an authorization request."""

    __tablename__ = "oauth_authorization_transaction"
    __table_args__ = (
        UniqueConstraint("transaction_prefix", name="uq_oauth_transaction_prefix"),
        UniqueConstraint("transaction_digest", name="uq_oauth_transaction_digest"),
        Index("ix_oauth_transaction_active", "expires_at", "consumed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    transaction_prefix: Mapped[str] = mapped_column(String(48), nullable=False)
    transaction_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_uri: Mapped[str | None] = mapped_column(Text)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    state: Mapped[str | None] = mapped_column(Text)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OAuthAuthorizationCodeOrm(Base):
    """Hash-only, one-time OAuth authorization code."""

    __tablename__ = "oauth_authorization_code"
    __table_args__ = (
        UniqueConstraint("code_prefix", name="uq_oauth_authorization_code_prefix"),
        UniqueConstraint("code_digest", name="uq_oauth_authorization_code_digest"),
        Index("ix_oauth_authorization_code_active", "expires_at", "consumed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    code_prefix: Mapped[str] = mapped_column(String(48), nullable=False)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user_account.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OAuthTokenGrantOrm(Base):
    """One generation in an OAuth token family; only digests are persisted."""

    __tablename__ = "oauth_token_grant"
    __table_args__ = (
        UniqueConstraint("access_token_digest", name="uq_oauth_access_token_digest"),
        UniqueConstraint("refresh_token_digest", name="uq_oauth_refresh_token_digest"),
        UniqueConstraint("family_id", "generation", name="uq_oauth_family_generation"),
        Index("ix_oauth_token_family", "family_id", "revoked_at"),
        Index("ix_oauth_access_active", "access_token_prefix", "access_expires_at"),
        Index("ix_oauth_refresh_active", "refresh_token_prefix", "refresh_expires_at"),
        Index("ix_oauth_grant_refresh_expires", "refresh_expires_at"),
        Index("ix_oauth_grant_revoked_at", "revoked_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    family_id: Mapped[str] = mapped_column(String(36), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user_account.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_prefix: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    access_token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    refresh_token_prefix: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    refresh_token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentProfileOrm(Base):
    __tablename__ = "agent_profile"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_agent_profile_workspace_slug"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    admin_prompt: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    llm_connector_name: Mapped[str | None] = mapped_column(String(128))
    allowed_database_connector_names: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    allowed_rag_connector_names: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    allowed_gitlab_connector_names: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    allowed_mcp_connector_names: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    allowed_airflow_connector_names: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    allowed_kubernetes_connector_names: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    allowed_victoriametrics_connector_names: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    allowed_documentation_connector_names: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    allowed_workspace_skill_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="SET NULL")
    )
    updated_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentStarOrm(Base):
    __tablename__ = "agent_star"
    __table_args__ = (UniqueConstraint("agent_id", "user_id", name="uq_agent_star_agent_user"),)

    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_profile.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentFavoriteOrm(Base):
    __tablename__ = "agent_favorite"
    __table_args__ = (UniqueConstraint("agent_id", "user_id", name="uq_agent_favorite_agent_user"),)

    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_profile.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_account.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConnectorConfigOrm(Base):
    __tablename__ = "connector_config"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_connector_workspace_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    engine: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    host: Mapped[str] = mapped_column(String(512), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password: Mapped[str | None] = mapped_column(String(2048))
    password_secret_ref: Mapped[str | None] = mapped_column(String(512))
    usage_description: Mapped[str | None] = mapped_column(Text)
    database: Mapped[str | None] = mapped_column(String(255))
    catalog: Mapped[str | None] = mapped_column(String(255))
    schema_name: Mapped[str | None] = mapped_column(String(255))
    secure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verify: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    extra_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    extra_credential_secret_refs: Mapped[dict[str, str]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LlmConnectorConfigOrm(Base):
    __tablename__ = "llm_connector_config"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_llm_connector_workspace_name"),
        CheckConstraint(
            "NOT is_default OR is_active",
            name="ck_llm_connector_default_is_active",
        ),
        CheckConstraint(
            "max_tokens_parameter IN ('max_tokens', 'max_completion_tokens')",
            name="ck_llm_connector_max_tokens_parameter",
        ),
        CheckConstraint("top_p >= 0 AND top_p <= 1", name="ck_llm_connector_top_p"),
        CheckConstraint(
            "reasoning_effort IN ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')",
            name="ck_llm_connector_reasoning_effort",
        ),
        Index(
            "uq_llm_connector_workspace_default",
            "workspace_id",
            unique=True,
            postgresql_where=text("is_default"),
            sqlite_where=text("is_default = 1"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key: Mapped[str | None] = mapped_column(String(4096))
    api_key_secret_ref: Mapped[str | None] = mapped_column(String(512))
    credential_set_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    endpoint_path: Mapped[str] = mapped_column(
        String(255), nullable=False, default="/chat/completions"
    )
    temperature: Mapped[float | None] = mapped_column(Float)
    top_p: Mapped[float | None] = mapped_column(Float)
    reasoning_effort: Mapped[str | None] = mapped_column(String(16))
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    max_tokens_parameter: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="max_tokens",
        server_default="max_tokens",
    )
    headers: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    extra_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RagConnectorConfigOrm(Base):
    __tablename__ = "rag_connector_config"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_rag_connector_workspace_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="qdrant")
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    api_key: Mapped[str | None] = mapped_column(String(4096))
    api_key_secret_ref: Mapped[str | None] = mapped_column(String(512))
    collection_name: Mapped[str] = mapped_column(
        String(255), nullable=False, default="rag_insights"
    )
    vector_size: Mapped[int] = mapped_column(Integer, nullable=False, default=384)
    distance: Mapped[str] = mapped_column(String(32), nullable=False, default="cosine")
    prefer_grpc: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    embedding_model: Mapped[str] = mapped_column(
        String(255), nullable=False, default="BAAI/bge-small-en-v1.5"
    )
    confidence_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    extra_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DocumentationConnectorConfigOrm(Base):
    __tablename__ = "documentation_connector_config"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_documentation_connector_workspace_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    visible_to_default_assistant: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="docsy_hugo")
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    index_url: Mapped[str | None] = mapped_column(String(1024))
    content_format: Mapped[str] = mapped_column(String(32), nullable=False, default="markdown")
    username: Mapped[str | None] = mapped_column(String(255))
    api_token: Mapped[str | None] = mapped_column(String(4096))
    api_token_secret_ref: Mapped[str | None] = mapped_column(String(512))
    usage_description: Mapped[str | None] = mapped_column(Text)
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_paths: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    exclude_paths: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=1440)
    qdrant_base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    qdrant_api_key: Mapped[str | None] = mapped_column(String(4096))
    qdrant_api_key_secret_ref: Mapped[str | None] = mapped_column(String(512))
    qdrant_collection_name: Mapped[str] = mapped_column(
        String(255), nullable=False, default="documentation_chunks"
    )
    qdrant_prefer_grpc: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    vector_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    embedding_base_url: Mapped[str | None] = mapped_column(String(1024))
    embedding_api_key: Mapped[str | None] = mapped_column(String(4096))
    embedding_api_key_secret_ref: Mapped[str | None] = mapped_column(String(512))
    embedding_model: Mapped[str] = mapped_column(
        String(255), nullable=False, default="BAAI/bge-small-en-v1.5"
    )
    embedding_endpoint_path: Mapped[str] = mapped_column(
        String(255), nullable=False, default="/v1/embeddings"
    )
    embedding_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    last_sync_status: Mapped[str | None] = mapped_column(String(32))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    extra_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DocumentationPageOrm(Base):
    __tablename__ = "documentation_page"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "connector_name",
            "page_ref",
            name="uq_documentation_page_scope_ref",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    connector_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    page_ref: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_path: Mapped[str | None] = mapped_column(String(2048))
    markdown_url: Mapped[str | None] = mapped_column(String(2048))
    summary: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DocumentationChunkOrm(Base):
    __tablename__ = "documentation_chunk"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "connector_name",
            "chunk_ref",
            name="uq_documentation_chunk_scope_ref",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    connector_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    page_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documentation_page.id", ondelete="CASCADE"), nullable=False
    )
    page_ref: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    chunk_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    qdrant_point_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DocumentationSyncRunOrm(Base):
    __tablename__ = "documentation_sync_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    connector_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    connector_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    pages_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunks_indexed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GitLabConnectorConfigOrm(Base):
    __tablename__ = "gitlab_connector_config"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_gitlab_connector_workspace_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="gitlab")
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    api_token: Mapped[str | None] = mapped_column(String(4096))
    api_token_secret_ref: Mapped[str | None] = mapped_column(String(512))
    use_when: Mapped[str] = mapped_column(Text, nullable=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_project_paths: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    default_group_paths: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    search_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    read_max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=1600)
    coding_archive_max_files: Mapped[int] = mapped_column(
        Integer, nullable=False, default=20_000, server_default="20000"
    )
    extra_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class McpConnectorConfigOrm(Base):
    __tablename__ = "mcp_connector_config"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_mcp_connector_workspace_name"),
        Index("ix_mcp_connector_workspace_id", "workspace_id"),
        Index("ix_mcp_connector_is_active", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    visible_to_external_mcp: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="generic")
    transport: Mapped[str] = mapped_column(String(32), nullable=False, default="streamable-http")
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    headers: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    secret_header_refs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    enabled_groups: Mapped[str | None] = mapped_column(String(512))
    allowed_tool_names: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=30.0)
    tool_name_prefix: Mapped[str | None] = mapped_column(String(64))
    llm_connector_name: Mapped[str | None] = mapped_column(String(128))
    usage_description: Mapped[str | None] = mapped_column(Text)
    last_sync_status: Mapped[str | None] = mapped_column(String(32))
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extra_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class McpToolCatalogOrm(Base):
    __tablename__ = "mcp_tool_catalog"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "exposed_tool_name",
            name="uq_mcp_tool_workspace_exposed_name",
        ),
        UniqueConstraint(
            "connector_id",
            "remote_tool_name",
            name="uq_mcp_tool_connector_remote_name",
        ),
        Index("ix_mcp_tool_workspace_id", "workspace_id"),
        Index("ix_mcp_tool_connector_id", "connector_id"),
        Index("ix_mcp_tool_is_active", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    connector_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mcp_connector_config.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    connector_name: Mapped[str] = mapped_column(String(128), nullable=False)
    remote_tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    exposed_tool_name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AirflowConnectorConfigOrm(Base):
    __tablename__ = "airflow_connector_config"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_airflow_connector_workspace_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="airflow")
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password: Mapped[str | None] = mapped_column(String(4096))
    password_secret_ref: Mapped[str | None] = mapped_column(String(512))
    use_when: Mapped[str] = mapped_column(Text, nullable=False)
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False, default="basic")
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    api_path: Mapped[str] = mapped_column(String(255), nullable=False, default="/api/v1")
    dag_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    dag_run_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    task_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    log_max_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=8000)
    timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=20.0)
    extra_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KubernetesConnectorConfigOrm(Base):
    __tablename__ = "kubernetes_connector_config"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_kubernetes_connector_workspace_name"),
        Index("ix_k8s_connector_workspace_id", "workspace_id"),
        Index("ix_k8s_connector_is_active", "is_active"),
        Index("ix_k8s_connector_visible_default", "visible_to_default_assistant"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    visible_to_default_assistant: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="kubernetes")
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    bearer_token: Mapped[str | None] = mapped_column(String(4096))
    bearer_token_secret_ref: Mapped[str | None] = mapped_column(String(512))
    use_when: Mapped[str] = mapped_column(Text, nullable=False)
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False, default="bearer")
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    namespace_allowlist: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    default_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    max_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    log_max_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=8000)
    timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=20.0)
    extra_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VictoriaMetricsConnectorConfigOrm(Base):
    __tablename__ = "victoriametrics_connector_config"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_victoriametrics_connector_workspace_name",
        ),
        Index("ix_vm_connector_workspace_id", "workspace_id"),
        Index("ix_vm_connector_is_active", "is_active"),
        Index("ix_vm_connector_visible_default", "visible_to_default_assistant"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    visible_to_default_assistant: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="victoriametrics")
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    bearer_token: Mapped[str | None] = mapped_column(String(4096))
    bearer_token_secret_ref: Mapped[str | None] = mapped_column(String(512))
    use_when: Mapped[str] = mapped_column(Text, nullable=False)
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    query_path: Mapped[str] = mapped_column(
        String(255), nullable=False, default="/prometheus/api/v1/query"
    )
    query_range_path: Mapped[str] = mapped_column(
        String(255), nullable=False, default="/prometheus/api/v1/query_range"
    )
    templates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=20.0)
    max_series: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    max_datapoints: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    extra_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MattermostConnectorConfigOrm(Base):
    __tablename__ = "mattermost_connector_config"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_mattermost_connector_workspace_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="mattermost")
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    bot_token: Mapped[str | None] = mapped_column(String(4096))
    bot_token_secret_ref: Mapped[str | None] = mapped_column(String(512))
    bot_username: Mapped[str | None] = mapped_column(String(255))
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    listen_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    send_sql_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ambient_listen_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ambient_channel_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    ambient_decision_prompt: Mapped[str | None] = mapped_column(Text)
    extra_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MattermostEventLedgerOrm(Base):
    __tablename__ = "mattermost_event_ledger"
    __table_args__ = (
        UniqueConstraint(
            "connector_id",
            "post_id",
            name="uq_mattermost_event_connector_post",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    connector_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    post_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MattermostThreadBindingOrm(Base):
    __tablename__ = "mattermost_thread_binding"
    __table_args__ = (
        UniqueConstraint(
            "connector_id",
            "channel_id",
            "thread_key",
            name="uq_mattermost_binding_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    connector_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    thread_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceSkillOrm(Base):
    __tablename__ = "workspace_skill"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_workspace_skill_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    capability_family: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    visible_to_default_assistant: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    title: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(String(500))
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_bundle: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    content_sha256: Mapped[str | None] = mapped_column(String(128))
    current_version_digest: Mapped[str | None] = mapped_column(String(128), index=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceSkillVersionOrm(Base):
    """Immutable skill content retained for pinned turn/task reads."""

    __tablename__ = "workspace_skill_version"
    __table_args__ = (
        UniqueConstraint(
            "skill_id",
            "version_digest",
            name="uq_workspace_skill_version_digest",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    skill_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    content_bundle: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MetricDefinitionOrm(Base):
    """ORM model for governed metric definitions."""

    __tablename__ = "metric_definitions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "metric_id",
            name="uq_metric_definitions_workspace_metric",
        ),
        UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_metric_definitions_workspace_name",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    metric_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    grain: Mapped[str] = mapped_column(String(64), nullable=False)
    allowed_dimensions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    default_filters: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    owner: Mapped[str | None] = mapped_column(String(128))
    certified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_tables: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    join_policy: Mapped[str | None] = mapped_column(String(64))
    freshness_hours: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
