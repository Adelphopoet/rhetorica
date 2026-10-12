from __future__ import annotations

from unittest.mock import MagicMock

from rhetorica_backend.contracts.execution import (
    EngineConnectionConfig,
    ExecuteSqlResponse,
    QueryPreview,
)
from rhetorica_backend.mcp.server import MCPServer


class TestRunSqlToolResponseContract:
    """Tests for the canonical run_sql result contract."""

    def test_run_sql_response_uses_preview_rows_and_columns(self) -> None:
        """Verify run_sql_tool returns preview.rows and preview.columns (not data/columns)."""

        # Mock execution service that returns proper ExecuteSqlResponse
        mock_execution_service = MagicMock()
        mock_execution_service.execute.return_value = ExecuteSqlResponse(
            execution_id="exec-123",
            status="succeeded",
            engine="clickhouse",
            row_count=2,
            normalized_sql="SELECT * FROM test_table LIMIT 1000",
            preview=QueryPreview(
                columns=["col_a", "col_b"],
                rows=[[1, "value1"], [2, "value2"]],
            ),
            warnings=[],
            violations=[],
        )

        # Mock connector repository
        mock_connector_repo = MagicMock()
        mock_connector_repo.get_connection_for_execution.return_value = EngineConnectionConfig(
            engine="clickhouse",
            host="localhost",
            port=8123,
            username="default",
        )

        server = MCPServer(
            chat_factory=MagicMock(),
            execution_service=mock_execution_service,
            db_connector_repository=mock_connector_repo,
            llm_connector_repository=MagicMock(),
            chat_repository=MagicMock(),
        )

        result = server.run_sql_tool(
            query="SELECT * FROM test_table",
            workspace_id="default",
            connector_name="test-connector",
        )

        assert result["success"] is True
        assert "data" not in result
        assert "columns" not in result
        assert result["preview"] == {
            "columns": ["col_a", "col_b"],
            "rows": [[1, "value1"], [2, "value2"]],
        }
        assert result["normalized_sql"] == "SELECT * FROM test_table LIMIT 1000"
        assert result["row_count"] == 2
        assert result["execution_id"] == "exec-123"
        assert result["status"] == "succeeded"

        # Verify the execution service was called with correct arguments
        call_args = mock_execution_service.execute.call_args
        assert call_args is not None
        request = call_args[0][0]
        assert request.sql == "SELECT * FROM test_table"
        assert request.workspace_id == "default"


class TestAskChatToolExecutionResponseContract:
    """Tests for the canonical ask_chat execution result contract."""

    def test_ask_chat_response_execution_uses_preview_rows_and_columns(self) -> None:
        """Verify ask_chat_tool returns execution.preview.rows and preview.columns."""

        from rhetorica_backend.contracts.chat import ChatAskResponse

        # Mock execution result with preview data
        mock_execution = ExecuteSqlResponse(
            execution_id="exec-456",
            status="succeeded",
            engine="clickhouse",
            row_count=3,
            preview=QueryPreview(
                columns=["id", "name", "value"],
                rows=[[1, "item1", 100], [2, "item2", 200], [3, "item3", 300]],
            ),
            warnings=[],
            violations=[],
        )

        # Create mock chat response
        mock_response = ChatAskResponse(
            answer="The results show 3 items.",
            conversation_id="conv-789",
            generated_sql="SELECT * FROM items",
            selected_database_connector="test-db",
            execution=mock_execution,
            warnings=[],
        )

        # Mock orchestrator
        mock_orchestrator = MagicMock()
        mock_orchestrator.ask.return_value = mock_response

        # Mock chat factory
        mock_chat_factory = MagicMock()
        mock_chat_factory.create_orchestrator.return_value = mock_orchestrator

        server = MCPServer(
            chat_factory=mock_chat_factory,
            execution_service=MagicMock(),
            db_connector_repository=MagicMock(),
            llm_connector_repository=MagicMock(),
            chat_repository=MagicMock(),
        )

        result = server.ask_chat_tool(
            question="Show me the items",
            workspace_id="default",
        )

        # Verify execution response structure
        assert result["answer"] == "The results show 3 items."
        assert result["execution"] is not None
        assert result["execution"]["preview"] == {
            "columns": ["id", "name", "value"],
            "rows": [
                [1, "item1", 100],
                [2, "item2", 200],
                [3, "item3", 300],
            ],
        }
        assert "data" not in result["execution"]
        assert "columns" not in result["execution"]
        assert result["execution"]["row_count"] == 3
        assert result["execution"]["status"] == "succeeded"
        assert result["execution"]["execution_id"] == "exec-456"


