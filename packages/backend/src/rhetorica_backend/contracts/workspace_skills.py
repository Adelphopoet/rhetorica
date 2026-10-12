from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

WorkspaceSkillCapabilityFamily = Literal["chat_instruction"]
WorkspaceSkillWritableCapabilityFamily = Literal["sql_optimization_advisor", "chat_instruction"]
WorkspaceSkillEngineScope = Literal["clickhouse", "trino", "greenplum", "impala", "any"]
WorkspaceSkillSourceKind = Literal[
    "github_markdown_url",
    "gitlab_bundle",
    "manual_markdown",
]
WorkspaceSkillStatus = Literal["active", "inactive"]
WorkspaceSkillValidationStatus = Literal["valid", "invalid", "pending"]
WorkspaceSkillAssetKind = Literal["main", "compiled", "rule", "doc"]


class WorkspaceSkillAsset(BaseModel):
    """Resolved markdown asset bundled with the skill snapshot."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1024)
    source_url: str | None = Field(default=None, max_length=1024)
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


class WorkspaceSkillVersionBinding(BaseModel):
    """Immutable skill version selected for one turn or durable agent task."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1, max_length=36)
    version_digest: str = Field(min_length=1, max_length=128)


class WorkspaceSkillManifestEntry(BaseModel):
    """Metadata for one persisted asset; content is deliberately omitted."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1024)
    kind: WorkspaceSkillAssetKind
    size_chars: int = Field(ge=0)
    title: str | None = Field(default=None, max_length=255)
    summary: str | None = Field(default=None, max_length=500)

    @classmethod
    def from_asset(cls, asset: WorkspaceSkillAsset) -> WorkspaceSkillManifestEntry:
        """Project a persisted asset without exposing its markdown content."""
        return cls(
            path=asset.path,
            kind=asset.kind,
            size_chars=len(asset.content_markdown),
            title=asset.title,
            summary=asset.summary,
        )


class WorkspaceSkillManifestPage(BaseModel):
    """Bounded page of one immutable skill version's manifest."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1, max_length=36)
    version_digest: str = Field(min_length=1, max_length=128)
    entries: list[WorkspaceSkillManifestEntry] = Field(default_factory=list)
    total_entries: int = Field(ge=0)
    next_cursor: str | None = Field(default=None, max_length=2048)
    truncated: bool = False


class WorkspaceSkillReadFragment(BaseModel):
    """Bounded content response for a path in an immutable skill version."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1, max_length=36)
    version_digest: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=1024)
    content: str
    offset: int = Field(ge=0)
    total_chars: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)
    truncated: bool = False
    available_files: list[WorkspaceSkillManifestEntry] | None = None


class WorkspaceSkillVersionSnapshot(BaseModel):
    """Persisted immutable content used after the current skill is re-authorized."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1, max_length=36)
    workspace_id: str = Field(min_length=1, max_length=128)
    version_digest: str = Field(min_length=1, max_length=128)
    source_url: str | None = Field(default=None, max_length=1024)
    content_markdown: str
    content_bundle: WorkspaceSkillBundle = Field(default_factory=WorkspaceSkillBundle)


class WorkspaceSkillItem(BaseModel):
    """Workspace-level skill snapshot used by runtime and admin UI."""

    id: str
    workspace_id: str
    name: str
    capability_family: WorkspaceSkillCapabilityFamily = "chat_instruction"
    engine_scope: WorkspaceSkillEngineScope = "clickhouse"
    priority: int = Field(default=100, ge=0, le=10_000)
    source_kind: WorkspaceSkillSourceKind = "github_markdown_url"
    source_url: str | None = Field(default=None, max_length=1024)
    status: WorkspaceSkillStatus = "active"
    validation_status: WorkspaceSkillValidationStatus = "pending"
    visible_to_default_assistant: bool = True
    title: str | None = Field(default=None, max_length=255)
    summary: str | None = Field(default=None, max_length=500)
    content_markdown: str = Field(default="", max_length=262_144)
    content_bundle: WorkspaceSkillBundle = Field(default_factory=WorkspaceSkillBundle)
    content_sha256: str | None = Field(default=None, max_length=128)
    version_digest: str | None = Field(default=None, max_length=128)
    last_validated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BaseUpsertWorkspaceSkillRequest(BaseModel):
    """Common fields for create or update workspace skill requests."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    capability_family: WorkspaceSkillWritableCapabilityFamily = "chat_instruction"
    engine_scope: WorkspaceSkillEngineScope = "clickhouse"
    priority: int = Field(default=100, ge=0, le=10_000)
    status: WorkspaceSkillStatus = "active"
    visible_to_default_assistant: bool = True

    @field_validator("capability_family")
    @classmethod
    def reject_removed_optimizer_family(cls, value: str) -> str:
        if value == "sql_optimization_advisor":
            raise ValueError(
                "workspace_skill_legacy_capability_family: use chat_instruction instead"
            )
        return value


class UpsertGithubWorkspaceSkillRequest(BaseUpsertWorkspaceSkillRequest):
    """Create or update a GitHub-backed workspace skill."""

    source_kind: Literal["github_markdown_url"] = "github_markdown_url"
    source_url: str = Field(min_length=1, max_length=1024)


class UpsertManualWorkspaceSkillRequest(BaseUpsertWorkspaceSkillRequest):
    """Create or update a manually entered workspace skill."""

    source_kind: Literal["manual_markdown"] = "manual_markdown"
    source_url: None = None
    content_markdown: str = Field(min_length=1, max_length=32_768)


class UpsertGitLabWorkspaceSkillRequest(BaseUpsertWorkspaceSkillRequest):
    """Import a bounded, read-only skill bundle through a configured connector."""

    source_kind: Literal["gitlab_bundle"] = "gitlab_bundle"
    connector_name: str = Field(min_length=1, max_length=128)
    project_path: str = Field(min_length=1, max_length=512)
    ref: str = Field(min_length=1, max_length=256)
    path: str = Field(default="", max_length=1024)


UpsertWorkspaceSkillRequest = Annotated[
    UpsertGithubWorkspaceSkillRequest
    | UpsertGitLabWorkspaceSkillRequest
    | UpsertManualWorkspaceSkillRequest,
    Field(discriminator="source_kind"),
]


class DeleteWorkspaceSkillResponse(BaseModel):
    skill_id: str
    deleted: bool
