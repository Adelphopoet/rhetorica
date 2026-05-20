"""Isolated analysis worker execution helpers."""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import ExecuteSqlResponse
from rhetorica_backend.domain.chat.orchestration.analysis_artifacts import (
    AnalysisWorkerResult,
    AnalysisWorkerTask,
)
from rhetorica_backend.domain.chat.orchestration.models import ToolExecutorPort
from rhetorica_backend.domain.chat.orchestration.prompt_artifact_serializer import (
    PromptArtifactEnvelope,
    PromptArtifactSerializer,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError


class WorkerToolScope:
    """Validates worker tool calls against the task allowlist."""

    def assert_allowed(
        self,
        *,
        task: AnalysisWorkerTask,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        if tool_name not in task.allowed_tools:
            raise ValueError(f"worker_tool_not_allowed:{tool_name}")
        if task.budget.max_tool_calls < 1:
            raise ValueError("worker_tool_budget_exhausted")
        if tool_name == "run_sql":
            self._assert_allowed_sql_tables(task=task, arguments=arguments)

    def _assert_allowed_sql_tables(
        self,
        *,
        task: AnalysisWorkerTask,
        arguments: dict[str, Any],
    ) -> None:
        sql = arguments.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("worker_sql_missing")
        referenced_tables = self._referenced_tables(sql)
        if not referenced_tables:
            return
        allowed_tables = self._allowed_table_references(task.allowed_tables)
        if not allowed_tables:
            raise ValueError("worker_allowed_tables_empty")
        denied = [
            table
            for table in referenced_tables
            if self._normalize_identifier(table) not in allowed_tables
        ]
        if denied:
            raise ValueError("worker_table_not_allowed:" + ",".join(denied))

    @classmethod
    def _referenced_tables(cls, sql: str) -> list[str]:
        try:
            parsed = parse_one(sql)
        except ParseError as exc:
            raise ValueError(f"worker_sql_parse_failed:{exc}") from exc
        cte_aliases = {
            cls._normalize_identifier(cte.alias)
            for cte in parsed.find_all(exp.CTE)
            if isinstance(cte.alias, str) and cte.alias.strip()
        }
        references: list[str] = []
        for table in parsed.find_all(exp.Table):
            parts = cls._table_identifier_parts(table)
            if not parts:
                continue
            identifier = ".".join(parts)
            if cls._normalize_identifier(identifier) in cte_aliases:
                continue
            if identifier not in references:
                references.append(identifier)
        return references

    @staticmethod
    def _table_identifier_parts(table: exp.Table) -> list[str]:
        parts = [
            part.name
            for part in table.parts
            if isinstance(part, exp.Identifier) and part.name.strip()
        ]
        if parts:
            return parts
        return [
            part
            for part in (table.catalog, table.db, table.name)
            if isinstance(part, str) and part.strip()
        ]

    @classmethod
    def _allowed_table_references(cls, allowed_tables: list[str]) -> set[str]:
        allowed: set[str] = set()
        for table in allowed_tables:
            normalized = cls._normalize_identifier(table)
            if not normalized:
                continue
            parts = normalized.split(".")
            for index in range(len(parts)):
                allowed.add(".".join(parts[index:]))
        return allowed

    @staticmethod
    def _normalize_identifier(identifier: str) -> str:
        return ".".join(
            part.strip("`\" ").casefold()
            for part in identifier.split(".")
            if part.strip("`\" ")
        )


class AnalysisWorkerRuntime:
    """Runs scoped worker tasks without merging raw transcripts into parent prompt."""

    def __init__(
        self,
        *,
        tool_executor: ToolExecutorPort,
        tool_scope: WorkerToolScope | None = None,
        serializer: PromptArtifactSerializer | None = None,
    ) -> None:
        self._tool_executor = tool_executor
        self._tool_scope = tool_scope or WorkerToolScope()
        self._serializer = serializer or PromptArtifactSerializer()

    def execute_single_tool_task(
        self,
        *,
        task: AnalysisWorkerTask,
        tool_name: str,
        arguments: dict[str, Any],
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        parent_state: ToolOrchestrationState,
    ) -> AnalysisWorkerResult:
        """Execute one scoped tool call and return a compact worker result."""
        try:
            self._tool_scope.assert_allowed(
                task=task,
                tool_name=tool_name,
                arguments=arguments,
            )
            worker_state = self._worker_state_from_parent(parent_state)
            tool_result = self._tool_executor.execute_tool(
                tool_name=tool_name,
                arguments=arguments,
                request=request,
                db_connectors=db_connectors,
                state=worker_state,
            )
        except Exception as exc:
            return AnalysisWorkerResult(
                task_id=task.task_id,
                goal=task.goal,
                status="failed",
                blocker=str(exc),
                warnings=["analysis_worker_failed"],
            )

        if not bool(tool_result.get("ok", False)):
            return AnalysisWorkerResult(
                task_id=task.task_id,
                goal=task.goal,
                status="failed",
                blocker=self._error_summary(tool_result),
                warnings=["analysis_worker_tool_failed"],
            )
        preview_rows = self._preview_row_count(tool_result)
        if preview_rows > task.budget.max_rows:
            return AnalysisWorkerResult(
                task_id=task.task_id,
                goal=task.goal,
                status="blocked",
                blocker=f"worker_row_budget_exceeded:{preview_rows}>{task.budget.max_rows}",
                warnings=["analysis_worker_row_budget_exceeded"],
                execution_outcome=self._latest_execution_outcome(worker_state),
            )
        evidence_ids = self._evidence_ids(tool_result)
        return AnalysisWorkerResult(
            task_id=task.task_id,
            goal=task.goal,
            status="succeeded",
            key_finding=self._key_finding(tool_result),
            evidence_ids=evidence_ids,
            execution_outcome=self._latest_execution_outcome(worker_state),
            next_action=None,
        )

    def execute_independent_tool_tasks(
        self,
        *,
        tasks: Iterable[tuple[AnalysisWorkerTask, str, dict[str, Any]]],
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        parent_state: ToolOrchestrationState,
    ) -> list[AnalysisWorkerResult]:
        """Execute independent read-only worker tasks in isolated contexts."""
        task_items = list(tasks)
        if not task_items:
            return []
        with ThreadPoolExecutor(max_workers=len(task_items)) as executor:
            futures = [
                executor.submit(
                    self.execute_single_tool_task,
                    task=task,
                    tool_name=tool_name,
                    arguments=arguments,
                    request=request,
                    db_connectors=db_connectors,
                    parent_state=parent_state,
                )
                for task, tool_name, arguments in task_items
            ]
            return [future.result() for future in futures]

    def prompt_artifact(self, *, result: AnalysisWorkerResult) -> PromptArtifactEnvelope:
        """Serialize compact worker result for parent prompt merge."""
        return self._serializer.serialize(
            artifact_type="worker_result",
            artifact=result.compact_payload(),
        )

    @staticmethod
    def merge_result(
        *,
        parent_state: ToolOrchestrationState,
        result: AnalysisWorkerResult,
    ) -> None:
        parent_state.worker_results.append(result)
        outcome = result.execution_outcome
        if not isinstance(outcome, dict):
            return
        execution_payload = outcome.get("execution")
        if not isinstance(execution_payload, dict):
            return
        try:
            execution = ExecuteSqlResponse.model_validate(execution_payload)
        except Exception:
            parent_state.add_warning("analysis_worker_execution_merge_failed")
            return
        if execution.execution_id and any(
            attempt.execution.execution_id == execution.execution_id
            for attempt in parent_state.execution_attempts
        ):
            return
        parent_state.record_execution_outcome(
            sql=str(outcome.get("sql") or ""),
            connector_name=(
                outcome.get("connector_name")
                if isinstance(outcome.get("connector_name"), str)
                else None
            ),
            execution=execution,
            preflight_status=(
                outcome.get("preflight_status")
                if isinstance(outcome.get("preflight_status"), str)
                else None
            ),
        )

    @staticmethod
    def _worker_state_from_parent(parent_state: ToolOrchestrationState) -> ToolOrchestrationState:
        worker_state = ToolOrchestrationState(
            conversation_id=parent_state.conversation_id,
            prompt_version=parent_state.prompt_version,
            selected_database_connector=parent_state.selected_database_connector,
            execution_zone=parent_state.execution_zone,
            semantic_grounding=parent_state.semantic_grounding,
        )
        worker_state.context_tables = list(parent_state.context_tables)
        worker_state.metadata_sources = set(parent_state.metadata_sources)
        worker_state.metadata_table_columns = {
            table_name: set(columns)
            for table_name, columns in parent_state.metadata_table_columns.items()
        }
        worker_state.metadata_table_schemas = dict(parent_state.metadata_table_schemas)
        return worker_state

    @staticmethod
    def _evidence_ids(tool_result: dict[str, Any]) -> list[str]:
        execution = tool_result.get("execution")
        execution_id = None
        if isinstance(execution, dict):
            execution_id = execution.get("execution_id")
        if not isinstance(execution_id, str):
            execution_id = tool_result.get("execution_id")
        return [execution_id] if isinstance(execution_id, str) and execution_id else []

    @staticmethod
    def _latest_execution_outcome(
        worker_state: ToolOrchestrationState,
    ) -> dict[str, Any] | None:
        if not worker_state.execution_attempts:
            return None
        attempt = worker_state.execution_attempts[-1]
        return {
            "sql": attempt.sql,
            "connector_name": attempt.connector_name,
            "execution": attempt.execution.model_dump(mode="json"),
            "preflight_status": attempt.preflight_status,
        }

    @staticmethod
    def _key_finding(tool_result: dict[str, Any]) -> str | None:
        execution = tool_result.get("execution")
        if isinstance(execution, dict):
            status = execution.get("status")
            row_count = execution.get("row_count")
            preview = execution.get("preview")
            if isinstance(preview, dict):
                columns = preview.get("columns")
                rows = preview.get("rows")
                if isinstance(columns, list) and isinstance(rows, list) and rows:
                    return (
                        f"Execution {status}; row_count={row_count}; "
                        f"columns={columns[:8]}; rows={rows[:5]}."
                    )
            if isinstance(status, str):
                return f"Execution {status}; row_count={row_count}."
        message = tool_result.get("message") or tool_result.get("user_message")
        return message if isinstance(message, str) else None

    @staticmethod
    def _preview_row_count(tool_result: dict[str, Any]) -> int:
        execution = tool_result.get("execution")
        if not isinstance(execution, dict):
            return 0
        preview = execution.get("preview")
        if not isinstance(preview, dict):
            return 0
        rows = preview.get("rows")
        return len(rows) if isinstance(rows, list) else 0

    @staticmethod
    def _error_summary(tool_result: dict[str, Any]) -> str:
        for key in ("error_code", "error", "message", "user_message"):
            value = tool_result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "worker_tool_failed"
