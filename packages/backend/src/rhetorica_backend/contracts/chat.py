from __future__ import annotations

from datetime import datetime
from enum import Enum
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rhetorica_backend.contracts.analysis_document import AnalysisDocument
from rhetorica_backend.contracts.coding import CodingResourceSelection
from rhetorica_backend.contracts.execution import ExecuteSqlResponse
from rhetorica_backend.contracts.files import FileArtifactRef
from rhetorica_backend.contracts.visualization import VisualizationSpec


class StreamEventType(str, Enum):
    """SSE event types for streaming chat responses."""

    THINKING = "thinking"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    EXECUTION_STATUS = "execution_status"
    CHUNK = "chunk"
    DONE = "done"
    ERROR = "error"


class ExecutionStatusValue(str, Enum):
    """SQL execution status values."""

    QUEUED = "queued"
    RUNNING = "running"
    STREAMING_RESULTS = "streaming_results"
    DONE = "done"
    FAILED = "failed"


class AssistantFeedbackValue(str, Enum):
    """Assistant answer feedback labels from analysts."""

    USEFUL = "useful"
    WRONG = "wrong"
    UNSAFE = "unsafe"
    INCOMPLETE = "incomplete"


class FeedbackReason(str, Enum):
    """Specific reasons for negative feedback on assistant answers."""

    WRONG_SOURCE = "wrong_source"  # Wrong table or data source was used
    WRONG_METRIC = "wrong_metric"  # Wrong metric/calculation logic used
    UNSAFE_SQL = "unsafe_sql"  # SQL is potentially unsafe or incorrect
    INCOMPLETE = "incomplete"  # Answer is missing information or scope
    BAD_CHART = "bad_chart"  # Visualization is wrong or unclear
    OTHER = "other"  # Other issue not captured above


class ClarificationChoiceType(str, Enum):
    """Types of clarification requests that can be presented to the user."""

    CHOOSE_CONNECTOR = "choose_connector"
    CHOOSE_TABLE = "choose_table"
    CHOOSE_METRIC_DEFINITION = "choose_metric_definition"
    CHOOSE_TIME_RANGE = "choose_time_range"
    CONFIRM_ASSUMPTION = "confirm_assumption"
    APPROVE_EXPENSIVE_QUERY = "approve_expensive_query"


class ClaimVerificationStatus(str, Enum):
    """Verification status for an answer claim."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"


class EvidenceType(str, Enum):
    """Evidence source types in answer contract."""

    EXECUTION_RESULT = "execution_result"
    METADATA_ENTITY = "metadata_entity"
    GITLAB_FILE = "gitlab_file"
    AIRFLOW_RUNTIME = "airflow_runtime"
    KUBERNETES_RUNTIME = "kubernetes_runtime"
    METRICS_TIMESERIES = "metrics_timeseries"
    RAG_INSIGHT = "rag_insight"
    INTERNAL_DOCUMENTATION = "internal_documentation"


class ClarificationChoice(BaseModel):
    """Single choice in a clarification request."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=512)
    type: ClarificationChoiceType
    provenance_id: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] | None = None


class ClarificationRequest(BaseModel):
    """Typed clarification request presented to the user."""

    model_config = ConfigDict(extra="forbid")

    clarification_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=1024)
    choices: list[ClarificationChoice] = Field(min_length=1)
    type: ClarificationChoiceType


class AnswerClaim(BaseModel):
    """Atomic claim extracted from final answer."""

    claim_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1)
    status: ClaimVerificationStatus = ClaimVerificationStatus.VERIFIED


class AnswerEvidence(BaseModel):
    """Evidence item used to support claims."""

    evidence_id: str = Field(min_length=1, max_length=128)
    type: EvidenceType
    reference: str = Field(min_length=1, max_length=512)
    title: str | None = Field(default=None, max_length=512)
    snippet: str | None = None
    status: ClaimVerificationStatus = ClaimVerificationStatus.VERIFIED


class ClaimEvidenceLink(BaseModel):
    """Link between one claim and one or many evidence entries."""

    claim_id: str = Field(min_length=1, max_length=128)
    evidence_ids: list[str] = Field(default_factory=list)


