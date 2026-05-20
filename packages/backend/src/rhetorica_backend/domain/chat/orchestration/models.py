"""Typed orchestration boundary models used across chat runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Generic, Literal, Protocol, TypeVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from rhetorica_backend.contracts.chat import (
    ChatAskRequest,
    ChatAskResponse,
    DoneEvent,
    ErrorEvent,
    StreamEvent,
)
from rhetorica_backend.contracts.connectors import ConnectorItem

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.response_assembly import (
        AssembledChatTurn,
    )
    from rhetorica_backend.domain.chat.orchestration.session import OrchestrationSession
else:
    AssembledChatTurn = Any
    OrchestrationSession = Any


RuntimeCompletionKind = Literal[
    "capability_question",
    "llm_connector_missing",
    "runtime_completed",
    "cancelled",
]


class ExecutionZoneSnapshot(BaseModel):
    """Serializable execution zone snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service: str
    database: str
    schema_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("schema_name", "schema"),
        serialization_alias="schema",
    )
    connector_name: str | None = None


class SqlExecutionOutcome(BaseModel):
    """Serializable execution outcome emitted by the SQL pipeline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sql: str
    connector_name: str | None = None
    execution: dict[str, Any]
    preflight_status: str | None = None


class SqlPlanningContext(BaseModel):
    """Explicit SQL planning boundary derived from orchestration state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context_tables: list[str] = Field(default_factory=list)
    metadata_table_columns: dict[str, list[str]] = Field(default_factory=dict)
    semantic_grounding: dict[str, Any] | None = None
    execution_zone: ExecutionZoneSnapshot | None = None
    selected_database_connector: str | None = None
    generated_sql: str | None = None
    metadata_context_revision: int = 0


class TurnContextSnapshot(BaseModel):
    """Stable, serializable turn snapshot used by finalization and diagnostics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    warnings: list[str] = Field(default_factory=list)
    metadata_sources: list[str] = Field(default_factory=list)
    context_tables: list[str] = Field(default_factory=list)
    metadata_table_columns: dict[str, list[str]] = Field(default_factory=dict)
    semantic_grounding: dict[str, Any] | None = None
    execution_zone: ExecutionZoneSnapshot | None = None
    selected_database_connector: str | None = None
    generated_sql: str | None = None
    visualization_candidate: dict[str, Any] | None = None
    prompt_version: str | None = None
    metadata_context_revision: int = 0
    execution_attempts: list[SqlExecutionOutcome] = Field(default_factory=list)


class NoRuntimeTransportArtifact(BaseModel):
    """Runtime completed without transport-specific artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_type: Literal["none"] = "none"


class StreamingRuntimeTransportArtifact(BaseModel):
    """Runtime artifact carrying streaming events collected during execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_type: Literal["streaming_events"] = "streaming_events"
    events: list[StreamEvent] = Field(default_factory=list)


RuntimeTransportArtifact = Annotated[
    NoRuntimeTransportArtifact | StreamingRuntimeTransportArtifact,
    Field(discriminator="artifact_type"),
]


class OrchestrationSessionRuntimeSnapshot(Protocol):
    """Stable session shape exposed to runtime hosts and compatibility shims."""

    request: ChatAskRequest
    conversation_id: str | None
    db_connectors: list[ConnectorItem]


class ToolExecutorPort(Protocol):
    """Minimal tool executor contract required by shared runtime coordination."""

    def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        state: Any,
    ) -> dict[str, Any]: ...


TState = TypeVar("TState")


@dataclass(frozen=True, slots=True)
class PreparedRuntimeTurnInput(Generic[TState]):
    """Stable runtime snapshot after session bootstrap and turn preparation."""

    session: OrchestrationSessionRuntimeSnapshot
    request: ChatAskRequest
    db_connectors: list[ConnectorItem]
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    state: TState
    tool_executor: ToolExecutorPort
    protocol_repair_enabled: bool
    llm_client: Any


class RuntimeExecutionResult(BaseModel):
    """Transport runtime result returned from the shared session flow."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    llm_payload: dict[str, Any]
    llm_usages: list[dict[str, Any]] = Field(default_factory=list)
    transport_artifact: RuntimeTransportArtifact = Field(
        default_factory=lambda: NoRuntimeTransportArtifact()
    )


