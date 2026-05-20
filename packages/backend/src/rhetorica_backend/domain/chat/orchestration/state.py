"""Tool orchestration state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from rhetorica_backend.contracts.chat import OptimizationTrace, SemanticGrounding
from rhetorica_backend.contracts.execution import ExecuteSqlResponse
from rhetorica_backend.contracts.visualization import VisualizationSpec
from rhetorica_backend.domain.chat.orchestration.analysis_artifacts import (
    AnalysisCheckpoint,
    AnalysisPlan,
    AnalysisWorkerResult,
)
from rhetorica_backend.domain.chat.orchestration.capability_registry import (
    RuntimeCapabilityState,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    ExecutionZoneSnapshot,
    SqlExecutionOutcome,
    SqlPlanningContext,
    ToolCallResult,
    TurnContextSnapshot,
)

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.llm.llm_client import LlmCallUsage


@dataclass(frozen=True)
class ExecutionZone:
    """Executable metadata zone resolved for the current SQL planning context."""

    service: str
    database: str
    schema: str | None = None
    connector_name: str | None = None


@dataclass
class SqlExecutionAttempt:
    """One concrete SQL execution attempt issued during the turn."""

    sql: str
    connector_name: str | None
    execution: ExecuteSqlResponse
    preflight_status: str | None = None


SchemaSnapshotSource = Literal[
    "catalog_search",
    "entity_details",
    "metadata_loader",
    "runtime_lookup",
    "runtime_describe",
    "runtime_probe",
]


@dataclass(frozen=True)
class TableSchemaSnapshot:
    """Typed schema snapshot used by SQL preflight hydration."""

    columns: frozenset[str]
    complete: bool
    source: SchemaSnapshotSource
    column_types: dict[str, str] = field(default_factory=dict)
    column_descriptions: dict[str, str] = field(default_factory=dict)


@dataclass
class ToolOrchestrationState:
    """State for LLM tool orchestration."""

    warnings: list[str] = field(default_factory=list)
    metadata_sources: set[str] = field(default_factory=set)
    context_tables: list[str] = field(default_factory=list)
    metadata_table_columns: dict[str, set[str]] = field(default_factory=dict)
    metadata_table_schemas: dict[str, TableSchemaSnapshot] = field(default_factory=dict)
    semantic_grounding: SemanticGrounding | None = None
    execution_zone: ExecutionZone | None = None
    generated_sql: str | None = None
    selected_database_connector: str | None = None
    execution: ExecuteSqlResponse | None = None
    visualization_candidate: VisualizationSpec | None = None
    optimization_trace: OptimizationTrace = field(default_factory=OptimizationTrace)
    execution_attempts: list[SqlExecutionAttempt] = field(default_factory=list)
    previous_tool_calls: list[tuple[str, str]] = field(default_factory=list)
    tool_call_context_revisions: dict[tuple[str, str], int] = field(default_factory=dict)
    tool_call_results: dict[tuple[str, str], dict[str, object]] = field(
        default_factory=dict
    )
    metadata_context_revision: int = 0
    conversation_id: str | None = None
    prompt_version: str | None = None
    tool_call_count: int = 0
    parse_failure_count: int = 0
    schema_valid_first_pass: int = 0
    schema_repaired: int = 0
    schema_failed: int = 0
    preflight_pass: int = 0
    preflight_fail: int = 0
    preflight_uncertain: int = 0
    unavailable_context_connector_fail: int = 0
    protocol_repair_attempts: int = 0
    capability_state: RuntimeCapabilityState = field(default_factory=RuntimeCapabilityState)
    analysis_plan: AnalysisPlan | None = None
    worker_results: list[AnalysisWorkerResult] = field(default_factory=list)
    checkpoint: AnalysisCheckpoint | None = None
    checkpoint_persist: Callable[[ToolOrchestrationState], None] | None = None
    llm_usages: list[LlmCallUsage] = field(default_factory=list)
    discovered_metrics: list[dict[str, Any]] = field(default_factory=list)

    def add_warning(self, warning: str) -> None:
        """Append a warning once to keep state transitions stable."""
        normalized = warning.strip()
        if normalized and normalized not in self.warnings:
            self.warnings.append(normalized)

    def record_tool_result(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> ToolCallResult:
        """Persist the latest tool result behind a typed boundary."""
        args_json = str(arguments)
        self.tool_call_results[(tool_name, args_json)] = result
        return ToolCallResult.model_validate(
            {
                "ok": bool(result.get("ok", False)),
                "error_code": result.get("error_code"),
                "can_continue": bool(result.get("can_continue", True)),
                "payload": result,
            }
        )

    def build_sql_planning_context(self) -> SqlPlanningContext:
        """Build an explicit SQL planning context instead of leaking whole state."""
        return SqlPlanningContext.model_validate(
            {
                "context_tables": list(self.context_tables),
                "metadata_table_columns": {
                    table_name: sorted(columns)
                    for table_name, columns in self.metadata_table_columns.items()
                },
                "semantic_grounding": (
                    self.semantic_grounding.model_dump(mode="json")
                    if self.semantic_grounding is not None
                    else None
                ),
                "execution_zone": (
                    ExecutionZoneSnapshot.model_validate(
                        {
                            "service": self.execution_zone.service,
                            "database": self.execution_zone.database,
                            "schema": self.execution_zone.schema,
                            "connector_name": self.execution_zone.connector_name,
                        }
                    )
                    if self.execution_zone is not None
                    else None
                ),
                "selected_database_connector": self.selected_database_connector,
                "generated_sql": self.generated_sql,
                "metadata_context_revision": self.metadata_context_revision,
            }
        )

    def record_execution_outcome(
        self,
        *,
        sql: str,
        connector_name: str | None,
        execution: ExecuteSqlResponse,
        preflight_status: str | None = None,
    ) -> SqlExecutionOutcome:
        """Record SQL execution as an explicit outcome event."""
        attempt = SqlExecutionAttempt(
            sql=sql,
            connector_name=connector_name,
            execution=execution,
            preflight_status=preflight_status,
        )
        self.execution = execution
        self.execution_attempts.append(attempt)
        return SqlExecutionOutcome.model_validate(
            {
                "sql": sql,
                "connector_name": connector_name,
                "execution": execution.model_dump(mode="json"),
                "preflight_status": preflight_status,
            }
        )

    def build_turn_context_snapshot(self) -> TurnContextSnapshot:
        """Build a frozen turn snapshot for finalizers and diagnostics."""
        return TurnContextSnapshot.model_validate(
            {
                "warnings": list(self.warnings),
                "metadata_sources": sorted(self.metadata_sources),
                "context_tables": list(self.context_tables[:20]),
                "metadata_table_columns": {
                    table_name: sorted(columns)
                    for table_name, columns in self.metadata_table_columns.items()
                },
                "semantic_grounding": (
                    self.semantic_grounding.model_dump(mode="json")
                    if self.semantic_grounding is not None
                    else None
                ),
                "execution_zone": (
                    ExecutionZoneSnapshot.model_validate(
                        {
                            "service": self.execution_zone.service,
                            "database": self.execution_zone.database,
                            "schema": self.execution_zone.schema,
                            "connector_name": self.execution_zone.connector_name,
                        }
                    )
                    if self.execution_zone is not None
                    else None
                ),
                "selected_database_connector": self.selected_database_connector,
                "generated_sql": self.generated_sql,
                "visualization_candidate": (
                    self.visualization_candidate.model_dump(mode="json")
                    if self.visualization_candidate is not None
                    else None
                ),
                "prompt_version": self.prompt_version,
                "metadata_context_revision": self.metadata_context_revision,
                "execution_attempts": [
                    {
                        "sql": attempt.sql,
                        "connector_name": attempt.connector_name,
                        "execution": attempt.execution.model_dump(mode="json"),
                        "preflight_status": attempt.preflight_status,
                    }
                    for attempt in self.execution_attempts
                ],
            }
        )

    def runtime_artifacts_for_prompt(self) -> list[tuple[str, dict[str, Any]]]:
        """Return compact runtime artifacts allowed to enter the parent prompt."""
        artifacts: list[tuple[str, dict[str, Any]]] = []
        if self.analysis_plan is not None:
            artifacts.append(("analysis_plan", self.analysis_plan.compact_payload()))
        for result in self.worker_results[-5:]:
            artifacts.append(("worker_result", result.compact_payload()))
        if self.checkpoint is not None:
            artifacts.append(("checkpoint_digest", self.checkpoint.digest_payload()))
        return artifacts
