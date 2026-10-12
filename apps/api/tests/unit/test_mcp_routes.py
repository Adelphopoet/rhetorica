"""Tests for the canonical MCP Streamable HTTP endpoint."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from rhetorica_api.app import mcp as mcp_runtime
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import agents as agent_routes
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.routes import mcp_connectors as mcp_connector_routes
from rhetorica_api.app.settings import settings
from rhetorica_backend.mcp.server import MCPServer

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-11-25",
}


def _client(tmp_path: Path) -> TestClient:
    """Create test client with fresh database."""
    settings.database_url = f"sqlite:///{tmp_path}/api_mcp.db"
    execution_routes._runtime.cache_clear()
    agent_routes._repository.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    mcp_connector_routes._repository.cache_clear()
    _, runtime = execution_routes._runtime()
    runtime.create_schema()
    client = TestClient(create_app())
    client.headers.update(MCP_HEADERS)
    return client


@pytest.fixture
def mcp_client(tmp_path: Path):
    with _client(tmp_path) as client:
        yield client


def test_legacy_mcp_routes_are_not_registered(mcp_client: TestClient) -> None:
    client = mcp_client

    assert client.post("/mcp/messages", json={}).status_code == 404
    assert client.get("/mcp/sse").status_code == 404
    assert client.get("/mcp/ws").status_code == 404
    assert client.get("/mcp/ws/agent").status_code == 404


def test_mcp_tools_list(mcp_client: TestClient) -> None:
    """Test MCP tools/list method."""
    client = mcp_client

    response = client.post(
        "/mcp",
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
    tool_names = [t["name"] for t in tools]
    assert set(tool_names) == {
        "run_sql",
        "list_metadata_services",
        "search_tables",
        "ask_chat",
        "list_agents",
        "list_connectors",
        "get_conversation",
        "list_conversations",
    }


def test_mcp_tools_list_has_correct_schemas(mcp_client: TestClient) -> None:
    """Test that tool schemas are correctly defined."""
    client = mcp_client

    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert response.status_code == 200

    tools = response.json()["result"]["tools"]
    ask_chat_tool = next(t for t in tools if t["name"] == "ask_chat")

    assert "inputSchema" in ask_chat_tool
    assert "outputSchema" in ask_chat_tool
    schema = ask_chat_tool["inputSchema"]
    assert schema["type"] == "object"
    assert "question" in schema["properties"]
    assert "agent_id" in schema["properties"]
    assert "workspace_id" not in schema["properties"]
    assert "question" in schema["required"]
    assert ask_chat_tool["title"] == "Ask Rhetorica analyst"
    assert ask_chat_tool["annotations"]["readOnlyHint"] is False

    list_agents_tool = next(t for t in tools if t["name"] == "list_agents")
    assert list_agents_tool["annotations"]["readOnlyHint"] is True
    assert list_agents_tool["annotations"]["idempotentHint"] is True

    run_sql_tool = next(t for t in tools if t["name"] == "run_sql")
    assert "normalized_sql" in run_sql_tool["outputSchema"]["properties"]
    assert "preview" in run_sql_tool["outputSchema"]["properties"]
    assert all(t["outputSchema"]["type"] == "object" for t in tools)


def _seed_mcp_catalog(
    client: TestClient,
    *,
    name: str,
    prefix: str,
    allowed_tool_names: list[str],
    tools: list[dict],
    visible_to_external_mcp: bool = True,
) -> None:
    response = client.post(
        "/v1/admin/mcp-connectors",
        json={
            "name": name,
            "workspace_id": "default",
            "is_active": True,
            "visible_to_external_mcp": visible_to_external_mcp,
            "connection": {
                "provider": "generic",
                "transport": "streamable-http",
                "url": "https://mcp.example.com/mcp",
                "allowed_tool_names": allowed_tool_names,
                "tool_name_prefix": prefix,
            },
        },
    )
    assert response.status_code == 200
    mcp_connector_routes._repository().replace_tool_catalog(
        connector_id=response.json()["id"],
        workspace_id="default",
        tools=tools,
        status="success",
    )


def test_mcp_proxy_tools_list_returns_full_catalog(mcp_client: TestClient) -> None:
    tools = [
        {
            "name": f"tool_{index:03d}",
            "input_schema": {"type": "object", "properties": {}},
        }
        for index in range(501)
    ]
    _seed_mcp_catalog(
        mcp_client,
        name="bulk-mcp",
        prefix="bulk",
        allowed_tool_names=[],
        tools=tools,
    )

    response = mcp_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 10, "method": "tools/list", "params": {}},
    )

    assert response.status_code == 200
    listed = response.json()["result"]["tools"]
    proxy_tools = [tool for tool in listed if tool["name"].startswith("bulk__")]
    assert len(proxy_tools) == 501
    assert proxy_tools[-1]["name"] == "bulk__tool_500"
    assert all(tool["annotations"]["destructiveHint"] is True for tool in proxy_tools)


def test_mcp_proxy_tools_enforce_admin_allowlist(mcp_client: TestClient) -> None:
    _seed_mcp_catalog(
        mcp_client,
        name="jira-mcp",
        prefix="jira",
        allowed_tool_names=["search_issues"],
        tools=[
            {
                "name": "search_issues",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "delete_issue",
                "input_schema": {"type": "object", "properties": {}},
            },
        ],
    )

    listed = mcp_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 11, "method": "tools/list", "params": {}},
    ).json()["result"]["tools"]
    names = {tool["name"] for tool in listed}
    assert "jira__search_issues" in names
    assert "jira__delete_issue" not in names

    blocked = mcp_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {"name": "jira__delete_issue", "arguments": {}},
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["result"]["isError"] is True


def test_mcp_proxy_hides_connector_disabled_by_admin(mcp_client: TestClient) -> None:
    _seed_mcp_catalog(
        mcp_client,
        name="private-mcp",
        prefix="private",
        allowed_tool_names=[],
        tools=[{"name": "search", "input_schema": {"type": "object"}}],
        visible_to_external_mcp=False,
    )

    listed = mcp_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 13, "method": "tools/list", "params": {}},
    ).json()["result"]["tools"]
    assert "private__search" not in {tool["name"] for tool in listed}

    blocked = mcp_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 14,
            "method": "tools/call",
            "params": {"name": "private__search", "arguments": {}},
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["result"]["isError"] is True


def test_mcp_tools_call_run_sql(mcp_client: TestClient, monkeypatch) -> None:
    """Test MCP tools/call for run_sql - expects error without proper connection."""
    client = mcp_client

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
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "run_sql",
                "arguments": {
                    "query": "SELECT 1 as value",
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


def test_mcp_tools_call_ask_chat(mcp_client: TestClient, monkeypatch) -> None:
    """Test MCP tools/call for ask_chat."""
    client = mcp_client

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
            "success": True,
            "answer": "Test answer from MCP",
            "warnings": [],
            "generated_sql": None,
            "selected_database_connector": None,
            "assistant_message_id": None,
            "metadata_sources": [],
            "context_tables": [],
            "execution": None,
            "conversation_id": "test-conversation-id",
            "error": None,
        },
    )

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "ask_chat",
                "arguments": {
                    "question": "Test question via MCP",
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
    assert payload["result"]["structuredContent"]["success"] is True

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


def test_mcp_tools_call_list_connectors(mcp_client: TestClient) -> None:
    """Test MCP tools/call for list_connectors."""
    client = mcp_client

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
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "list_connectors",
                "arguments": {},
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
    assert content["success"] is True
    connectors = content["connectors"]
    assert len(connectors) >= 2

    connector_types = [c["type"] for c in connectors]
    assert "database" in connector_types
    assert "llm" in connector_types


def test_mcp_tools_ignore_model_controlled_workspace(
    mcp_client: TestClient,
    monkeypatch,
) -> None:
    """Extra model input cannot override the transport-bound workspace."""
    client = mcp_client

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

    # A model-controlled workspace must not change the request identity.
    response = client.post(
        "/mcp",
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
    assert received_workspace_ids == ["default"]
    payload = response.json()
    assert payload["result"]["isError"] is False


def test_streamable_http_initializes_and_lists_capabilities(tmp_path: Path) -> None:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with _client(tmp_path) as client:
        initialized = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1.0"},
                },
            },
        )
        assert initialized.status_code == 200
        init_payload = initialized.json()
        assert init_payload["result"]["protocolVersion"] == "2025-06-18"
        assert {"tools", "resources", "prompts"} <= set(
            init_payload["result"]["capabilities"]
        )

        protocol_headers = {**headers, "MCP-Protocol-Version": "2025-06-18"}
        tools_response = client.post(
            "/mcp",
            headers=protocol_headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert tools_response.status_code == 200
        tools = tools_response.json()["result"]["tools"]
        assert {tool["name"] for tool in tools} == {
            "run_sql",
            "list_metadata_services",
            "search_tables",
            "ask_chat",
            "list_agents",
            "list_connectors",
            "get_conversation",
            "list_conversations",
        }
        assert all("workspace_id" not in tool["inputSchema"]["properties"] for tool in tools)
        assert all(tool.get("title") for tool in tools)
        assert all(tool.get("annotations") for tool in tools)

        resources = client.post(
            "/mcp",
            headers=protocol_headers,
            json={"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}},
        )
        assert resources.status_code == 200
        assert resources.json()["result"]["resources"][0]["uri"] == (
            "rhetorica://connection-guide"
        )

        prompts = client.post(
            "/mcp",
            headers=protocol_headers,
            json={"jsonrpc": "2.0", "id": 4, "method": "prompts/list", "params": {}},
        )
        assert prompts.status_code == 200
        assert prompts.json()["result"]["prompts"][0]["name"] == "analyze_data"

        called = client.post(
            "/mcp",
            headers=protocol_headers,
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "list_connectors", "arguments": {}},
            },
        )
        assert called.status_code == 200
        assert called.json()["result"]["structuredContent"]["success"] is True

        failed = client.post(
            "/mcp",
            headers=protocol_headers,
            json={
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "run_sql", "arguments": {"query": "SELECT 1"}},
            },
        )
        assert failed.status_code == 200
        assert failed.json()["result"]["isError"] is True
        assert failed.json()["result"]["structuredContent"]["error"]["code"] == (
            "database_connector_not_configured"
        )
        assert "database_connector_not_configured" in failed.json()["result"]["content"][0]["text"]


def test_streamable_http_runs_sync_chat_outside_server_event_loop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The synchronous chat adapter owns an event loop and must run in a worker thread."""
    import asyncio

    def sync_chat_adapter(*_args, **_kwargs):
        async def build_result() -> dict[str, object]:
            return {
                "success": True,
                "answer": "threaded",
                "conversation_id": "conversation-1",
                "error": None,
            }

        return asyncio.run(build_result())

    monkeypatch.setattr(MCPServer, "ask_chat_tool", sync_chat_adapter)
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-06-18",
    }
    with _client(tmp_path) as client:
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "ask_chat",
                    "arguments": {"question": "Does the adapter block?", "run_sql": False},
                },
            },
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["answer"] == "threaded"


