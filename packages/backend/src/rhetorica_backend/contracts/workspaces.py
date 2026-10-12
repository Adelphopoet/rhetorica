"""Transport-neutral workspace membership and resource-sharing contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    ANALYST = "analyst"
    VIEWER = "viewer"


class WorkspaceResourceKind(StrEnum):
    DATABASE_CONNECTOR = "database_connector"
    LLM_CONNECTOR = "llm_connector"
    RAG_CONNECTOR = "rag_connector"
    DOCUMENTATION_CONNECTOR = "documentation_connector"
    GITLAB_CONNECTOR = "gitlab_connector"
    MCP_CONNECTOR = "mcp_connector"
    AIRFLOW_CONNECTOR = "airflow_connector"
    KUBERNETES_CONNECTOR = "kubernetes_connector"
    VICTORIAMETRICS_CONNECTOR = "victoriametrics_connector"
    MATTERMOST_CONNECTOR = "mattermost_connector"
    WORKSPACE_SKILL = "workspace_skill"


class WorkspaceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class WorkspaceItem(WorkspaceContract):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    is_active: bool = True
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class CreateWorkspaceRequest(WorkspaceContract):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    name: str = Field(min_length=1, max_length=255)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        return value.strip().lower()


class WorkspaceMembershipItem(WorkspaceContract):
    workspace_id: str
    user_id: str
    user_login: str | None = None
    user_display_name: str | None = None
    role: WorkspaceRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UpsertWorkspaceMembershipRequest(WorkspaceContract):
    user_id: str = Field(min_length=1, max_length=36)
    role: WorkspaceRole
    is_active: bool = True


class WorkspaceRequestContext(WorkspaceContract):
    """Server-created workspace binding; never accepted from a model tool call."""

    actor_user_id: str
    home_workspace_id: str
    workspace_id: str
    workspace_role: WorkspaceRole
    credential_type: str


class WorkspaceResourceGrantItem(WorkspaceContract):
    id: str
    resource_kind: WorkspaceResourceKind
    resource_id: str
    owner_workspace_id: str
    recipient_workspace_id: str
    alias: str
    is_active: bool
    created_by: str
    created_at: datetime
    updated_at: datetime


class WorkspaceResourceCatalogItem(WorkspaceContract):
    id: str
    name: str
    kind: WorkspaceResourceKind
    owner_workspace_id: str


class CreateWorkspaceResourceGrantRequest(WorkspaceContract):
    resource_kind: WorkspaceResourceKind
    resource_id: str = Field(min_length=1, max_length=128)
    owner_workspace_id: str = Field(min_length=1, max_length=128)
    recipient_workspace_id: str = Field(min_length=1, max_length=128)
    alias: str = Field(min_length=1, max_length=128)

    @field_validator("alias")
    @classmethod
    def normalize_alias(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("workspace_resource_alias_required")
        return normalized

    @model_validator(mode="after")
    def reject_local_grant(self) -> CreateWorkspaceResourceGrantRequest:
        if self.owner_workspace_id == self.recipient_workspace_id:
            raise ValueError("workspace_resource_grant_must_cross_workspaces")
        return self


class ResolvedWorkspaceResource(WorkspaceContract):
    resource_kind: WorkspaceResourceKind
    resource_id: str
    owner_workspace_id: str
    requested_workspace_id: str
    alias: str
    is_shared: bool


class WorkspacePolicyAuditEventItem(WorkspaceContract):
    id: str
    workspace_id: str
    actor_user_id: str
    target_type: str
    target_id: str
    action: str
    previous_value: dict[str, object] | None = None
    current_value: dict[str, object] | None = None
    created_at: datetime
