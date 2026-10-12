"""SQL routing service - routes SQL to correct database connector."""

from __future__ import annotations

import logging
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import (
    EngineConnectionConfig,
    ExecuteSqlRequest,
    ExecuteSqlResponse,
)
from rhetorica_backend.domain.execution.policy import sql_guard_policy_for_connection
from rhetorica_backend.domain.execution.service import ExecutionService


class SqlRouter:
    """Routes SQL queries to appropriate database connectors."""

    def __init__(
        self,
        execution_service: ExecutionService,
        db_connector_repository: Any,
        actor_id: str | None = None,
    ):
        self._execution_service = execution_service
        self._db_connectors = db_connector_repository
        self._actor_id = actor_id
        self._logger = logging.getLogger(__name__)

    @staticmethod
    def _to_optional_str(value: Any) -> str | None:
        """Convert value to optional string."""
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def select_connector(
        self,
        request: ChatAskRequest,
        llm_payload: dict[str, Any],
        db_connectors: list[ConnectorItem],
    ) -> ConnectorItem | None:
        """
        Select database connector based on request and LLM target.

        Args:
            request: Chat request.
            llm_payload: LLM response payload with target info.
            db_connectors: Available database connectors.

        Returns:
            Selected connector or None.
        """
        # Explicit connector name from request
        if request.database_connector_name:
            return self._find_connector_by_name(db_connectors, request.database_connector_name)

        # Target from LLM
        target = llm_payload.get("target")
        if isinstance(target, dict):
            target_name = self._to_optional_str(target.get("connector_name"))
            if target_name:
                return self._find_connector_by_name(db_connectors, target_name)

        # No implicit connector selection — forcing explicit selection policy.
        # Calls should handle None by requesting clarification.
        return None

    def _find_connector_by_name(
        self,
        db_connectors: list[ConnectorItem],
        name: str,
    ) -> ConnectorItem | None:
        """Find connector by name."""
        normalized = name.strip().lower()
        for connector in db_connectors:
            if connector.name.lower() == normalized:
                return connector
        return None

    def execute_sql(
        self,
        sql: str,
        connection: Any,
        workspace_id: str,
        dialect: str,
        row_limit: int | None = None,
        conversation_id: str | None = None,
    ) -> ExecuteSqlResponse:
        """
        Execute SQL through guarded execution path.

        Args:
            sql: SQL query to execute.
            connection: Database connection config.
            workspace_id: Workspace identifier.
            dialect: SQL dialect.

        Returns:
            Execution response.

        Raises:
            Exception: If execution fails.
        """
        base_policy = sql_guard_policy_for_connection(connection)
        policy = (
            base_policy.model_copy(update={"row_limit": row_limit})
            if row_limit is not None
            else base_policy
        )
        return self._execution_service.execute(
            ExecuteSqlRequest(
                sql=sql,
                connection=connection,
                dialect=dialect,
                policy=policy,
                workspace_id=workspace_id,
                actor_id=self._actor_id,
                conversation_id=conversation_id,
            )
        )

    def get_connection_for_execution(
        self,
        workspace_id: str,
        connector_name: str,
    ) -> EngineConnectionConfig | None:
        """Resolve full connector config with decrypted password for execution."""
        get_connection = getattr(self._db_connectors, "get_connection_for_execution", None)
        if not callable(get_connection):
            return None
        return get_connection(workspace_id=workspace_id, name=connector_name)