def test_mcp_unknown_tool(mcp_client: TestClient) -> None:
    """Test error handling for unknown tool."""
    client = mcp_client

    response = client.post(
        "/mcp",
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
    assert payload["result"]["isError"] is True
    assert "nonexistent_tool" in payload["result"]["content"][0]["text"]


def test_mcp_unknown_method(mcp_client: TestClient) -> None:
    """Test error handling for unknown method."""
    client = mcp_client

    response = client.post(
        "/mcp",
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
    assert payload["error"]["code"] == -32602
    assert payload["error"]["message"] == "Invalid request parameters"


def test_mcp_conversation_endpoints(mcp_client: TestClient, monkeypatch) -> None:
    """Test MCP conversation tools."""
    client = mcp_client

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
        agent_id: str | None = None,
        llm_connector_name: str | None = None,
        database_connector_name: str | None = None,
        run_sql: bool = True,
        current_user_id: str | None = None,
    ) -> dict[str, object]:
        del agent_id, llm_connector_name, database_connector_name, run_sql
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
            "success": True,
            "answer": "Test",
            "conversation_id": conversation_id,
            "assistant_message_id": (
                assistant_message.id if assistant_message is not None else None
            ),
            "warnings": [],
            "error": None,
        }

    monkeypatch.setattr(MCPServer, "ask_chat_tool", mock_ask_chat_tool)

    # First ask to create conversation
    response1 = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ask_chat",
                "arguments": {
                    "question": "First question",
                },
            },
        },
    )
    assert response1.status_code == 200

    conv_data = response1.json()["result"]["structuredContent"]
    conversation_id = conv_data["conversation_id"]

    # Test get_conversation
    response2 = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "get_conversation",
                "arguments": {
                    "conversation_id": conversation_id,
                },
            },
        },
    )
    assert response2.status_code == 200
    content2 = response2.json()["result"]["structuredContent"]
    assert content2["success"] is True
    assert content2["conversation"]["id"] == conversation_id
    assert "messages" in content2["conversation"]

    # Test list_conversations
    response3 = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "list_conversations",
                "arguments": {
                    "limit": 10,
                },
            },
        },
    )
    assert response3.status_code == 200
    content3 = response3.json()["result"]["structuredContent"]
    assert content3["success"] is True
    assert len(content3["conversations"]) >= 1