class PersistConversationHistory(BaseModel):
    """Explicit decision to persist the assembled turn into history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_type: Literal["persist"] = "persist"
    conversation_id: str = Field(min_length=1)


class SkipConversationHistory(BaseModel):
    """Explicit decision to skip persistence for the assembled turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_type: Literal["skip"] = "skip"
    reason: Literal["missing_conversation_id", "cancelled"]


HistoryPersistenceDecision = Annotated[
    PersistConversationHistory | SkipConversationHistory,
    Field(discriminator="decision_type"),
]


class CompletedSessionFlow(BaseModel):
    """Shared completed orchestration flow before transport persistence/emission."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    completion_kind: RuntimeCompletionKind
    session: OrchestrationSession
    turn: AssembledChatTurn
    llm_usages: list[dict[str, Any]] = Field(default_factory=list)
    transport_artifact: RuntimeTransportArtifact = Field(
        default_factory=lambda: NoRuntimeTransportArtifact()
    )
    history_persistence: HistoryPersistenceDecision


class FinalizationInput(BaseModel):
    """Typed payload passed into finalization/repair steps."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_final_text: str
    uncertainty_flags: list[str] = Field(default_factory=list)
    turn_context: TurnContextSnapshot


class ToolCallRequest(BaseModel):
    """Validated tool call request from LLM orchestration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    tool_call_id: str | None = None


class ToolCallResult(BaseModel):
    """Typed wrapper around tool execution payloads."""

    model_config = ConfigDict(extra="allow", frozen=True)

    ok: bool = False
    error_code: str | None = None
    can_continue: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)


class ExecuteToolCallPlan(BaseModel):
    """Tool loop step: execute the requested tool call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_type: Literal["execute"] = "execute"
    tool_call_id: str
    call_signature: tuple[str, str]


class DuplicateToolCallPlan(BaseModel):
    """Tool loop step: reuse a previously observed tool result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_type: Literal["duplicate"] = "duplicate"
    tool_call_id: str
    call_signature: tuple[str, str]
    tool_result: dict[str, Any]
    cached_result_available: bool
    recovery_kind: Literal["none", "duplicate_loop"] = "none"


ToolCallExecutionPlan = Annotated[
    ExecuteToolCallPlan | DuplicateToolCallPlan,
    Field(discriminator="plan_type"),
]


class ContinueToolResultDecision(BaseModel):
    """Tool loop step: keep iterating after the tool result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_type: Literal["continue"] = "continue"


class StopToolResultDecision(BaseModel):
    """Tool loop step: stop and surface a terminal payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_type: Literal["stop"] = "stop"
    payload: dict[str, Any]
    recovery_kind: Literal["none", "execution_error"] = "none"


ToolResultDecision = Annotated[
    ContinueToolResultDecision | StopToolResultDecision,
    Field(discriminator="decision_type"),
]


class FinalAnswerDecision(BaseModel):
    """Turn outcome: finalize the current visible content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_type: Literal["final_answer"] = "final_answer"
    raw_final_text: str
    uncertainty_flags: list[str] = Field(default_factory=list)
    finalization_mode: Literal["direct", "structured"] = "structured"


class ClarificationDecision(BaseModel):
    """Turn outcome: user clarification is the final answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_type: Literal["clarification"] = "clarification"
    answer_markdown: str
    uncertainty_flags: list[str] = Field(default_factory=lambda: ["needs_user_clarification"])
    finalization_mode: Literal["direct", "structured"] = "structured"


class NextToolDecision(BaseModel):
    """Turn outcome: continue the loop with another tool call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_type: Literal["next_tool"] = "next_tool"
    tool_request: ToolCallRequest
    content_for_history: str | None = None


class ToolBatchDecision(BaseModel):
    """Turn outcome: continue the loop with one or more tool calls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_type: Literal["tool_batch"] = "tool_batch"
    tool_requests: list[ToolCallRequest] = Field(default_factory=list, min_length=1)
    assistant_history_message: dict[str, Any] | None = None


class AbortDecision(BaseModel):
    """Turn outcome: abort current orchestration branch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_type: Literal["abort"] = "abort"
    reason: str


OrchestrationDecision = Annotated[
    (
        FinalAnswerDecision
        | ClarificationDecision
        | NextToolDecision
        | ToolBatchDecision
        | AbortDecision
    ),
    Field(discriminator="decision_type"),
]