class SemanticGroundingCandidate(BaseModel):
    """Compact semantic candidate kept in runtime trace."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    fqn: str = Field(min_length=1, max_length=512)
    service: str | None = Field(default=None, max_length=255)
    database: str | None = Field(default=None, max_length=255)
    schema_name: str | None = Field(default=None, alias="schema", max_length=255)
    relevance_score: float = 0.0
    matched_on: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    certification: str | None = Field(default=None, max_length=255)
    tier: str | None = Field(default=None, max_length=255)
    owners: list[str] = Field(default_factory=list)
    owner_teams: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    glossary_terms: list[str] = Field(default_factory=list)
    related_metrics: list[str] = Field(default_factory=list)
    quality_status: str | None = Field(default=None, max_length=64)
    quality_summary: dict[str, Any] | None = None
    usage_count: int = 0
    suspicious: bool = False


class SemanticGrounding(BaseModel):
    """Typed runtime semantic grounding for one turn."""

    model_config = ConfigDict(extra="forbid")

    chosen_table_fqn: str | None = Field(default=None, max_length=512)
    candidates: list[SemanticGroundingCandidate] = Field(default_factory=list)
    selection_rationale: str | None = None
    explicit_choice: bool = Field(
        default=False,
        description="True if table was explicitly chosen by user/LLM, False if derived from single candidate",
    )
    lineage_summary: str | None = None


class AgentRuntimeTrace(BaseModel):
    """Compact agent provenance persisted with a chat message."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=36)
    has_prompt: bool = False
    llm_connector_name: str | None = Field(default=None, max_length=128)


class WorkspaceSkillActivationTrace(BaseModel):
    """Audit metadata for a model-selected turn-scoped workspace skill."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1, max_length=36)
    content_sha256: str = Field(min_length=1, max_length=128)
    scope: Literal["turn"] = "turn"
    activated_by: Literal["model"] = "model"


class McpToolActivationTrace(BaseModel):
    """Exact remote MCP tools selected for one connector instance."""

    model_config = ConfigDict(extra="forbid")

    connector_name: str = Field(min_length=1, max_length=255)
    remote_tool_names: list[str] = Field(default_factory=list)


class ToolActivationTrace(BaseModel):
    """Model-selected tool surface persisted across conversation turns."""

    model_config = ConfigDict(extra="forbid")

    group_names: list[str] = Field(default_factory=list)
    mcp_tools: list[McpToolActivationTrace] = Field(default_factory=list)


class RuntimeContextTrace(BaseModel):
    """Compact runtime context persisted in capability trace."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_workspace_skill_reads(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "workspace_skills_read" not in value:
            return value

        normalized = dict(value)
        legacy_reads = normalized.pop("workspace_skills_read")
        if "workspace_skill_activations" in normalized:
            return normalized

        activations: list[dict[str, str]] = []
        if isinstance(legacy_reads, list):
            for item in legacy_reads:
                if not isinstance(item, dict):
                    continue
                skill_id = item.get("skill_id") or item.get("id")
                content_sha256 = item.get("content_sha256")
                content_markdown = item.get("content_markdown")
                if (
                    (not isinstance(content_sha256, str) or not content_sha256)
                    and isinstance(content_markdown, str)
                    and content_markdown
                ):
                    content_sha256 = sha256(content_markdown.encode("utf-8")).hexdigest()
                if not isinstance(skill_id, str) or not 1 <= len(skill_id) <= 36:
                    continue
                if not isinstance(content_sha256, str) or not 1 <= len(content_sha256) <= 128:
                    continue
                activations.append({
                    "skill_id": skill_id,
                    "content_sha256": content_sha256,
                    "scope": "turn",
                    "activated_by": "model",
                })
        normalized["workspace_skill_activations"] = activations
        return normalized

    metadata_table_columns: dict[str, list[str]] = Field(default_factory=dict)
    semantic_grounding: SemanticGrounding | None = None
    workspace_skill_activations: list[WorkspaceSkillActivationTrace] = Field(default_factory=list)
    tool_activation: ToolActivationTrace = Field(default_factory=ToolActivationTrace)
    conversation_state: dict[str, Any] | None = None
    langfuse_trace_id: str | None = Field(default=None, max_length=64)
    review_tool_call_count: int = Field(default=0, ge=0)
    review_tool_error_count: int = Field(default=0, ge=0)
    llm_calls: list[dict[str, Any]] = Field(default_factory=list)
    agent: AgentRuntimeTrace | None = None


