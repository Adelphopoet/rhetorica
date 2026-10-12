from __future__ import annotations

import json
from unittest.mock import MagicMock

from rhetorica_backend.mcp.proxy_tools import McpProxyToolSurface


def _definition(name: str, *, description: str | None, parameters: dict | None) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


def test_list_tools_maps_catalog_definitions_to_mcp_tools() -> None:
    execution_service = MagicMock()
    execution_service.external_tool_definitions.return_value = [
        _definition(
            "openmetadata-mcp__search_metadata",
            description="Search OpenMetadata assets.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
        _definition("mcp-jira__list_projects", description=None, parameters=None),
    ]
    surface = McpProxyToolSurface(execution_service=execution_service)

    tools = surface.list_tools(workspace_id="ws-1")

    execution_service.external_tool_definitions.assert_called_once_with(
        workspace_id="ws-1",
        limit=None,
    )
    assert [tool.name for tool in tools] == [
        "openmetadata-mcp__search_metadata",
        "mcp-jira__list_projects",
    ]
    assert tools[0].description == "Search OpenMetadata assets."
    assert tools[0].inputSchema["required"] == ["query"]
    assert tools[0].annotations is not None
    assert tools[0].annotations.readOnlyHint is False
    assert tools[0].annotations.destructiveHint is True
    assert tools[0].annotations.openWorldHint is True
    assert tools[1].inputSchema == {"type": "object", "properties": {}}


def test_has_tool_checks_workspace_catalog() -> None:
    execution_service = MagicMock()
    execution_service.is_external_tool_available.side_effect = (
        lambda *, workspace_id, exposed_tool_name: workspace_id == "ws-1"
        and exposed_tool_name == "omd__search_metadata"
    )
    surface = McpProxyToolSurface(execution_service=execution_service)

    assert surface.has_tool(workspace_id="ws-1", name="omd__search_metadata") is True
    assert surface.has_tool(workspace_id="ws-1", name="run_sql") is False
    assert execution_service.is_external_tool_available.call_count == 2
    execution_service.is_external_tool_available.assert_any_call(
        workspace_id="ws-1",
        exposed_tool_name="omd__search_metadata",
    )
    execution_service.is_external_tool_available.assert_any_call(
        workspace_id="ws-1",
        exposed_tool_name="run_sql",
    )


def test_call_tool_returns_structured_remote_result() -> None:
    execution_service = MagicMock()
    execution_service.execute_external.return_value = {
        "ok": True,
        "connector_name": "openmetadata-mcp",
        "remote_tool_name": "search_metadata",
        "result": {"content": [{"type": "text", "text": "dm.orders"}], "isError": False},
    }
    surface = McpProxyToolSurface(execution_service=execution_service)

    result = surface.call_tool(
        workspace_id="ws-1",
        name="openmetadata-mcp__search_metadata",
        arguments={"query": "orders"},
    )

    execution_service.execute_external.assert_called_once_with(
        workspace_id="ws-1",
        exposed_tool_name="openmetadata-mcp__search_metadata",
        arguments={"query": "orders"},
    )
    assert result.isError is False
    assert result.structuredContent["result"]["content"][0]["text"] == "dm.orders"
    assert json.loads(result.content[0].text)["connector_name"] == "openmetadata-mcp"


def test_call_tool_marks_governed_errors_as_tool_errors() -> None:
    execution_service = MagicMock()
    execution_service.execute_external.return_value = {
        "error_code": "mcp_tool_disabled",
        "user_message": "MCP tool is disabled or not present in the synced catalog.",
    }
    surface = McpProxyToolSurface(execution_service=execution_service)

    result = surface.call_tool(workspace_id="ws-1", name="omd__missing", arguments={})

    assert result.isError is True
    assert result.structuredContent["error_code"] == "mcp_tool_disabled"


def test_list_tools_prefixes_description_with_connector_usage() -> None:
    from types import SimpleNamespace

    execution_service = MagicMock()
    execution_service.external_tool_definitions.return_value = [
        _definition(
            "openmetadata__search_metadata",
            description="Keyword-based search for data assets.",
            parameters={"type": "object", "properties": {}},
        ),
    ]
    connector_repository = MagicMock()
    connector_repository.list_active_tools.return_value = [
        SimpleNamespace(
            exposed_tool_name="openmetadata__search_metadata",
            connector_name="openmetadata-mcp",
        )
    ]
    connector_repository.list.return_value = [
        SimpleNamespace(
            name="openmetadata-mcp",
            connection=SimpleNamespace(
                usage_description="OpenMetadata: filter by serviceType, size <= 5; prefer search_tables."
            ),
        )
    ]
    surface = McpProxyToolSurface(
        execution_service=execution_service,
        connector_repository=connector_repository,
    )

    tools = surface.list_tools(workspace_id="ws-1")

    assert tools[0].description == (
        "[openmetadata-mcp] OpenMetadata: filter by serviceType, size <= 5; prefer search_tables.\n\n"
        "Keyword-based search for data assets."
    )
    connector_repository.list.assert_called_once_with(workspace_id="ws-1", limit=None)
    connector_repository.list_active_tools.assert_called_once_with(
        workspace_id="ws-1", limit=None
    )
