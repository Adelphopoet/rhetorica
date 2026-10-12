"""Versioned, transport-neutral contracts for executable analytical scenarios."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ValueType = Literal["string", "number", "boolean", "json", "table", "file", "files"]
RunStatus = Literal[
    "queued",
    "running",
    "waiting",
    "succeeded",
    "failed",
    "cancel_requested",
    "cancelled",
    "rejected",
]
StepStatus = Literal["pending", "running", "waiting", "succeeded", "failed", "skipped", "cancelled"]
TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled", "rejected"})


class WorkflowContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValueSpec(WorkflowContract):
    type: ValueType = "json"
    title: str = Field(default="", max_length=255)
    required: bool = True
    default: Any = None
    json_schema: dict[str, Any] | None = None


class InputBinding(WorkflowContract):
    source: Literal["constant", "input", "step"] = "constant"
    type: ValueType = "json"
    value: Any = None
    node_id: str | None = None
    path: list[str | int] = Field(default_factory=list, max_length=20)
    required: bool = True
    default: Any = None


class NodeBase(WorkflowContract):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    title: str = Field(min_length=1, max_length=255)
    inputs: dict[str, InputBinding] = Field(default_factory=dict)
    outputs: dict[str, ValueSpec] = Field(default_factory=dict)


class InputNode(NodeBase):
    type: Literal["input"] = "input"


class SqlNode(NodeBase):
    type: Literal["sql"] = "sql"
    connector_name: str = Field(default="", max_length=128)
    sql: str = Field(default="", max_length=20_000)
    row_limit: int = Field(default=5000, ge=1, le=1_048_575)
    timeout_ms: int = Field(default=300_000, ge=100, le=900_000)
    outputs: dict[str, ValueSpec] = Field(
        default_factory=lambda: {
            "table": ValueSpec(type="table"),
            "file": ValueSpec(type="file"),
            "execution_id": ValueSpec(type="string"),
            "row_count": ValueSpec(type="number"),
        }
    )


class PythonNode(NodeBase):
    type: Literal["python"] = "python"
    code: str = Field(default="", max_length=19_000)
    outputs: dict[str, ValueSpec] = Field(
        default_factory=lambda: {
            "result": ValueSpec(type="json"),
            "files": ValueSpec(type="files", required=False),
        }
    )


class LlmNode(NodeBase):
    type: Literal["llm"] = "llm"
    agent_id: str = Field(default="", max_length=36)
    instructions: str = Field(default="", max_length=12_000)
    output_format: Literal["text", "json"] = "text"
    allowed_tools: list[str] = Field(default_factory=list, max_length=100)
    outputs: dict[str, ValueSpec] = Field(
        default_factory=lambda: {"result": ValueSpec(type="string")}
    )


class ConditionRule(WorkflowContract):
    input: str = Field(default="value", min_length=1, max_length=80)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "is_empty"] = "eq"
    value: Any = None


class ConditionNode(NodeBase):
    type: Literal["condition"] = "condition"
    rules: list[ConditionRule] = Field(default_factory=lambda: [ConditionRule()], min_length=1)
    match: Literal["all", "any"] = "all"
    outputs: dict[str, ValueSpec] = Field(
        default_factory=lambda: {"branch": ValueSpec(type="boolean")}
    )


class ParallelNode(NodeBase):
    type: Literal["parallel"] = "parallel"


class JoinNode(NodeBase):
    type: Literal["join"] = "join"


class ApprovalNode(NodeBase):
    type: Literal["approval"] = "approval"
    message: str = Field(default="Проверьте результаты перед продолжением.", max_length=4000)


class ResultNode(NodeBase):
    type: Literal["result"] = "result"


ScenarioNode = Annotated[
    InputNode
    | SqlNode
    | PythonNode
    | LlmNode
    | ConditionNode
    | ParallelNode
    | JoinNode
    | ApprovalNode
    | ResultNode,
    Field(discriminator="type"),
]


class ScenarioEdge(WorkflowContract):
    source: str
    target: str
    branch: Literal["true", "false"] | None = None


class ScenarioWorkflowDefinition(WorkflowContract):
    schema_version: Literal[1] = 1
    parameters: dict[str, ValueSpec] = Field(default_factory=dict)
    nodes: list[ScenarioNode] = Field(default_factory=list, max_length=100)
    edges: list[ScenarioEdge] = Field(default_factory=list, max_length=500)
    allowed_tools: list[str] = Field(default_factory=list, max_length=100)


class NodePosition(WorkflowContract):
    x: float
    y: float


class ScenarioLayout(WorkflowContract):
    positions: dict[str, NodePosition] = Field(default_factory=dict)
    viewport: dict[str, float] = Field(default_factory=dict)


class ScenarioTestCase(WorkflowContract):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=255)
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_schema: dict[str, Any] | None = None


class ScenarioDraft(WorkflowContract):
    scenario_id: str
    revision: int
    workflow: ScenarioWorkflowDefinition
    layout: ScenarioLayout = Field(default_factory=ScenarioLayout)
    test_cases: list[ScenarioTestCase] = Field(default_factory=list)
    updated_at: datetime | None = None


class SaveScenarioDraftRequest(WorkflowContract):
    workspace_id: str = "default"
    expected_revision: int = Field(ge=0)
    workflow: ScenarioWorkflowDefinition
    layout: ScenarioLayout = Field(default_factory=ScenarioLayout)
    test_cases: list[ScenarioTestCase] = Field(default_factory=list, max_length=50)


class PublishScenarioRequest(WorkflowContract):
    workspace_id: str = "default"
    expected_revision: int = Field(ge=0)


class ScenarioVersion(WorkflowContract):
    id: str
    scenario_id: str
    version: int
    kind: Literal["legacy", "workflow"]
    workflow: ScenarioWorkflowDefinition
    created_at: datetime
    created_by: str | None = None


class WorkflowValidationIssue(WorkflowContract):
    code: str
    message: str
    node_id: str | None = None
    field: str | None = None


class WorkflowValidationResult(WorkflowContract):
    valid: bool
    issues: list[WorkflowValidationIssue] = Field(default_factory=list)


class StartScenarioRunRequest(WorkflowContract):
    workspace_id: str = "default"
    inputs: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str | None = None
    version: int | None = Field(default=None, ge=1)
    draft_revision: int | None = Field(default=None, ge=0)
    test_node_id: str | None = None
    test_inputs: dict[str, Any] | None = None
    expected_schema: dict[str, Any] | None = None


class ScenarioStepRun(WorkflowContract):
    node_id: str
    status: StepStatus
    attempt: int = 0
    operation_id: str | None = None
    input_ref: str | None = None
    output_ref: str | None = None
    inputs: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ScenarioRun(WorkflowContract):
    id: str
    scenario_id: str
    scenario_title: str
    version: int
    status: RunStatus
    conversation_id: str | None = None
    is_test: bool = False
    created_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    steps: list[ScenarioStepRun] = Field(default_factory=list)


class ScenarioRunEvent(WorkflowContract):
    sequence: int
    run_id: str
    type: str
    node_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ScenarioApprovalRequest(WorkflowContract):
    workspace_id: str = "default"
    approved: bool


class ScenarioAiProposalRequest(WorkflowContract):
    workspace_id: str = "default"
    agent_id: str
    instruction: str = Field(min_length=1, max_length=12_000)
    expected_revision: int = Field(ge=0)


class ScenarioAiProposal(WorkflowContract):
    base_revision: int
    summary: str
    workflow: ScenarioWorkflowDefinition
    validation: WorkflowValidationResult


class ScenarioAiProgress(WorkflowContract):
    phase: Literal["generating", "validating", "repairing"]
    message: str
    batch: int
    node_count: int