class TestRunSqlToolConnectorResolution:
    """Tests for run_sql_tool connector resolution (Bug #1)."""

    def test_run_sql_without_connector_name_uses_first_available(self) -> None:
        """Verify run_sql_tool resolves connector when not explicitly provided."""
        from datetime import UTC, datetime

        from rhetorica_backend.contracts.connectors import ConnectorItem

        # Mock execution service
        mock_execution_service = MagicMock()
        mock_execution_service.execute.return_value = ExecuteSqlResponse(
            execution_id="exec-001",
            status="succeeded",
            engine="clickhouse",
            preview=QueryPreview(columns=[], rows=[]),
            warnings=[],
            violations=[],
        )

        # Mock connector with proper connection config
        mock_connection_config = EngineConnectionConfig(
            engine="clickhouse",
            host="db.example.com",
            port=8123,
            username="analyst",
            password="secret",
        )
        now = datetime.now(tz=UTC)
        mock_connector_item = ConnectorItem(
            id="conn-123",
            name="default-db",
            workspace_id="default",
            connection=mock_connection_config,
            has_password=True,
            created_at=now,
            updated_at=now,
        )

        # Mock connector repository
        mock_connector_repo = MagicMock()
        mock_connector_repo.list.return_value = [mock_connector_item]
        mock_connector_repo.get_connection_for_execution.return_value = mock_connection_config

        server = MCPServer(
            chat_factory=MagicMock(),
            execution_service=mock_execution_service,
            db_connector_repository=mock_connector_repo,
            llm_connector_repository=MagicMock(),
            chat_repository=MagicMock(),
        )

        result = server.run_sql_tool(
            query="SELECT 1",
            workspace_id="default",
            # No connector_name provided
        )

        assert result["success"] is True
        # Verify connector resolution was called
        mock_connector_repo.list.assert_called_once()
        mock_connector_repo.get_connection_for_execution.assert_called_once_with(
            workspace_id="default",
            name="default-db",
        )

    def test_run_sql_returns_error_when_no_connectors_configured(self) -> None:
        """Verify run_sql_tool returns clear error when no connectors exist."""

        mock_connector_repo = MagicMock()
        mock_connector_repo.list.return_value = []  # No connectors

        server = MCPServer(
            chat_factory=MagicMock(),
            execution_service=MagicMock(),
            db_connector_repository=mock_connector_repo,
            llm_connector_repository=MagicMock(),
            chat_repository=MagicMock(),
        )

        result = server.run_sql_tool(
            query="SELECT 1",
            workspace_id="default",
        )

        assert result["success"] is False
        assert result["error"]["code"] == "database_connector_not_configured"
        # Execution service should not have been called
        assert (
            not hasattr(server, "_execution_service")
            or server._execution_service.execute.call_count == 0
        )


def test_run_sql_tool_blocks_unsafe_sql_through_execution_service_guard(tmp_path) -> None:
    from rhetorica_backend.domain.execution.service import ExecutionService
    from rhetorica_backend.infra.postgres.execution_history_repository import (
        ExecutionHistoryRepository,
    )
    from rhetorica_backend.infra.postgres.session import DatabaseRuntime

    db = DatabaseRuntime(f"sqlite:///{tmp_path}/mcp_exec_history.db")
    db.create_schema()
    execution_service = ExecutionService(
        history_repository=ExecutionHistoryRepository(db),
        adapters={},
    )

    mock_connector_repo = MagicMock()
    mock_connector_repo.get_connection_for_execution.return_value = EngineConnectionConfig(
        engine="clickhouse",
        host="localhost",
        port=8123,
        username="default",
    )

    server = MCPServer(
        chat_factory=MagicMock(),
        execution_service=execution_service,
        db_connector_repository=mock_connector_repo,
        llm_connector_repository=MagicMock(),
        chat_repository=MagicMock(),
    )

    result = server.run_sql_tool(
        query="INSERT INTO forbidden_table SELECT 1",
        workspace_id="default",
        connector_name="test-connector",
    )

    assert result["success"] is True
    assert result["status"] == "blocked"
    assert result["execution_id"]
    assert result["normalized_sql"]
    assert result["violations"]
    assert result["preview"] == {"columns": [], "rows": []}


