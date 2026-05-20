from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

ScalarSetting = str | int | float | bool

MetadataProvider = Literal["openmetadata"]
RagProvider = Literal["qdrant"]
RagDistance = Literal["cosine", "dot", "euclid"]
RagVerificationStatus = Literal["pending", "verified", "rejected"]
ConfluenceProvider = Literal["confluence"]
ConfluenceAuthType = Literal["bearer", "basic"]
JiraProvider = Literal["jira"]
JiraAuthType = Literal["bearer", "basic"]
MattermostProvider = Literal["mattermost"]


class LlmConnectionConfig(BaseModel):
    """Connection details for strict OpenAI Responses API endpoints."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1, max_length=1024)
    model: str = Field(min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=4096)
    endpoint_path: str = Field(default="/responses", min_length=1, max_length=255)
    verify_tls: bool = True
    temperature: float | None = Field(default=0.1, ge=0, le=2)
    max_tokens: int | None = Field(default=1_200, ge=1, le=32_000)
    headers: dict[str, str] = Field(default_factory=dict)
    extra_settings: dict[str, ScalarSetting] = Field(default_factory=dict)
    phase_capable: dict[str, bool] | None = Field(
        default=None,
        description="Phase-specific capability overrides (phase name -> enabled)",
    )


class UpsertLlmConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    connection: LlmConnectionConfig


class LlmConnectorItem(BaseModel):
    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    connection: LlmConnectionConfig
    has_api_key: bool
    capability_profile: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class OpenMetadataConnectionConfig(BaseModel):
    """Connection details for OpenMetadata and optional MCP table bridge."""

    model_config = ConfigDict(extra="forbid")

    provider: MetadataProvider = "openmetadata"
    base_url: str = Field(min_length=1, max_length=1024)
    api_token: str | None = Field(default=None, max_length=4096)
    verify_tls: bool = True
    mcp_tables_url: str | None = Field(default=None, max_length=1024)
    service_name: str | None = Field(default=None, max_length=255)
    database_name: str | None = Field(default=None, max_length=255)
    schema_name: str | None = Field(default=None, max_length=255)
    table_limit: int = Field(default=200, ge=1, le=2_000)
    fields: str = Field(default="columns,database,databaseSchema", min_length=1, max_length=255)
    extra_settings: dict[str, ScalarSetting] = Field(default_factory=dict)

    @field_validator("mcp_tables_url")
    @classmethod
    def _normalize_mcp_tables_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        raw = value.strip()
        if not raw:
            return None

        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return raw

        path = (parsed.path or "").rstrip("/")
        if not path:
            path = "/mcp"
        if path.endswith("/mcp/sse"):
            path = path[: -len("/sse")]

        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


class UpsertOpenMetadataConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    connection: OpenMetadataConnectionConfig


class OpenMetadataConnectorItem(BaseModel):
    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    connection: OpenMetadataConnectionConfig
    has_api_token: bool
    created_at: datetime
    updated_at: datetime


class RagConnectionConfig(BaseModel):
    """Connection details for Qdrant-backed RAG insights storage."""

    model_config = ConfigDict(extra="forbid")

    provider: RagProvider = "qdrant"
    base_url: str = Field(min_length=1, max_length=1024)
    api_key: str | None = Field(default=None, max_length=4096)
    collection_name: str = Field(default="rag_insights", min_length=1, max_length=255)
    vector_size: int = Field(default=384, ge=8, le=4096)
    distance: RagDistance = "cosine"
    prefer_grpc: bool = False
    embedding_model: str = Field(default="BAAI/bge-small-en-v1.5", min_length=1, max_length=255)
    confidence_threshold: float = Field(default=0.7, ge=0, le=1)
    extra_settings: dict[str, ScalarSetting] = Field(default_factory=dict)


class UpsertRagConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    connection: RagConnectionConfig


class RagConnectorItem(BaseModel):
    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    connection: RagConnectionConfig
    has_api_key: bool
    created_at: datetime
    updated_at: datetime


class ConfluenceConnectionConfig(BaseModel):
    """Connection details for Confluence search integration."""

    model_config = ConfigDict(extra="forbid")

    provider: ConfluenceProvider = "confluence"
    base_url: str = Field(min_length=1, max_length=1024)
    username: str | None = Field(default=None, max_length=255)
    api_token: str | None = Field(default=None, max_length=4096)
    auth_type: ConfluenceAuthType = "bearer"
    verify_tls: bool = True
    space_key: str | None = Field(default=None, max_length=64)
    page_limit: int = Field(default=10, ge=1, le=50)
    extra_settings: dict[str, ScalarSetting] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        raw = value.strip()
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return raw
        path = (parsed.path or "").rstrip("/")
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


class UpsertConfluenceConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    connection: ConfluenceConnectionConfig


class ConfluenceConnectorItem(BaseModel):
    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    connection: ConfluenceConnectionConfig
    has_api_token: bool
    created_at: datetime
    updated_at: datetime


class JiraConnectionConfig(BaseModel):
    """Connection details for Jira Server/Data Center integration."""

    model_config = ConfigDict(extra="forbid")

    provider: JiraProvider = "jira"
    base_url: str = Field(min_length=1, max_length=1024)
    username: str | None = Field(default=None, max_length=255)
    api_token: str | None = Field(default=None, max_length=4096)
    auth_type: JiraAuthType = "bearer"
    verify_tls: bool = True
    project_keys: list[str] = Field(default_factory=list, max_length=100)
    issue_limit: int = Field(default=10, ge=1, le=50)
    extra_settings: dict[str, ScalarSetting] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url_for_jira(cls, value: str) -> str:
        raw = value.strip()
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return raw
        path = (parsed.path or "").rstrip("/")
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))

    @field_validator("project_keys")
    @classmethod
    def _normalize_project_keys(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            candidate = item.strip().upper()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized


class UpsertJiraConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    connection: JiraConnectionConfig


class JiraConnectorItem(BaseModel):
    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    connection: JiraConnectionConfig
    has_api_token: bool
    created_at: datetime
    updated_at: datetime


class MattermostConnectionConfig(BaseModel):
    """Connection details for Mattermost/Tag bot transport."""

    model_config = ConfigDict(extra="forbid")

    provider: MattermostProvider = "mattermost"
    base_url: str = Field(default="https://chat.example.com", min_length=1, max_length=1024)
    bot_token: str | None = Field(default=None, max_length=4096, repr=False)
    bot_username: str | None = Field(default=None, max_length=255)
    verify_tls: bool = True
    listen_enabled: bool = True
    extra_settings: dict[str, ScalarSetting] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url_for_mattermost(cls, value: str) -> str:
        raw = value.strip()
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return raw
        path = (parsed.path or "").rstrip("/")
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))

    @field_validator("bot_username")
    @classmethod
    def _normalize_bot_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lstrip("@")
        return normalized or None


class UpsertMattermostConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    connection: MattermostConnectionConfig


class MattermostConnectorItem(BaseModel):
    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    connection: MattermostConnectionConfig
    has_bot_token: bool
    created_at: datetime
    updated_at: datetime


class RagInsightReviewItem(BaseModel):
    id: str
    workspace_id: str
    conversation_id: str
    message_id: str
    execution_id: str | None = None
    summary: str
    article_markdown: str | None = None
    answer_excerpt: str
    generated_sql: str | None = None
    context_tables: list[str] = Field(default_factory=list)
    metadata_sources: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    llm_confidence: float = Field(ge=0, le=1)
    llm_reason: str
    likes_count: int = Field(default=0, ge=0)
    verification_status: RagVerificationStatus
    human_verified: bool
    verified_by: str | None = None
    verified_at: str | None = None
    created_at: str
    updated_at: str


class RagInsightReviewActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    verified_by: str = Field(default="admin", min_length=1, max_length=255)


class RagInsightReviewActionResponse(BaseModel):
    record_id: str
    verification_status: RagVerificationStatus
    verified_by: str
    verified_at: str


class DeleteConnectorResponse(BaseModel):
    connector_id: str
    deleted: bool