class CapabilityTrace(BaseModel):
    """Runtime capability surface and provenance trace for one answer."""

    model_config = ConfigDict(extra="forbid")

    available_tools: list[str] = Field(default_factory=list)
    used_tools: list[str] = Field(default_factory=list)
    used_providers: list[str] = Field(default_factory=list)
    evidence_origin: list[str] = Field(default_factory=list)
    degraded_capabilities: list[str] = Field(default_factory=list)
    policy_decisions: list[str] = Field(default_factory=list)
    runtime_context: RuntimeContextTrace | None = None


class OptimizationCitation(BaseModel):
    """Citation attached to optimizer output."""

    kind: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=255)
    reference: str = Field(min_length=1, max_length=1024)


class OptimizationFinding(BaseModel):
    """One deterministic or advisory optimizer finding."""

    rule_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1)
    severity: str = Field(default="info", min_length=1, max_length=32)
    applies_to: list[str] = Field(default_factory=list)
    citations: list[OptimizationCitation] = Field(default_factory=list)


class OptimizationAction(BaseModel):
    """One optimizer recommendation or applied rewrite."""

    action_id: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1)
    sql: str | None = None
    applied: bool = False
    citations: list[OptimizationCitation] = Field(default_factory=list)


class OptimizationTrace(BaseModel):
    """Pre-execution SQL optimization trace."""

    optimizer_used: bool = False
    optimizer_provider: str | None = Field(default=None, max_length=255)
    input_sql: str | None = None
    optimized_sql_draft: str | None = None
    input_sql_hash: str | None = Field(default=None, max_length=128)
    optimized_sql_hash: str | None = Field(default=None, max_length=128)
    accepted_or_rejected: str | None = Field(default=None, max_length=32)
    applied: bool = False
    risk_level: str | None = Field(default=None, max_length=32)
    confidence: float | None = Field(default=None, ge=0, le=1)
    engine_scope: str | None = Field(default=None, max_length=64)
    rules_triggered: list[str] = Field(default_factory=list)
    findings: list[OptimizationFinding] = Field(default_factory=list)
    recommended_actions: list[OptimizationAction] = Field(default_factory=list)
    citations: list[OptimizationCitation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    skills_used: list[str] = Field(default_factory=list)
    evidence_used: list[str] = Field(default_factory=list)


class StreamEventBase(BaseModel):
    """Base class for all stream events."""

    type: StreamEventType
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class ThinkingEvent(StreamEventBase):
    """LLM thinking/reasoning block."""

    type: StreamEventType = StreamEventType.THINKING
    content: str
    delta: bool = False


class ToolCallStartEvent(StreamEventBase):
    """Tool execution started."""

    type: StreamEventType = StreamEventType.TOOL_CALL_START
    tool_name: str
    tool_call_id: str | None = None
    arguments: dict | None = None


class ToolCallEndEvent(StreamEventBase):
    """Tool execution completed."""

    type: StreamEventType = StreamEventType.TOOL_CALL_END
    tool_name: str
    tool_call_id: str | None = None
    result_summary: str
    success: bool = True


class ExecutionStatusEvent(StreamEventBase):
    """SQL execution progress update."""

    type: StreamEventType = StreamEventType.EXECUTION_STATUS
    status: ExecutionStatusValue
    message: str | None = None


class ChunkEvent(StreamEventBase):
    """LLM response chunk (final answer streaming)."""

    type: StreamEventType = StreamEventType.CHUNK
    content: str


class DoneEvent(StreamEventBase):
    """Stream completed successfully."""

    type: StreamEventType = StreamEventType.DONE
    conversation_id: str | None = None
    answer: str
    generated_sql: str | None = None
    execution: ExecuteSqlResponse | None = None
    analysis_document: AnalysisDocument | None = None
    visualization: VisualizationSpec | None = None
    selected_database_connector: str | None = None
    metadata_sources: list[str] = Field(default_factory=list)
    context_tables: list[str] = Field(default_factory=list)
    claims: list[AnswerClaim] = Field(default_factory=list)
    evidence: list[AnswerEvidence] = Field(default_factory=list)
    claim_evidence_links: list[ClaimEvidenceLink] = Field(default_factory=list)
    uncertainty_flags: list[str] = Field(default_factory=list)
    capability_trace: CapabilityTrace = Field(default_factory=CapabilityTrace)
    optimization_trace: OptimizationTrace | None = None
    warnings: list[str] = Field(default_factory=list)
    cancelled: bool = False
    assistant_message_id: str | None = None
    clarification_request: ClarificationRequest | None = None
    files: list[FileArtifactRef] = Field(default_factory=list)


class ErrorEvent(StreamEventBase):
    """Stream failed."""

    type: StreamEventType = StreamEventType.ERROR
    code: str
    message: str
    details: dict | None = None


StreamEvent = (
    ThinkingEvent
    | ToolCallStartEvent
    | ToolCallEndEvent
    | ExecutionStatusEvent
    | ChunkEvent
    | DoneEvent
    | ErrorEvent
)


class ChatActorContext(BaseModel):
    """Trusted compact requester identity for one internal chat turn."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["web", "mattermost"]
    login: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    role: str | None = Field(default=None, min_length=1, max_length=64)
    auth_provider: str | None = Field(default=None, min_length=1, max_length=64)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=128)
    external_user_id: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator(
        "login",
        "display_name",
        "role",
        "auth_provider",
        "workspace_id",
        "external_user_id",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class ChatAskRequest(BaseModel):
    """Internal chat request used by orchestration and privileged callers."""

    model_config = ConfigDict(extra="forbid")

    question: str = ""
    attachment_ids: list[str] = Field(default_factory=list, max_length=5)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=36)
    agent_id: str | None = Field(default=None, min_length=1, max_length=36)
    agent_prompt: str | None = Field(default=None, max_length=8_000)
    agent_allowed_database_connector_names: list[str] | None = None
    agent_allowed_rag_connector_names: list[str] | None = None
    agent_allowed_documentation_connector_names: list[str] | None = None
    agent_allowed_gitlab_connector_names: list[str] | None = None
    agent_allowed_mcp_connector_names: list[str] | None = None
    agent_allowed_airflow_connector_names: list[str] | None = None
    agent_allowed_kubernetes_connector_names: list[str] | None = None
    agent_allowed_victoriametrics_connector_names: list[str] | None = None
    agent_allowed_workspace_skill_ids: list[str] | None = None
    agent_temperature: float | None = Field(default=None, ge=0, le=2)
    agent_max_tokens: int | None = Field(default=None, ge=1, le=32_000)
    llm_connector_name: str | None = Field(default=None, min_length=1, max_length=128)
    database_connector_name: str | None = Field(default=None, min_length=1, max_length=128)
    run_sql: bool = True
    coding_mode: bool = False
    coding_resources: CodingResourceSelection | None = None
    selected_clarification_id: str | None = Field(default=None, max_length=64)
    selected_clarification_value: str | None = Field(default=None, max_length=256)
    ambient_context_markdown: str | None = Field(default=None, max_length=16_000)
    source_provider: str | None = Field(default=None, min_length=1, max_length=32)
    source_message_id: str | None = Field(default=None, min_length=1, max_length=128)
    actor_context: ChatActorContext | None = None

    @model_validator(mode="after")
    def _require_text_or_attachment(self) -> ChatAskRequest:
        if not self.question.strip() and not self.attachment_ids:
            raise ValueError("question or attachment_ids is required")
        return self


class AnalystChatAskRequest(BaseModel):
    """Analyst-facing chat request without transport-level connector pinning."""

    model_config = ConfigDict(extra="forbid")

    question: str = ""
    attachment_ids: list[str] = Field(default_factory=list, max_length=5)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=36)
    agent_id: str | None = Field(default=None, min_length=1, max_length=36)
    run_sql: bool = True
    coding_mode: bool = False
    coding_resources: CodingResourceSelection | None = None
    selected_clarification_id: str | None = Field(default=None, max_length=64)
    selected_clarification_value: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _require_text_or_attachment(self) -> AnalystChatAskRequest:
        if not self.question.strip() and not self.attachment_ids:
            raise ValueError("question or attachment_ids is required")
        return self


class ChatAskResponse(BaseModel):
    """Chat response with optional SQL execution payload and conversation context."""

    answer: str
    conversation_id: str | None = None
    generated_sql: str | None = None
    selected_database_connector: str | None = None
    metadata_sources: list[str] = Field(default_factory=list)
    context_tables: list[str] = Field(default_factory=list)
    claims: list[AnswerClaim] = Field(default_factory=list)
    evidence: list[AnswerEvidence] = Field(default_factory=list)
    claim_evidence_links: list[ClaimEvidenceLink] = Field(default_factory=list)
    uncertainty_flags: list[str] = Field(default_factory=list)
    capability_trace: CapabilityTrace = Field(default_factory=CapabilityTrace)
    optimization_trace: OptimizationTrace | None = None
    execution: ExecuteSqlResponse | None = None
    analysis_document: AnalysisDocument | None = None
    visualization: VisualizationSpec | None = None
    warnings: list[str] = Field(default_factory=list)
    assistant_message_id: str | None = None
    clarification_request: ClarificationRequest | None = None
    files: list[FileArtifactRef] = Field(default_factory=list)


class MessageFeedbackRequest(BaseModel):
    """Request to set feedback label for assistant message."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=36)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    feedback: AssistantFeedbackValue
    reason: FeedbackReason | None = Field(
        default=None,
        description="Specific reason for negative feedback (required for negative feedback types)",
    )
    comment: str | None = Field(
        default=None,
        max_length=1024,
        description="Optional free-text comment explaining the feedback",
    )
    what_went_wrong: str | None = Field(default=None, max_length=4_000)
    expected_answer: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def validate_expanded_feedback(self) -> MessageFeedbackRequest:
        self.what_went_wrong = (
            self.what_went_wrong.strip() if self.what_went_wrong is not None else None
        )
        self.expected_answer = (
            self.expected_answer.strip() if self.expected_answer is not None else None
        )
        if self.feedback == AssistantFeedbackValue.USEFUL:
            if self.what_went_wrong is not None or self.expected_answer is not None:
                raise ValueError("useful_feedback_must_not_include_expanded_details")
            return self
        if self.feedback == AssistantFeedbackValue.WRONG and (
            not self.what_went_wrong or not self.expected_answer
        ):
            raise ValueError("wrong_feedback_requires_expanded_details")
        if (self.what_went_wrong is None) != (self.expected_answer is None):
            raise ValueError("what_went_wrong_and_expected_answer_must_be_provided_together")
        return self


class MessageFeedbackResponse(BaseModel):
    """Feedback update result."""

    message_id: str
    conversation_id: str
    feedback: AssistantFeedbackValue
    feedback_updated_at: str
    reason: FeedbackReason | None = None
    comment: str | None = None
    what_went_wrong: str | None = None
    expected_answer: str | None = None


class CreateConversationRequest(BaseModel):
    """Request to create a new chat conversation."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=255)
    agent_id: str | None = Field(default=None, min_length=1, max_length=36)


class ConversationTokenUsageItem(BaseModel):
    """Cumulative provider-reported LLM token usage for one conversation."""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_write_input_tokens: int = Field(default=0, ge=0)
    cache_usage_reported_calls: int = Field(default=0, ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def _total_matches_components(self) -> ConversationTokenUsageItem:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        return self


class ConversationItem(BaseModel):
    """Summary of a chat conversation."""

    id: str
    workspace_id: str
    title: str | None
    agent_id: str | None = None
    created_at: str
    updated_at: str
    message_count: int = 0
    token_usage: ConversationTokenUsageItem


class ConversationDetailItem(BaseModel):
    """Full conversation with messages."""

    id: str
    workspace_id: str
    title: str | None
    agent_id: str | None = None
    created_at: str
    updated_at: str
    token_usage: ConversationTokenUsageItem
    messages: list[MessageItem] = Field(default_factory=list)


class MessageItem(BaseModel):
    """Single message within a conversation."""

    journal_events: list[dict[str, Any]] | None = None

    id: str
    conversation_id: str
    role: str
    content: str
    agent_id: str | None = None
    created_at: str
    generated_sql: str | None = None
    execution_id: str | None = None
    context_tables: list[str] = Field(default_factory=list)
    metadata_sources: list[str] = Field(default_factory=list)
    claims: list[AnswerClaim] = Field(default_factory=list)
    evidence: list[AnswerEvidence] = Field(default_factory=list)
    claim_evidence_links: list[ClaimEvidenceLink] = Field(default_factory=list)
    uncertainty_flags: list[str] = Field(default_factory=list)
    capability_trace: CapabilityTrace = Field(default_factory=CapabilityTrace)
    optimization_trace: OptimizationTrace | None = None
    selected_connector: str | None = None
    analysis_document: AnalysisDocument | None = None
    visualization: VisualizationSpec | None = None
    warnings: list[str] = Field(default_factory=list)
    feedback: AssistantFeedbackValue | None = None
    feedback_updated_at: str | None = None
    feedback_reason: FeedbackReason | None = None
    feedback_comment: str | None = None
    feedback_what_went_wrong: str | None = None
    feedback_expected_answer: str | None = None
    files: list[FileArtifactRef] = Field(default_factory=list)


ConversationDetailItem.model_rebuild()


# =============================================================================
# Repair Turn contracts
# =============================================================================


class RepairTurnRequest(BaseModel):
    """Request to generate a repair/fixed answer based on negative feedback."""

    model_config = ConfigDict(extra="forbid")

    # Original message context
    message_id: str = Field(min_length=1, max_length=36)
    conversation_id: str = Field(min_length=1, max_length=36)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    original_question: str = Field(min_length=1)
    # Original answer context
    original_answer: str = Field(min_length=1)
    generated_sql: str | None = None
    executed_sql: str | None = None
    context_tables: list[str] = Field(default_factory=list)
    metadata_sources: list[str] = Field(default_factory=list)
    evidence: list[AnswerEvidence] = Field(default_factory=list)
    claims: list[AnswerClaim] = Field(default_factory=list)
    claim_evidence_links: list[ClaimEvidenceLink] = Field(default_factory=list)
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    # Feedback context
    feedback_reason: FeedbackReason = FeedbackReason.OTHER
    feedback_comment: str | None = Field(default=None, max_length=1024)
    what_went_wrong: str | None = Field(default=None, max_length=4_000)
    expected_answer: str | None = Field(default=None, max_length=4_000)


class RepairTurnResponse(BaseModel):
    """Response from repair turn generation."""

    repair_question: str
    original_conversation_id: str
    original_message_id: str


class CardinalityBucket(str):
    """Cardinality bucket classification."""

    LOW = "low"  # < 100 distinct values
    MEDIUM = "medium"  # 100 - 10000 distinct values
    HIGH = "high"  # > 10000 distinct values


class ProfileColumnSummary(BaseModel):
    """Column-level profile summary for profiling tools."""

    name: str = Field(min_length=1, max_length=128)
    null_rate: float | None = Field(default=None, ge=0.0, le=100.0)
    cardinality_bucket: str = Field(default=CardinalityBucket.LOW)
    top_values: list[str] = Field(default_factory=list, max_length=10)


class ProfileRowCounts(BaseModel):
    """Row count estimate for profiling."""

    table: str = Field(min_length=1, max_length=256)
    table_schema: str | None = Field(default=None, alias="schema", max_length=128)
    connector: str = Field(min_length=1, max_length=128)
    row_count: int | None = Field(default=None, ge=0)
    is_estimate: bool = False


class ProfileFreshness(BaseModel):
    """Freshness data for profiling."""

    dimensions: list[ProfileColumnSummary] | None = None
    min_max_values: dict[str, str] | None = None
    stale: bool = False
    freshness_warning: str | None = None


class ProfileWarnings(BaseModel):
    """Warning flags from profiling."""

    stale: bool = False
    high_cardinality: bool = False
    sparse: bool = False
    unsupported_engine: bool = False
    message: str | None = None


class ProfileSummary(BaseModel):
    """Complete profile summary returned by profiling tools."""

    ok: bool = True
    tool_name: str = Field(min_length=1, max_length=64)
    row_counts: list[ProfileRowCounts] = Field(default_factory=list)
    freshness: ProfileFreshness | None = None
    column_summary: list[ProfileColumnSummary] | None = None
    warnings: ProfileWarnings | None = None
    token_capped: bool = False
    row_capped: bool = False
    truncated: bool = False
    top_values_cap: int = 10