class NonToolTurnCompletionInput(BaseModel):
    """Focused snapshot for terminal completion after a non-tool assistant turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_content: str
    finalizable_content: str
    has_current_tools: bool = False
    current_tool_names: list[str] = Field(default_factory=list)


class DecisionFinalizedTurnCompletion(BaseModel):
    """Completion produced from an explicit final/clarification decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    completion_type: Literal["decision_finalized"] = "decision_finalized"
    decision_type: Literal["final_answer", "clarification"]
    payload: dict[str, Any]


class SuccessfulTurnCompletion(BaseModel):
    """Completion produced from a validated acceptable final payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    completion_type: Literal["success"] = "success"
    payload: dict[str, Any]
    emit_visible_content: bool = True


class FallbackTurnCompletion(BaseModel):
    """Completion produced from abort or unacceptable payload fallback."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    completion_type: Literal["fallback"] = "fallback"
    reason: Literal["abort", "unacceptable_final_payload", "analysis_plan_incomplete"]
    payload: dict[str, Any]
    soft_fallback_applied: bool = False


class LoopLimitTurnCompletion(BaseModel):
    """Completion produced when the tool loop hits its iteration limit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    completion_type: Literal["loop_limit"] = "loop_limit"
    payload: dict[str, Any]


TurnCompletion = Annotated[
    (
        DecisionFinalizedTurnCompletion
        | SuccessfulTurnCompletion
        | FallbackTurnCompletion
        | LoopLimitTurnCompletion
    ),
    Field(discriminator="completion_type"),
]


class HistoryPersistenceResult(BaseModel):
    """Persistence outcome returned by the post-assembly finalization flow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: HistoryPersistenceDecision
    assistant_message_id: str | None = None


class TransportCompletionDiagnostics(BaseModel):
    """Normalized completion diagnostics shared by sync and streaming transports."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str | None = None
    assistant_message_id: str | None = None
    selected_database_connector: str | None = None
    generated_sql_present: bool = False
    execution_status: str | None = None
    warnings: list[str] = Field(default_factory=list)
    llm_calls: list[dict[str, Any]] = Field(default_factory=list)
    llm_total_tokens: int = 0
    llm_total_duration_ms: float = 0.0

    def to_log_fields(self, *, workspace_id: str) -> dict[str, Any]:
        return {
            "workspace_id": workspace_id,
            "conversation_id": self.conversation_id,
            "assistant_message_id": self.assistant_message_id,
            "selected_database_connector": self.selected_database_connector,
            "generated_sql_present": self.generated_sql_present,
            "execution_status": self.execution_status,
            "warnings": self.warnings,
            "llm_calls": self.llm_calls,
            "llm_total_tokens": self.llm_total_tokens,
            "llm_total_duration_ms": self.llm_total_duration_ms,
        }


class TransportFinalizationInput(BaseModel):
    """Typed input for shared post-assembly transport finalization."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    request: ChatAskRequest
    conversation_id: str | None = None
    completion_kind: RuntimeCompletionKind
    turn: AssembledChatTurn
    llm_usages: list[dict[str, Any]] = Field(default_factory=list)
    history_persistence: HistoryPersistenceDecision
    transport_events: list[StreamEvent] = Field(default_factory=list)


class SyncTransportFinalizationResult(BaseModel):
    """Final sync transport payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transport_kind: Literal["sync"] = "sync"
    response: ChatAskResponse
    diagnostics: TransportCompletionDiagnostics


class StreamingDoneTerminalResult(BaseModel):
    """Streaming finalization produced a terminal done event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    terminal_kind: Literal["done"] = "done"
    event: DoneEvent


class StreamingErrorTerminalResult(BaseModel):
    """Streaming finalization produced a terminal error event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    terminal_kind: Literal["error"] = "error"
    event: ErrorEvent


StreamingTerminalResult = Annotated[
    StreamingDoneTerminalResult | StreamingErrorTerminalResult,
    Field(discriminator="terminal_kind"),
]


class StreamingTransportFinalizationResult(BaseModel):
    """Final streaming transport payload including emission order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transport_kind: Literal["streaming"] = "streaming"
    events: list[StreamEvent] = Field(default_factory=list)
    terminal: StreamingTerminalResult
    diagnostics: TransportCompletionDiagnostics | None = None


TransportFinalizationResult = Annotated[
    SyncTransportFinalizationResult | StreamingTransportFinalizationResult,
    Field(discriminator="transport_kind"),
]
