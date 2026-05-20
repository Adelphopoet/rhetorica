"""SQL-family orchestration tools."""

from __future__ import annotations

from collections.abc import Callable

from rhetorica_backend.contracts.chat import OptimizationTrace
from rhetorica_backend.domain.chat.sql.optimization import SqlOptimizationService
from rhetorica_backend.domain.chat.sql.run_sql_pipeline import (
    RunSqlPipeline,
    RunSqlPipelineSupport,
)
from rhetorica_backend.domain.chat.sql.semantic_preflight import (
    SemanticSqlPreflightService,
)
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter


class ToolSqlCapabilityService:
    """Capability-family service for SQL tools."""

    def __init__(
        self,
        *,
        support: RunSqlPipelineSupport,
        sql_router: SqlRouter,
        semantic_preflight_service: SemanticSqlPreflightService,
        sql_optimization_service: SqlOptimizationService,
        logger,
    ) -> None:
        self._sql_optimizer_start_handler: Callable[[dict[str, object]], None] | None = None
        self._sql_optimizer_end_handler: Callable[[OptimizationTrace, str], None] | None = None
        self._run_sql_pipeline = RunSqlPipeline(
            sql_router=sql_router,
            semantic_preflight_service=semantic_preflight_service,
            sql_optimization_service=sql_optimization_service,
            support=support,
            logger=logger,
        )

    def set_sql_optimizer_event_handlers(
        self,
        *,
        on_start: Callable[[dict[str, object]], None] | None = None,
        on_end: Callable[[OptimizationTrace, str], None] | None = None,
    ) -> None:
        self._sql_optimizer_start_handler = on_start
        self._sql_optimizer_end_handler = on_end

    def execute_run_sql(self, **kwargs):
        """Execute run_sql tool."""
        return self._run_sql_pipeline.execute(**kwargs)

    def emit_sql_optimizer_start(
        self,
        *,
        selected_connector_name: str,
        engine: str,
        referenced_tables: list[str],
    ) -> None:
        if self._sql_optimizer_start_handler is None:
            return
        self._sql_optimizer_start_handler(
            {
                "selected_connector_name": selected_connector_name,
                "engine": engine,
                "referenced_tables": referenced_tables[:5],
            }
        )

    def emit_sql_optimizer_end(self, *, trace: OptimizationTrace) -> None:
        if self._sql_optimizer_end_handler is None:
            return
        self._sql_optimizer_end_handler(
            trace,
            self.summarize_optimization_trace(trace),
        )

    @staticmethod
    def summarize_optimization_trace(trace: OptimizationTrace) -> str:
        if trace.applied:
            if trace.rules_triggered:
                return f"Applied optimizer rewrite via {', '.join(trace.rules_triggered)}"
            return "Applied optimizer rewrite"
        if trace.findings:
            count = len(trace.findings)
            suffix = "" if count == 1 else "s"
            finding_titles = "; ".join(
                finding.title for finding in trace.findings[:2] if finding.title
            )
            if finding_titles:
                return f"Advisory only: {count} finding{suffix}. {finding_titles}"
            return f"Advisory only: {count} finding{suffix}"
        return "Inspected SQL before execution"