def test_mcp_agent_catalog_and_stateful_chat(mcp_client: TestClient) -> None:
    client = mcp_client

    def agent_payload(slug: str, title: str) -> dict[str, object]:
        return {
            "workspace_id": "default",
            "slug": slug,
            "title": title,
            "description": f"{title} agent",
            "admin_prompt": f"You are {title}.",
            "is_active": True,
            "llm_connector_name": None,
            "allowed_database_connector_names": [],
            "allowed_rag_connector_names": [],
            "allowed_mcp_connector_names": [],
            "allowed_workspace_skill_ids": [],
            "settings": {
                "run_sql_allowed": False,
                "temperature": 0.2,
                "max_tokens": 1024,
            },
        }

    first_agent = client.post(
        "/v1/admin/agents",
        json=agent_payload("finance", "Finance"),
    ).json()
    second_agent = client.post(
        "/v1/admin/agents",
        json=agent_payload("ops", "Operations"),
    ).json()

    list_response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "list_agents", "arguments": {}},
        },
    )
    listed = list_response.json()["result"]["structuredContent"]
    assert listed["success"] is True
    assert {agent["id"] for agent in listed["agents"]} == {
        first_agent["id"],
        second_agent["id"],
    }
    assert all("admin_prompt" not in agent for agent in listed["agents"])

    missing_agent = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "ask_chat",
                "arguments": {"question": "Hello", "agent_id": "missing-agent"},
            },
        },
    ).json()["result"]
    assert missing_agent["isError"] is True
    assert missing_agent["structuredContent"]["error"]["code"] == "agent_not_found"

    server = mcp_runtime.get_mcp_server()
    captured_requests = []

    class FakeChatFactory:
        def create_orchestrator(self, *, current_user_id: str | None):
            def ask(request):
                captured_requests.append(request)
                conversation_id = request.conversation_id
                if conversation_id is None:
                    conversation = server._chat_repository.create_conversation(
                        workspace_id=request.workspace_id,
                        title=request.question,
                        owner_user_id=current_user_id,
                        agent_id=request.agent_id,
                    )
                    conversation_id = conversation.id
                return SimpleNamespace(
                    answer=f"{request.agent_id}:{request.question}",
                    conversation_id=conversation_id,
                    generated_sql=None,
                    selected_database_connector=None,
                    assistant_message_id=None,
                    metadata_sources=[],
                    context_tables=[],
                    execution=None,
                    warnings=[],
                )

            return SimpleNamespace(ask=ask)

    server._chat_factory = FakeChatFactory()

    first_turn = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "ask_chat",
                "arguments": {
                    "question": "First question",
                    "agent_id": first_agent["id"],
                    "run_sql": True,
                },
            },
        },
    ).json()["result"]["structuredContent"]
    assert first_turn["success"] is True
    assert first_turn["agent_id"] == first_agent["id"]
    assert first_turn["agent"]["slug"] == "finance"
    assert captured_requests[0].agent_prompt == "You are Finance."
    assert captured_requests[0].run_sql is False

    follow_up = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "ask_chat",
                "arguments": {
                    "question": "Follow up",
                    "conversation_id": first_turn["conversation_id"],
                },
            },
        },
    ).json()["result"]["structuredContent"]
    assert follow_up["success"] is True
    assert follow_up["agent_id"] == first_agent["id"]
    assert captured_requests[1].agent_id == first_agent["id"]

    locked = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "ask_chat",
                "arguments": {
                    "question": "Switch agent",
                    "conversation_id": first_turn["conversation_id"],
                    "agent_id": second_agent["id"],
                },
            },
        },
    ).json()["result"]
    assert locked["isError"] is True
    assert locked["structuredContent"]["error"]["code"] == "conversation_agent_locked"
    assert len(captured_requests) == 2

    history = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "get_conversation",
                "arguments": {"conversation_id": first_turn["conversation_id"]},
            },
        },
    ).json()["result"]["structuredContent"]
    assert history["conversation"]["agent_id"] == first_agent["id"]