class TestSearchTablesToolContract:
    """search_tables re-exports the OpenMetadata-first cascade as one native tool."""

    def test_search_tables_tool_returns_candidates_and_tried_stages(self) -> None:
        from rhetorica_backend.domain.metadata.table_search_service import (
            TableSearchResult,
            TableSearchStage,
        )

        table_search_service = MagicMock()
        table_search_service.search.return_value = TableSearchResult(
            tables=[{"fqn": "Clickhouse-Sales.default.dm.ft_order", "found_by": "omd_scoped"}],
            tried=[TableSearchStage(name="omd_scoped", detail="serviceType in ['clickhouse']", found=1)],
            found_by="omd_scoped",
        )
        server = MCPServer(
            chat_factory=MagicMock(),
            execution_service=MagicMock(),
            db_connector_repository=MagicMock(),
            llm_connector_repository=MagicMock(),
            chat_repository=MagicMock(),
            table_search_service=table_search_service,
        )

        result = server.search_tables_tool(
            query="заказы фудтех",
            workspace_id="default",
            connector_name=None,
            service_name="Clickhouse-Sales",
            database="dm",
            size=5,
        )

        table_search_service.search.assert_called_once_with(
            workspace_id="default",
            query="заказы фудтех",
            connector_name=None,
            service_name="Clickhouse-Sales",
            database="dm",
            size=5,
        )
        assert result["success"] is True
        assert result["found_by"] == "omd_scoped"
        assert result["tables"][0]["fqn"] == "Clickhouse-Sales.default.dm.ft_order"
        assert result["tried"] == [
            {
                "stage": "omd_scoped",
                "detail": "serviceType in ['clickhouse']",
                "found": 1,
                "status": "succeeded",
            }
        ]
        assert result["error"] is None

    def test_search_tables_tool_reports_missing_service(self) -> None:
        server = MCPServer(
            chat_factory=MagicMock(),
            execution_service=MagicMock(),
            db_connector_repository=MagicMock(),
            llm_connector_repository=MagicMock(),
            chat_repository=MagicMock(),
        )

        result = server.search_tables_tool(query="orders", workspace_id="default")

        assert result["success"] is False
        assert result["error"]["code"] == "table_search_not_configured"

    def test_list_metadata_services_tool_returns_exact_services(self) -> None:
        table_search_service = MagicMock()
        table_search_service.list_metadata_services.return_value = [
            {"name": "Clickhouse-Sales", "fqn": "Clickhouse-Sales", "service_type": "Clickhouse"}
        ]
        server = MCPServer(
            chat_factory=MagicMock(),
            execution_service=MagicMock(),
            db_connector_repository=MagicMock(),
            llm_connector_repository=MagicMock(),
            chat_repository=MagicMock(),
            table_search_service=table_search_service,
        )

        result = server.list_metadata_services_tool(workspace_id="default")

        assert result["success"] is True
        assert result["services"][0]["name"] == "Clickhouse-Sales"
        table_search_service.list_metadata_services.assert_called_once_with(
            workspace_id="default"
        )

    def test_list_metadata_services_tool_returns_model_readable_error(self) -> None:
        from rhetorica_backend.domain.metadata.table_search_service import (
            MetadataServiceDiscoveryError,
        )

        table_search_service = MagicMock()
        table_search_service.list_metadata_services.side_effect = MetadataServiceDiscoveryError(
            code="mcp_tool_call_failed",
            message="OpenMetadata authorization failed.",
        )
        server = MCPServer(
            chat_factory=MagicMock(),
            execution_service=MagicMock(),
            db_connector_repository=MagicMock(),
            llm_connector_repository=MagicMock(),
            chat_repository=MagicMock(),
            table_search_service=table_search_service,
        )

        result = server.list_metadata_services_tool(workspace_id="default")

        assert result["success"] is False
        assert result["error"] == {
            "code": "mcp_tool_call_failed",
            "message": "OpenMetadata authorization failed.",
            "details": {},
        }
