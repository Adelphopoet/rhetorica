from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

WorkspaceSkillCapabilityFamily = Literal["sql_optimization_advisor"]
WorkspaceSkillEngineScope = Literal["clickhouse", "trino", "greenplum", "impala", "any"]
WorkspaceSkillSourceKind = Literal["github_markdown_url"]
WorkspaceSkillStatus = Literal["active", "inactive"]
WorkspaceSkillValidationStatus = Literal["valid", "invalid", "pending"]
WorkspaceSkillAssetKind = Literal["main", "compiled", "rule", "doc"]


class WorkspaceSkillAsset(BaseModel):
    """Resolved markdown asset bundled with the skill snapshot."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1024)
    source_url: str = Field(min_length=1, max_length=1024)
    kind: WorkspaceSkillAssetKind = "doc"
    title: str | None = Field(default=None, max_length=255)
    summary: str | None = Field(default=None, max_length=500)
    rule_id: str | None = Field(default=None, max_length=255)
    impact: str | None = Field(default=None, max_length=64)
    tags: list[str] = Field(default_factory=list)
    content_markdown: str = Field(default="", max_length=262_144)
    content_sha256: str | None = Field(default=None, max_length=128)
    frontmatter: dict[str, Any] = Field(default_factory=dict)


class WorkspaceSkillBundle(BaseModel):
    """Structured markdown bundle resolved from a skill source."""

    model_config = ConfigDict(extra="forbid")

    assets: list[WorkspaceSkillAsset] = Field(default_factory=list)


class WorkspaceSkillItem(BaseModel):
    """Workspace-level skill snapshot used by runtime and admin UI."""

    id: str
    workspace_id: str
    name: str
    capability_family: WorkspaceSkillCapabilityFamily = "sql_optimization_advisor"
    engine_scope: WorkspaceSkillEngineScope = "clickhouse"
    priority: int = Field(default=100, ge=0, le=10_000)
    source_kind: WorkspaceSkillSourceKind = "github_markdown_url"
    source_url: str = Field(min_length=1, max_length=1024)
    status: WorkspaceSkillStatus = "active"
    validation_status: WorkspaceSkillValidationStatus = "pending"
    title: str | None = Field(default=None, max_length=255)
    summary: str | None = Field(default=None, max_length=500)
    content_markdown: str = Field(default="", max_length=262_144)
    content_bundle: WorkspaceSkillBundle = Field(default_factory=WorkspaceSkillBundle)
    content_sha256: str | None = Field(default=None, max_length=128)
    last_validated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class UpsertWorkspaceSkillRequest(BaseModel):
    """Create or update a workspace skill."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    capability_family: WorkspaceSkillCapabilityFamily = "sql_optimization_advisor"
    engine_scope: WorkspaceSkillEngineScope = "clickhouse"
    priority: int = Field(default=100, ge=0, le=10_000)
    source_kind: WorkspaceSkillSourceKind = "github_markdown_url"
    source_url: str = Field(min_length=1, max_length=1024)
    status: WorkspaceSkillStatus = "active"


class DeleteWorkspaceSkillResponse(BaseModel):
    skill_id: str
    deleted: bool