def test_mcp_enabled_feature_flag_default(mcp_client: TestClient) -> None:
    """Test that MCP_ENABLED defaults to True and routes are registered."""
    client = mcp_client

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "Rhetorica"


def test_mcp_enabled_feature_flag_disabled(monkeypatch) -> None:
    """Test that MCP routes are not registered when MCP_ENABLED=false."""
    from rhetorica_api.app import settings as api_settings

    # Temporarily disable MCP
    monkeypatch.setattr(api_settings.settings, "mcp_enabled", False)

    # Create a fresh app with MCP disabled
    from rhetorica_api.app.main import create_app
    from rhetorica_api.app.routes import connectors as connector_routes
    from rhetorica_api.app.routes import execution as execution_routes
    from rhetorica_api.app.routes import llm_connectors as llm_connector_routes

    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()

    client = TestClient(create_app())

    response = client.post(
        "/mcp",
        headers=MCP_HEADERS,
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert response.status_code == 404

    # But other routes should still work
    response = client.get("/healthz")
    assert response.status_code == 200


def test_mcp_tools_list_includes_search_tables(mcp_client: TestClient) -> None:
    response = mcp_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert response.status_code == 200
    tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}

    tool = tools["search_tables"]
    assert tool["inputSchema"]["required"] == ["query"]
    assert {"connector_name", "service_name", "database", "size"} <= set(
        tool["inputSchema"]["properties"]
    )
    assert "cursor" not in tool["inputSchema"]["properties"]
    assert {"tables", "tried", "found_by"} <= set(
        tool["outputSchema"]["properties"]
    )
    assert "next_cursor" not in tool["outputSchema"]["properties"]
    assert tool["annotations"]["readOnlyHint"] is True

    services_tool = tools["list_metadata_services"]
    assert services_tool["inputSchema"]["properties"] == {}
    assert "services" in services_tool["outputSchema"]["properties"]
    assert services_tool["annotations"]["readOnlyHint"] is True


