from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentSettings(BaseModel):
    """Safe runtime knobs exposed on an agent profile."""

    model_config = ConfigDict(extra="forbid")

    run_sql_allowed: bool = True
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=32_000)


class UpsertAgentProfileRequest(BaseModel):
    """Create or update a workspace-scoped agent profile."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    slug: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2_000)
    admin_prompt: str | None = Field(default=None, max_length=8_000)
    is_active: bool = True
    llm_connector_name: str | None = Field(default=None, max_length=128)
    allowed_database_connector_names: list[str] = Field(default_factory=list, max_length=100)
    allowed_rag_connector_names: list[str] = Field(default_factory=list, max_length=100)
    allowed_documentation_connector_names: list[str] = Field(default_factory=list, max_length=100)
    allowed_gitlab_connector_names: list[str] = Field(default_factory=list, max_length=100)
    allowed_mcp_connector_names: list[str] = Field(default_factory=list, max_length=100)
    allowed_airflow_connector_names: list[str] = Field(default_factory=list, max_length=100)
    allowed_kubernetes_connector_names: list[str] = Field(default_factory=list, max_length=100)
    allowed_victoriametrics_connector_names: list[str] = Field(default_factory=list, max_length=100)
    allowed_workspace_skill_ids: list[str] = Field(default_factory=list, max_length=100)
    settings: AgentSettings = Field(default_factory=AgentSettings)

    @field_validator(
        "allowed_database_connector_names",
        "allowed_rag_connector_names",
        "allowed_documentation_connector_names",
        "allowed_gitlab_connector_names",
        "allowed_mcp_connector_names",
        "allowed_airflow_connector_names",
        "allowed_kubernetes_connector_names",
        "allowed_victoriametrics_connector_names",
        "allowed_workspace_skill_ids",
    )
    @classmethod
    def _normalize_names(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            candidate = item.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    @field_validator("slug")
    @classmethod
    def _normalize_slug(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("agent_slug_required")
        return normalized


class AgentProfileItem(BaseModel):
    """Agent payload returned to API clients."""

    id: str
    workspace_id: str
    slug: str
    title: str
    description: str | None = None
    admin_prompt: str | None = None
    is_active: bool
    llm_connector_name: str | None = None
    allowed_database_connector_names: list[str] = Field(default_factory=list)
    allowed_rag_connector_names: list[str] = Field(default_factory=list)
    allowed_documentation_connector_names: list[str] = Field(default_factory=list)
    allowed_gitlab_connector_names: list[str] = Field(default_factory=list)
    allowed_mcp_connector_names: list[str] = Field(default_factory=list)
    allowed_airflow_connector_names: list[str] = Field(default_factory=list)
    allowed_kubernetes_connector_names: list[str] = Field(default_factory=list)
    allowed_victoriametrics_connector_names: list[str] = Field(default_factory=list)
    allowed_workspace_skill_ids: list[str] = Field(default_factory=list)
    settings: AgentSettings = Field(default_factory=AgentSettings)
    star_count: int = 0
    is_starred: bool = False
    is_favorite: bool = False
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentProfilePublicItem(BaseModel):
    """Agent payload returned to analyst-facing API clients."""

    id: str
    workspace_id: str
    slug: str
    title: str
    description: str | None = None
    is_active: bool
    llm_connector_name: str | None = None
    allowed_database_connector_names: list[str] = Field(default_factory=list)
    allowed_rag_connector_names: list[str] = Field(default_factory=list)
    allowed_documentation_connector_names: list[str] = Field(default_factory=list)
    allowed_gitlab_connector_names: list[str] = Field(default_factory=list)
    allowed_mcp_connector_names: list[str] = Field(default_factory=list)
    allowed_airflow_connector_names: list[str] = Field(default_factory=list)
    allowed_kubernetes_connector_names: list[str] = Field(default_factory=list)
    allowed_victoriametrics_connector_names: list[str] = Field(default_factory=list)
    allowed_workspace_skill_ids: list[str] = Field(default_factory=list)
    settings: AgentSettings = Field(default_factory=AgentSettings)
    star_count: int = 0
    is_starred: bool = False
    is_favorite: bool = False
    created_at: datetime
    updated_at: datetime


class AgentReactionResponse(BaseModel):
    agent_id: str
    starred: bool | None = None
    favorite: bool | None = None
    star_count: int


class LiteLlmAgentApiSpikeResult(BaseModel):
    """Static compatibility summary for the current LiteLLM OpenAPI shape."""

    source: str
    supports_agent_catalog: bool
    supported_paths: list[str]
    notes: list[str]
    raw_schema_summary: dict[str, Any] = Field(default_factory=dict)
