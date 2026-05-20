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
from rhetorica_backend.domain.execution.service import ExecutionService
from rhetorica_backend.domain.execution.policy import sql_guard_policy_for_connection


class SqlRouter:
    """Routes SQL queries to appropriate database connectors."""

    def __init__(
        self,
        execution_service: ExecutionService,
        db_connector_repository: Any,
    ):
        self._execution_service = execution_service
        self._db_connectors = db_connector_repository
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
            return self._find_connector_by_name(
                db_connectors, request.database_connector_name
            )

        # Target from LLM
        target = llm_payload.get("target")
        if isinstance(target, dict):
            target_name = self._to_optional_str(target.get("connector_name"))
            target_service = self._to_optional_str(target.get("service"))
            target_database = self._to_optional_str(target.get("database"))
            target_schema = self._to_optional_str(target.get("schema"))
            has_explicit_target = any(
                value is not None
                for value in (
                    target_name,
                    target_service,
                    target_database,
                    target_schema,
                )
            )
            if target_name:
                by_name = self._find_connector_by_name(db_connectors, target_name)
                if by_name:
                    return by_name

            if any(
                value is not None
                for value in (target_service, target_database, target_schema)
            ):
                by_mapping = self._find_connector_by_mapping(
                    db_connectors=db_connectors,
                    service=target_service,
                    database=target_database,
                    schema=target_schema,
                )
                if by_mapping:
                    return by_mapping
            if has_explicit_target:
                return None

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

    def _find_connector_by_mapping(
        self,
        db_connectors: list[ConnectorItem],
        service: str | None,
        database: str | None,
        schema: str | None,
    ) -> ConnectorItem | None:
        """Find connector by metadata mapping."""
        best_match: ConnectorItem | None = None
        best_score: tuple[int, int, int] | None = None
        for connector in db_connectors:
            mapping = connector.connection.extra_settings
            score = self._mapping_match_score(
                mapping=mapping,
                service=service,
                database=database,
                schema=schema,
            )
            if score is None:
                continue
            if best_score is None or score > best_score:
                best_match = connector
                best_score = score
        return best_match

    @classmethod
    def _mapping_match_score(
        cls,
        *,
        mapping: dict[str, Any],
        service: str | None,
        database: str | None,
        schema: str | None,
    ) -> tuple[int, int, int] | None:
        service_score = cls._mapping_component_score(
            mapping_value=mapping.get("metadata_service"),
            target=service,
            allow_unset_wildcard=False,
        )
        database_score = cls._mapping_component_score(
            mapping_value=mapping.get("metadata_database"),
            target=database,
            allow_unset_wildcard=False,
        )
        schema_score = cls._mapping_component_score(
            mapping_value=mapping.get("metadata_schema"),
            target=schema,
            allow_unset_wildcard=True,
        )
        if None in (service_score, database_score, schema_score):
            return None
        return (
            int(service_score),
            int(database_score),
            int(schema_score),
        )

    @staticmethod
    def _mapping_component_score(
        mapping_value: Any,
        target: str | None,
        *,
        allow_unset_wildcard: bool,
    ) -> int | None:
        """Compare mapping value with target and score exact vs wildcard matches."""
        if target is None:
            return 1
        if isinstance(mapping_value, str) and mapping_value.strip():
            if mapping_value.strip().lower() == target.strip().lower():
                return 3
            return None
        if allow_unset_wildcard:
            return 1
        return None

    @staticmethod
    def _mapping_signature(connector: ConnectorItem) -> str:
        """Build mapping signature for a connector."""
        settings = connector.connection.extra_settings
        service = settings.get("metadata_service", "*")
        database = settings.get("metadata_database", "*")
        schema = settings.get("metadata_schema", "*")
        return f"{service}.{database}.{schema}"

    def execute_sql(
        self,
        sql: str,
        connection: Any,
        workspace_id: str,
        dialect: str,
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
        return self._execution_service.execute(
            ExecuteSqlRequest(
                sql=sql,
                connection=connection,
                dialect=dialect,
                policy=sql_guard_policy_for_connection(connection),
                workspace_id=workspace_id,
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

    @staticmethod
    def is_non_analytic_question(question: str) -> bool:
        """
        Check if question is non-analytic (greeting, small talk).

        Args:
            question: User question.

        Returns:
            True if non-analytic, False otherwise.
        """
        text = question.strip().lower().strip("!?.,:;")
        if not text:
            return True

        direct_small_talk = {
            "привет",
            "здравствуй",
            "здравствуйте",
            "добрый день",
            "доброе утро",
            "добрый вечер",
            "hi",
            "hello",
            "hey",
            "thanks",
            "thank you",
            "спасибо",
        }
        if text in direct_small_talk:
            return True

        return len(text.split()) <= 2 and any(
            text.startswith(prefix)
            for prefix in (
                "привет",
                "здрав",
                "hello",
                "hi",
                "hey",
                "спасибо",
                "thanks",
            )
        )