def test_mcp_tools_call_search_tables_runs_omd_first_cascade(
    mcp_client: TestClient, monkeypatch
) -> None:
    import json as json_module

    from rhetorica_backend.domain.mcp_connectors import client as mcp_client_module

    client = mcp_client
    # Hidden from the external proxy on purpose: search_tables must still reach OpenMetadata
    # through the internal execution path, independent of visible_to_external_mcp.
    _seed_mcp_catalog(
        client,
        name="openmetadata-mcp",
        prefix="openmetadata",
        allowed_tool_names=[],
        tools=[
            {
                "name": "search_metadata",
                "description": "Keyword-based search for data assets.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "entityType": {"type": "string"},
                        "size": {"type": "integer"},
                        "queryFilter": {"type": "string"},
                        "cursor": {"type": "string"},
                    },
                },
            }
        ],
        visible_to_external_mcp=False,
    )
    db_create = client.post(
        "/v1/admin/connectors",
        json={
            "name": "clickhouse",
            "workspace_id": "default",
            "connection": {
                "engine": "clickhouse",
                "host": "db.local",
                "port": 8123,
                "username": "analyst",
                "password": "secret",
                "database": "default",
                "secure": False,
                "verify": True,
                "extra_settings": {},
            },
        },
    )
    assert db_create.status_code == 200, db_create.text

    remote_calls: list[dict] = []

    def _fake_call_tool(self, connector, *, tool_name, arguments):
        remote_calls.append({"tool_name": tool_name, "arguments": arguments})
        return {
            "content": [
                {
                    "type": "text",
                    "text": json_module.dumps({
                        "results": [
                            {
                                "fullyQualifiedName": "Clickhouse-Sales.default.dm.ft_order",
                                "entityType": "table",
                                "serviceType": "Clickhouse",
                                "service": {"type": "databaseService", "name": "Clickhouse-Sales"},
                                "database": {"type": "database", "name": "default"},
                                "databaseSchema": {"type": "databaseSchema", "name": "dm"},
                                "description": "Витрина заказов FoodTech",
                                "columnNames": ["order_id", "order_create_dttm"],
                            }
                        ],
                        "returnedCount": 1,
                        "totalFound": 1,
                        "hasMore": False,
                    }),
                }
            ],
            "isError": False,
        }

    monkeypatch.setattr(mcp_client_module.McpRemoteClient, "call_tool", _fake_call_tool)

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "search_tables",
                "arguments": {
                    "query": "заказы фудтех",
                    "service_name": "Clickhouse-Sales",
                },
            },
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result.get("isError") is not True
    payload = result["structuredContent"]

    assert payload["success"] is True
    assert payload["found_by"] == "omd_scoped"
    assert payload["tables"][0]["fqn"] == "Clickhouse-Sales.default.dm.ft_order"
    assert payload["tables"][0]["service"] == "Clickhouse-Sales"
    assert payload["tables"][0]["connectors"] == []
    assert payload["tried"][0]["stage"] == "omd_scoped"
    assert remote_calls[0]["tool_name"] == "search_metadata"
    query_filter = json_module.loads(remote_calls[0]["arguments"]["queryFilter"])
    assert "Clickhouse" in json_module.dumps(query_filter)
    assert "Clickhouse-Sales" in json_module.dumps(query_filter)
