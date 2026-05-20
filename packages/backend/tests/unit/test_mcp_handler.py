from __future__ import annotations

from unittest.mock import MagicMock

from rhetorica_backend.contracts.execution import ExecuteSqlResponse, QueryPreview
from rhetorica_backend.mcp.handler import McpRequestHandler


class _McpServerStub:
    def ask_chat_tool(self, **kwargs):
        return {"answer": "ok", "kwargs": kwargs}


def test_mcp_handler_returns_tools_list() -> None:
    handler = McpRequestHandler(server=_McpServerStub())

    result = handler.handle_message(
        body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        workspace_id="default",
    )

    assert result["jsonrpc"] == "2.0"
    assert result["id"] == 1
    assert any(tool["name"] == "ask_chat" for tool in result["result"]["tools"])


def test_mcp_handler_dispatches_tool_call_with_workspace_and_user() -> None:
    handler = McpRequestHandler(server=_McpServerStub())

    result = handler.handle_message(
        body={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "ask_chat",
                "arguments": {
                    "question": "why",
                    "run_sql": False,
                },
            },
        },
        workspace_id="workspace-1",
        current_user_id="user-1",
    )

    content = result["result"]["content"][0]["text"]
    assert "workspace-1" in content
    assert "user-1" in content


class TestRunSqlToolResponseContract:
    """Tests for run_sql_tool response field access (Bug #2)."""

    def test_run_sql_response_uses_preview_rows_and_columns(self) -> None:
        """Verify run_sql_tool returns preview.rows and preview.columns (not data/columns)."""
        from rhetorica_backend.contracts.execution import EngineConnectionConfig
        from rhetorica_backend.mcp.server import MCPServer

        # Mock execution service that returns proper ExecuteSqlResponse
        mock_execution_service = MagicMock()
        mock_execution_service.execute.return_value = ExecuteSqlResponse(
            execution_id="exec-123",
            status="succeeded",
            engine="clickhouse",
            row_count=2,
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
            metadata_catalog_service=MagicMock(),
            execution_service=mock_execution_service,
            db_connector_repository=mock_connector_repo,
            llm_connector_repository=MagicMock(),
            openmetadata_connector_repository=MagicMock(),
            chat_repository=MagicMock(),
        )

        result = server.run_sql_tool(
            query="SELECT * FROM test_table",
            workspace_id="default",
            connector_name="test-connector",
        )

        # Verify the response structure
        assert result["success"] is True
        assert "data" in result
        assert "columns" in result
        assert result["data"] == [[1, "value1"], [2, "value2"]]
        assert result["columns"] == ["col_a", "col_b"]
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
    """Tests for ask_chat_tool execution field access (Bug #3)."""

    def test_ask_chat_response_execution_uses_preview_rows_and_columns(self) -> None:
        """Verify ask_chat_tool returns execution.preview.rows and preview.columns."""

        from rhetorica_backend.contracts.chat import ChatAskResponse
        from rhetorica_backend.mcp.server import MCPServer

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
            metadata_catalog_service=MagicMock(),
            execution_service=MagicMock(),
            db_connector_repository=MagicMock(),
            llm_connector_repository=MagicMock(),
            openmetadata_connector_repository=MagicMock(),
            chat_repository=MagicMock(),
        )

        result = server.ask_chat_tool(
            question="Show me the items",
            workspace_id="default",
        )

        # Verify execution response structure
        assert result["answer"] == "The results show 3 items."
        assert result["execution"] is not None
        assert result["execution"]["data"] == [[1, "item1", 100], [2, "item2", 200], [3, "item3", 300]]
        assert result["execution"]["columns"] == ["id", "name", "value"]
        assert result["execution"]["row_count"] == 3
        assert result["execution"]["status"] == "succeeded"
        assert result["execution"]["execution_id"] == "exec-456"


class TestRunSqlToolConnectorResolution:
    """Tests for run_sql_tool connector resolution (Bug #1)."""

    def test_run_sql_without_connector_name_uses_first_available(self) -> None:
        """Verify run_sql_tool resolves connector when not explicitly provided."""
        from datetime import UTC, datetime

        from rhetorica_backend.contracts.connectors import ConnectorItem
        from rhetorica_backend.contracts.execution import EngineConnectionConfig
        from rhetorica_backend.mcp.server import MCPServer

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
            metadata_catalog_service=MagicMock(),
            execution_service=mock_execution_service,
            db_connector_repository=mock_connector_repo,
            llm_connector_repository=MagicMock(),
            openmetadata_connector_repository=MagicMock(),
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
        from rhetorica_backend.mcp.server import MCPServer

        mock_connector_repo = MagicMock()
        mock_connector_repo.list.return_value = []  # No connectors

        server = MCPServer(
            chat_factory=MagicMock(),
            metadata_catalog_service=MagicMock(),
            execution_service=MagicMock(),
            db_connector_repository=mock_connector_repo,
            llm_connector_repository=MagicMock(),
            openmetadata_connector_repository=MagicMock(),
            chat_repository=MagicMock(),
        )

        result = server.run_sql_tool(
            query="SELECT 1",
            workspace_id="default",
        )

        assert result["success"] is False
        assert "No database connector configured" in result["error"]
        # Execution service should not have been called
        assert not hasattr(server, "_execution_service") or server._execution_service.execute.call_count == 0
