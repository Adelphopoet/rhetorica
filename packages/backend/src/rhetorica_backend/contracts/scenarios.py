from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rhetorica_backend.contracts.chat import ChatAskResponse


class ScenarioScope(str):
    OFFICIAL = "official"
    PERSONAL = "personal"


class ScenarioStatus(str):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class ScenarioToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_run_sql: bool = True
    sql_guard_required: bool = True
    allowed_tools: list[str] = Field(default_factory=list)
    post_answer_checklist: list[str] = Field(default_factory=list)


class ScenarioConnectorBindings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_connector_name: str | None = Field(default=None, min_length=1, max_length=128)
    database_connector_name: str | None = Field(default=None, min_length=1, max_length=128)
    run_sql: bool | None = None


class ScenarioDefinitionItem(BaseModel):
    execution_kind: str = "legacy"
    id: str
    slug: str
    workspace_id: str
    owner_user_id: str | None = None
    scope: str
    title: str
    summary: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    prompt_template: str
    tool_policy: ScenarioToolPolicy = Field(default_factory=ScenarioToolPolicy)
    default_connector_bindings: ScenarioConnectorBindings = Field(
        default_factory=ScenarioConnectorBindings
    )
    status: str
    version: int
    published_at: str | None = None
    created_at: str
    updated_at: str


class CreateScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    scope: str = Field(default=ScenarioScope.PERSONAL)
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=280)
    description: str = Field(default="", max_length=4000)
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    prompt_template: str = Field(min_length=1, max_length=12000)
    tool_policy: ScenarioToolPolicy = Field(default_factory=ScenarioToolPolicy)
    default_connector_bindings: ScenarioConnectorBindings = Field(
        default_factory=ScenarioConnectorBindings
    )
    status: str = Field(default=ScenarioStatus.PUBLISHED)


class UpdateScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = Field(default=None, min_length=1, max_length=280)
    description: str | None = Field(default=None, max_length=4000)
    input_schema: dict[str, Any] | None = None
    prompt_template: str | None = Field(default=None, min_length=1, max_length=12000)
    tool_policy: ScenarioToolPolicy | None = None
    default_connector_bindings: ScenarioConnectorBindings | None = None
    status: str | None = None


class ScenarioRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=36)
    inputs: dict[str, Any] = Field(default_factory=dict)


class ScenarioRunResponse(ChatAskResponse):
    scenario_run_id: str
    scenario_definition_id: str
    rendered_prompt: str
