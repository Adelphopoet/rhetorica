from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from rhetorica_backend.infra.postgres.base import Base
from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
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
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    login: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    auth_provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    auth_subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(255))
    is_bootstrap_user: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    database: Mapped[str | None] = mapped_column(String(255))
    catalog: Mapped[str | None] = mapped_column(String(255))
    schema_name: Mapped[str | None] = mapped_column(String(255))
    secure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verify: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    extra_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LlmConnectorConfigOrm(Base):
    __tablename__ = "llm_connector_config"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_llm_connector_workspace_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key: Mapped[str | None] = mapped_column(String(4096))
    api_key_secret_ref: Mapped[str | None] = mapped_column(String(512))
    endpoint_path: Mapped[str] = mapped_column(
        String(255), nullable=False, default="/chat/completions"
    )
    temperature: Mapped[float | None] = mapped_column(Float)
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    headers: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    extra_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OpenMetadataConnectorConfigOrm(Base):
    __tablename__ = "openmetadata_connector_config"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_openmetadata_connector_workspace_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="openmetadata")
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    api_token: Mapped[str | None] = mapped_column(String(4096))
    api_token_secret_ref: Mapped[str | None] = mapped_column(String(512))
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mcp_tables_url: Mapped[str | None] = mapped_column(String(1024))
    service_name: Mapped[str | None] = mapped_column(String(255))
    database_name: Mapped[str | None] = mapped_column(String(255))
    schema_name: Mapped[str | None] = mapped_column(String(255))
    table_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    fields: Mapped[str] = mapped_column(
        String(255), nullable=False, default="columns,database,databaseSchema"
    )
    extra_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MetadataTableUsageOrm(Base):
    __tablename__ = "metadata_table_usage"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "connector_name",
            "table_fqn",
            name="uq_metadata_table_usage_scope_table",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    connector_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    table_fqn: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    positive_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    negative_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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


class ConfluenceConnectorConfigOrm(Base):
    __tablename__ = "confluence_connector_config"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_confluence_connector_workspace_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="confluence")
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    api_token: Mapped[str | None] = mapped_column(String(4096))
    api_token_secret_ref: Mapped[str | None] = mapped_column(String(512))
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False, default="bearer")
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    space_key: Mapped[str | None] = mapped_column(String(64))
    page_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    extra_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JiraConnectorConfigOrm(Base):
    __tablename__ = "jira_connector_config"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_jira_connector_workspace_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="jira")
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    api_token: Mapped[str | None] = mapped_column(String(4096))
    api_token_secret_ref: Mapped[str | None] = mapped_column(String(512))
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False, default="bearer")
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    project_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    issue_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
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

    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="mattermost")
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    bot_token: Mapped[str | None] = mapped_column(String(4096))
    bot_token_secret_ref: Mapped[str | None] = mapped_column(String(512))
    bot_username: Mapped[str | None] = mapped_column(String(255))
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    listen_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    extra_settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

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
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    title: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(String(500))
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_bundle: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    content_sha256: Mapped[str | None] = mapped_column(String(128))
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
