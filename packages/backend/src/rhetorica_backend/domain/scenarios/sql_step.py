"""Guarded SQL step and full-result artifact publication."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from rhetorica_backend.contracts.execution import ExecuteSqlRequest, sql_dialect_for_engine
from rhetorica_backend.contracts.scenario_workflows import SqlNode
from rhetorica_backend.domain.execution.policy import sql_guard_policy_for_connection
from rhetorica_backend.domain.execution.service import ExecutionService
from rhetorica_backend.domain.files.sql_export import SqlResultExportService
from rhetorica_backend.domain.scenarios.errors import WorkflowError
from rhetorica_backend.domain.scenarios.resources import ScenarioResourcePolicy
from rhetorica_backend.domain.scenarios.step_context import ScenarioStepContext
from rhetorica_backend.infra.postgres.scenario_activity_repository import ScenarioActivityRepository
from rhetorica_backend.sql_guard.models import SqlGuardPolicy
from sqlglot import exp, parse
from sqlglot.errors import ParseError


def bind_sql(sql: str, values: dict[str, Any], dialect: str) -> str:
    try:
        expressions = parse(sql, read=dialect)
    except ParseError as exc:
        raise WorkflowError("invalid_sql", str(exc)) from exc
    if len(expressions) != 1 or expressions[0] is None:
        raise WorkflowError("single_sql_statement_required")
    expression = expressions[0]
    used: set[str] = set()

    def substitute(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Placeholder):
            return node
        name = str(node.this or "")
        if name not in values:
            raise WorkflowError("missing_sql_parameter", name)
        value = values[name]
        if value is not None and not isinstance(value, str | bool | int | float):
            raise WorkflowError("sql_parameter_must_be_scalar", name)
        used.add(name)
        return exp.convert(value)

    expression = expression.transform(substitute)
    if set(values) - used:
        raise WorkflowError("unused_sql_parameters", ", ".join(sorted(set(values) - used)))
    return expression.sql(dialect=dialect)


class ScenarioSqlStepRunner:
    def __init__(
        self,
        execution: ExecutionService,
        exports: SqlResultExportService,
        resources: ScenarioResourcePolicy,
        repository: ScenarioActivityRepository,
    ):
        self.execution = execution
        self.exports = exports
        self.resources = resources
        self.repository = repository

    async def run(
        self, node: SqlNode, inputs: dict[str, Any], context: ScenarioStepContext
    ) -> dict[str, Any]:
        task = asyncio.create_task(asyncio.to_thread(self._execute, node, inputs, context))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            with suppress(Exception):
                await asyncio.to_thread(self.execution.cancel, context.operation_id)
            with suppress(Exception):
                await task
            raise

    def _execute(
        self, node: SqlNode, inputs: dict[str, Any], context: ScenarioStepContext
    ) -> dict[str, Any]:
        connection = self.resources.database(
            node.connector_name, context.user, snapshot=context.agent_snapshot
        )
        dialect = sql_dialect_for_engine(connection.engine)
        sql = bind_sql(node.sql, inputs, dialect)
        policy = sql_guard_policy_for_connection(
            connection,
            SqlGuardPolicy(
                row_limit=node.row_limit,
                timeout_ms=node.timeout_ms,
            ),
        )
        policy.timeout_ms = min(policy.timeout_ms, node.timeout_ms)
        request = ExecuteSqlRequest(
            sql=sql,
            connection=connection,
            dialect=dialect,
            policy=policy,
            workspace_id=context.user.workspace_id,
            actor_id=context.user.id,
            conversation_id=context.conversation_id,
        )
        history = self.execution.get(context.operation_id)
        if history is None:
            self.execution.create_queued_execution(context.operation_id, request)
        self.repository.link_execution(context.run_id, context.node_id, context.operation_id)
        if history is None or history.status != "succeeded":
            result = self.execution.execute_prepared(context.operation_id, request)
            if result.status != "succeeded":
                raise WorkflowError(f"sql_{result.status}", result.error_message or result.status)
        history = self.execution.get(context.operation_id)
        if history is None:
            raise WorkflowError("sql_execution_not_found")
        snapshot = self.execution.get_snapshot(context.operation_id)
        if snapshot is None:
            raise WorkflowError(
                "sql_snapshot_unavailable",
                "Полный результат SQL не сохранён; превью не используется как вход.",
            )
        exported = self.exports.export(
            execution_id=context.operation_id,
            export_format="xlsx",
            workspace_id=context.user.workspace_id,
            conversation_id=context.conversation_id,
            owner_user_id=context.user.id,
            allowed_current_execution_ids={context.operation_id},
            artifact_id=str(uuid5(NAMESPACE_URL, f"scenario-sql/{context.operation_id}")),
        )
        file_ref = exported.file.model_dump(mode="json")
        self.repository.pin_artifact(context.run_id, file_ref["id"], context.node_id)
        self.repository.update_diagnostics(
            context.run_id,
            context.node_id,
            context.attempt,
            {
                "sql": sql,
                "normalized_sql": history.normalized_sql,
                "connector_name": node.connector_name,
                "execution_id": context.operation_id,
                "duration_ms": history.duration_ms,
                "preview": history.preview.model_dump(mode="json"),
                "warnings": history.warnings,
            },
        )
        return {
            "execution_id": context.operation_id,
            "row_count": exported.row_count,
            "file": file_ref,
            "table": {
                "execution_id": context.operation_id,
                "columns": list(snapshot.columns),
                "row_count": exported.row_count,
                "file": file_ref,
                "possibly_truncated": exported.possibly_truncated,
            },
        }
