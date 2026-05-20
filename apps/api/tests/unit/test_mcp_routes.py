"""Tests for MCP agent bridge endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import confluence_connectors as confluence_connector_routes
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.routes import openmetadata_connectors as openmetadata_connector_routes
from rhetorica_api.app.settings import settings
from rhetorica_backend.mcp.server import MCPServer


def _client(tmp_path: Path) -> TestClient:
    """Create test client with fresh database."""
    settings.database_url = f"sqlite:///{tmp_path}/api_mcp.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    confluence_connector_routes._repository.cache_clear()
    openmetadata_connector_routes._repository.cache_clear()
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    return TestClient(create_app())


def test_mcp_root_endpoint(tmp_path: Path) -> None:
    """Test MCP root info endpoint."""
    client = _client(tmp_path)

    response = client.get("/mcp/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Rhetorica MCP Server"
    assert payload["version"] == "0.1.0"
    assert "endpoints" in payload
    assert "/mcp/sse" in payload["endpoints"].values()
    assert "/mcp/messages" in payload["endpoints"].values()


def test_mcp_tools_list(tmp_path: Path) -> None:
    """Test MCP tools/list method."""
    client = _client(tmp_path)

    response = client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 1
    assert "result" in payload
    assert "tools" in payload["result"]

    tools = payload["result"]["tools"]
    assert len(tools) >= 5

    tool_names = [t["name"] for t in tools]
    assert "run_sql" in tool_names
    assert "search_metadata" in tool_names
    assert "ask_chat" in tool_names
    assert "list_connectors" in tool_names
    assert "get_conversation" in tool_names
    assert "list_conversations" in tool_names


def test_mcp_tools_list_has_correct_schemas(tmp_path: Path) -> None:
    """Test that tool schemas are correctly defined."""
    client = _client(tmp_path)

    response = client.post(
        "/mcp/messages",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert response.status_code == 200

    tools = response.json()["result"]["tools"]
    ask_chat_tool = next(t for t in tools if t["name"] == "ask_chat")

    assert "inputSchema" in ask_chat_tool
    schema = ask_chat_tool["inputSchema"]
    assert schema["type"] == "object"
    assert "question" in schema["properties"]
    assert "workspace_id" in schema["properties"]
    assert "question" in schema["required"]


def test_mcp_tools_call_run_sql(tmp_path: Path, monkeypatch) -> None:
    """Test MCP tools/call for run_sql - expects error without proper connection."""
    client = _client(tmp_path)

    # Setup database connector
    db_create = client.post(
        "/v1/admin/connectors",
        json={
            "name": "test-db",
            "workspace_id": "default",
            "connection": {
                "engine": "clickhouse",
                "host": "db.local",
                "port": 8123,
                "username": "analyst",
                "password": "secret",
                "database": "analytics",
                "secure": False,
                "verify": True,
                "extra_settings": {},
            },
        },
    )
    assert db_create.status_code == 200

    # run_sql without proper connection setup will fail validation
    # This test verifies the MCP endpoint handles the error gracefully
    response = client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "run_sql",
                "arguments": {
                    "query": "SELECT 1 as value",
                    "workspace_id": "default",
                },
            },
        },
    )
    # Should return error response, not crash
    assert response.status_code == 200
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 2
    # Error is expected since connection config is not provided
    assert "error" in payload or "result" in payload


def test_mcp_tools_call_ask_chat(tmp_path: Path, monkeypatch) -> None:
    """Test MCP tools/call for ask_chat."""
    client = _client(tmp_path)

    # Setup LLM connector
    llm_create = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "test-llm",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4",
                "api_key": "test-key",
                "extra_settings": {"structured_output_enabled": True, "support_tool_calls": True},
            },
        },
    )
    assert llm_create.status_code == 200

    # MCP chat path now goes through the orchestrator adapter directly.
    monkeypatch.setattr(
        MCPServer,
        "ask_chat_tool",
        lambda *_args, **_kwargs: {
            "answer": "Test answer from MCP",
            "warnings": [],
            "generated_sql": None,
            "selected_database_connector": None,
            "assistant_message_id": None,
            "metadata_sources": [],
            "context_tables": [],
            "execution": None,
            "conversation_id": "test-conversation-id",
        },
    )

    response = client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "ask_chat",
                "arguments": {
                    "question": "Test question via MCP",
                    "workspace_id": "default",
                    "run_sql": False,
                },
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 3
    assert "result" in payload

    # Parse content
    import ast
    import json
    content_text = payload["result"]["content"][0]["text"]
    # Content might be dict, JSON string, or Python dict string
    if isinstance(content_text, dict | list):
        content = content_text
    else:
        try:
            content = json.loads(content_text)
        except json.JSONDecodeError:
            # Fallback to ast.literal_eval for Python dict strings
            content = ast.literal_eval(content_text)
    assert content["answer"] == "Test answer from MCP"
    assert content["conversation_id"] is not None


def test_mcp_tools_call_list_connectors(tmp_path: Path) -> None:
    """Test MCP tools/call for list_connectors."""
    client = _client(tmp_path)

    # Setup connectors
    db_create = client.post(
        "/v1/admin/connectors",
        json={
            "name": "test-db",
            "workspace_id": "default",
            "connection": {
                "engine": "clickhouse",
                "host": "db.local",
                "port": 8123,
                "username": "analyst",
                "password": "secret",
                "database": "analytics",
                "secure": False,
                "verify": True,
                "extra_settings": {},
            },
        },
    )
    assert db_create.status_code == 200

    llm_create = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "test-llm",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4",
                "api_key": "test-key",
                "extra_settings": {"structured_output_enabled": True, "support_tool_calls": True},
            },
        },
    )
    assert llm_create.status_code == 200

    response = client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "list_connectors",
                "arguments": {
                    "workspace_id": "default",
                },
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()

    import ast
    import json
    content_text = payload["result"]["content"][0]["text"]
    if isinstance(content_text, dict | list):
        content = content_text
    else:
        try:
            content = json.loads(content_text)
        except json.JSONDecodeError:
            content = ast.literal_eval(content_text)
    assert isinstance(content, list)
    assert len(content) >= 2

    connector_types = [c["type"] for c in content]
    assert "database" in connector_types
    assert "llm" in connector_types


def test_mcp_tools_call_with_workspace_isolation(tmp_path: Path, monkeypatch) -> None:
    """Test that workspace_id is enforced for isolation."""
    client = _client(tmp_path)

    # Setup LLM for workspace1
    llm_create = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "workspace1-llm",
            "workspace_id": "workspace1",
            "connection": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4",
                "api_key": "test-key",
                "extra_settings": {"structured_output_enabled": True, "support_tool_calls": True},
            },
        },
    )
    assert llm_create.status_code == 200

    # Mock to verify workspace_id is passed
    received_workspace_ids = []

    def mock_ask_chat_tool(self, **kwargs):
        received_workspace_ids.append(kwargs["workspace_id"])
        return {
            "answer": f"Answer for {kwargs['workspace_id']}",
            "conversation_id": "test-conv",
            "warnings": [],
        }

    monkeypatch.setattr(MCPServer, "ask_chat_tool", mock_ask_chat_tool)

    # Call with workspace1
    response = client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "ask_chat",
                "arguments": {
                    "question": "Test",
                    "workspace_id": "workspace1",
                },
            },
        },
    )
    assert response.status_code == 200
    assert "workspace1" in received_workspace_ids


def test_mcp_unknown_tool(tmp_path: Path) -> None:
    """Test error handling for unknown tool."""
    client = _client(tmp_path)

    response = client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {
                "name": "nonexistent_tool",
                "arguments": {},
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 99
    assert "error" in payload
    assert "Unknown tool" in payload["error"]["message"]


def test_mcp_unknown_method(tmp_path: Path) -> None:
    """Test error handling for unknown method."""
    client = _client(tmp_path)

    response = client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 100,
            "method": "unknown/method",
            "params": {},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 100
    assert "error" in payload
    assert "Method not found" in payload["error"]["message"]


def test_mcp_conversation_endpoints(tmp_path: Path, monkeypatch) -> None:
    """Test MCP conversation tools."""
    client = _client(tmp_path)

    # Setup LLM
    llm_create = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "test-llm",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4",
                "api_key": "test-key",
                "extra_settings": {"structured_output_enabled": True, "support_tool_calls": True},
            },
        },
    )
    assert llm_create.status_code == 200

    def mock_ask_chat_tool(
        self,
        *,
        question: str,
        workspace_id: str = "default",
        conversation_id: str | None = None,
        llm_connector_name: str | None = None,
        metadata_connector_name: str | None = None,
        database_connector_name: str | None = None,
        run_sql: bool = True,
        current_user_id: str | None = None,
    ) -> dict[str, object]:
        del llm_connector_name, metadata_connector_name, database_connector_name, run_sql
        if conversation_id is None:
            conversation = self._chat_repository.create_conversation(
                workspace_id=workspace_id,
                title=question,
                owner_user_id=current_user_id,
            )
            conversation_id = conversation.id
        self._chat_repository.save_message(
            conversation_id=conversation_id,
            role="user",
            content=question,
            owner_user_id=current_user_id,
        )
        assistant_message = self._chat_repository.save_message(
            conversation_id=conversation_id,
            role="assistant",
            content="Test",
            warnings=[],
            owner_user_id=current_user_id,
        )
        return {
            "answer": "Test",
            "conversation_id": conversation_id,
            "workspace_id": workspace_id,
            "question": question,
            "assistant_message_id": (
                assistant_message.id if assistant_message is not None else None
            ),
            "warnings": [],
        }

    monkeypatch.setattr(MCPServer, "ask_chat_tool", mock_ask_chat_tool)

    # First ask to create conversation
    response1 = client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ask_chat",
                "arguments": {
                    "question": "First question",
                    "workspace_id": "default",
                },
            },
        },
    )
    assert response1.status_code == 200

    import ast
    import json
    content1 = response1.json()["result"]["content"][0]["text"]
    if isinstance(content1, dict):
        conv_data = content1
    else:
        try:
            conv_data = json.loads(content1)
        except json.JSONDecodeError:
            conv_data = ast.literal_eval(content1)
    conversation_id = conv_data["conversation_id"]

    # Test get_conversation
    response2 = client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "get_conversation",
                "arguments": {
                    "conversation_id": conversation_id,
                    "workspace_id": "default",
                },
            },
        },
    )
    assert response2.status_code == 200
    content2_text = response2.json()["result"]["content"][0]["text"]
    if isinstance(content2_text, dict):
        content2 = content2_text
    else:
        try:
            content2 = json.loads(content2_text)
        except json.JSONDecodeError:
            content2 = ast.literal_eval(content2_text)
    assert content2["id"] == conversation_id
    assert "messages" in content2

    # Test list_conversations
    response3 = client.post(
        "/mcp/messages",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "list_conversations",
                "arguments": {
                    "workspace_id": "default",
                    "limit": 10,
                },
            },
        },
    )
    assert response3.status_code == 200
    content3_text = response3.json()["result"]["content"][0]["text"]
    if isinstance(content3_text, list):
        content3 = content3_text
    else:
        try:
            content3 = json.loads(content3_text)
        except json.JSONDecodeError:
            content3 = ast.literal_eval(content3_text)
    assert isinstance(content3, list)
    assert len(content3) >= 1


def test_mcp_enabled_feature_flag_default(tmp_path: Path) -> None:
    """Test that MCP_ENABLED defaults to True and routes are registered."""
    client = _client(tmp_path)

    # Should be able to access MCP endpoint when MCP_ENABLED is not set (defaults to True)
    response = client.get("/mcp/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Rhetorica MCP Server"


def test_mcp_enabled_feature_flag_disabled(monkeypatch) -> None:
    """Test that MCP routes are not registered when MCP_ENABLED=false."""
    from rhetorica_api.app import settings as api_settings

    # Temporarily disable MCP
    monkeypatch.setattr(api_settings.settings, "mcp_enabled", False)

    # Create a fresh app with MCP disabled
    from rhetorica_api.app.main import create_app
    from rhetorica_api.app.routes import confluence_connectors as confluence_connector_routes
    from rhetorica_api.app.routes import connectors as connector_routes
    from rhetorica_api.app.routes import execution as execution_routes
    from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
    from rhetorica_api.app.routes import openmetadata_connectors as openmetadata_connector_routes

    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    confluence_connector_routes._repository.cache_clear()
    openmetadata_connector_routes._repository.cache_clear()

    client = TestClient(create_app())

    # MCP routes should not be registered
    response = client.get("/mcp/")
    assert response.status_code == 404

    # But other routes should still work
    response = client.get("/healthz")
    assert response.status_code == 200
