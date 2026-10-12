"""Tool orchestration state."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from rhetorica_backend.contracts.chat import OptimizationTrace, SemanticGrounding
from rhetorica_backend.contracts.execution import ExecuteSqlResponse
from rhetorica_backend.contracts.files import FileArtifactRef
from rhetorica_backend.contracts.visualization import VisualizationSpec
from rhetorica_backend.domain.chat.orchestration.capability_registry import (
    RuntimeCapabilityState,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    ExecutedToolCallLedgerEntry,
    ExecutionZoneSnapshot,
    SqlExecutionOutcome,
    SqlPlanningContext,
    ToolCallOutcomeReceipt,
    ToolCallResult,
)
from rhetorica_backend.domain.chat.orchestration.tool_result_artifacts import (
    ToolResultArtifactStore,
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
    visualization_candidates: list[VisualizationSpec] = field(default_factory=list)
    optimization_trace: OptimizationTrace = field(default_factory=OptimizationTrace)
    execution_attempts: list[SqlExecutionAttempt] = field(default_factory=list)
    tool_call_results: dict[tuple[str, str], dict[str, object]] = field(default_factory=dict)
    executed_tool_call_ledger: list[ExecutedToolCallLedgerEntry] = field(default_factory=list)
    last_compacted_tool_call_sequence: int = 0
    blocked_tool_call_turn_count: int = 0
    force_context_compaction: bool = False
    metadata_context_revision: int = 0
    conversation_id: str | None = None
    prompt_version: str | None = None
    tool_call_count: int = 0
    preflight_pass: int = 0
    preflight_fail: int = 0
    preflight_uncertain: int = 0
    unavailable_context_connector_fail: int = 0
    capability_state: RuntimeCapabilityState = field(default_factory=RuntimeCapabilityState)
    llm_usages: list[LlmCallUsage] = field(default_factory=list)
    workspace_skills_read: list[dict[str, Any]] = field(default_factory=list)
    discovered_metrics: list[dict[str, Any]] = field(default_factory=list)
    sandbox_session_id: str | None = None
    sandbox_input_artifact_ids: list[str] = field(default_factory=list)
    sandbox_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    published_output_ids: set[str] = field(default_factory=set)
    published_sandbox_outputs: dict[str, FileArtifactRef] = field(default_factory=dict)
    published_output_bytes: int = 0
    published_files: list[FileArtifactRef] = field(default_factory=list)
    deferred_tool_definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    available_tool_groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    loaded_tool_definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    loaded_mcp_tool_definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    loaded_tool_group_names: list[str] = field(default_factory=list)
    loaded_mcp_remote_tool_names: dict[str, list[str]] = field(default_factory=dict)
    loaded_mcp_exposed_tool_names: dict[str, dict[str, str]] = field(default_factory=dict)
    tool_result_artifacts: ToolResultArtifactStore = field(default_factory=ToolResultArtifactStore)

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
        args_json = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        self.tool_call_results[(tool_name, args_json)] = result
        return ToolCallResult.model_validate({
            "ok": bool(result.get("ok", False)),
            "error_code": result.get("error_code"),
            "can_continue": bool(result.get("can_continue", True)),
            "payload": result,
        })

    def record_executed_tool_call(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        prompt_result: dict[str, Any],
    ) -> ExecutedToolCallLedgerEntry:
        """Record exact call identity plus a fixed-schema outcome receipt."""
        execution = result.get("execution")
        execution_payload = execution if isinstance(execution, dict) else {}
        tool_artifact = prompt_result.get("tool_artifact")
        artifact_payload = tool_artifact if isinstance(tool_artifact, dict) else {}
        requested_artifact_id = arguments.get("artifact_id")
        entry = ExecutedToolCallLedgerEntry(
            sequence=len(self.executed_tool_call_ledger) + 1,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            outcome=ToolCallOutcomeReceipt(
                ok=bool(result.get("ok", False)),
                error_code=self._optional_ledger_str(result.get("error_code")),
                error_category=self._optional_ledger_str(result.get("error_category")),
                status=self._optional_ledger_str(
                    result.get("status") or execution_payload.get("status")
                ),
                execution_status=self._optional_ledger_str(
                    result.get("execution_status") or execution_payload.get("status")
                ),
                execution_id=self._optional_ledger_str(
                    result.get("execution_id") or execution_payload.get("execution_id")
                ),
                result_state=self._optional_ledger_str(result.get("result_state")),
                row_count=self._optional_ledger_int(
                    result.get("row_count", execution_payload.get("row_count"))
                ),
                artifact_id=self._optional_ledger_str(
                    artifact_payload.get("artifact_id") or requested_artifact_id
                ),
                artifact_sha256=self._optional_ledger_str(artifact_payload.get("sha256")),
            ),
        )
        self.executed_tool_call_ledger.append(entry)
        return entry

    @staticmethod
    def _optional_ledger_str(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _optional_ledger_int(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def build_sql_planning_context(self) -> SqlPlanningContext:
        """Build an explicit SQL planning context instead of leaking whole state."""
        return SqlPlanningContext.model_validate({
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
                ExecutionZoneSnapshot.model_validate({
                    "service": self.execution_zone.service,
                    "database": self.execution_zone.database,
                    "schema": self.execution_zone.schema,
                    "connector_name": self.execution_zone.connector_name,
                })
                if self.execution_zone is not None
                else None
            ),
            "selected_database_connector": self.selected_database_connector,
            "generated_sql": self.generated_sql,
            "metadata_context_revision": self.metadata_context_revision,
        })

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
        return SqlExecutionOutcome.model_validate({
            "sql": sql,
            "connector_name": connector_name,
            "execution": execution.model_dump(mode="json"),
            "preflight_status": preflight_status,
        })
