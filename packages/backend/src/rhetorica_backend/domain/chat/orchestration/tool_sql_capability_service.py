"""SQL-family orchestration tools."""

from __future__ import annotations

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
        logger,
    ) -> None:
        self._run_sql_pipeline = RunSqlPipeline(
            sql_router=sql_router,
            semantic_preflight_service=semantic_preflight_service,
            support=support,
            logger=logger,
        )

    def execute_run_sql(self, **kwargs):
        """Execute run_sql tool."""
        return self._run_sql_pipeline.execute(**kwargs)
