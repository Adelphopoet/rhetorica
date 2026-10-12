"""Re-export synced MCP connector tools as native tools of the Rhetorica MCP server."""

from __future__ import annotations

import json
from typing import Any

from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

from rhetorica_backend.domain.mcp_connectors import McpToolExecutionService


class McpProxyToolSurface:
    """Workspace-scoped view of synced MCP connector tools for external MCP clients.

    Tool names keep the catalog's exposed form, so one name resolves identically in the
    agent runtime and in the external MCP surface. Execution goes through the same
    governed service the agent uses; the connector credentials never leave the backend.
    """

    def __init__(
        self,
        *,
        execution_service: McpToolExecutionService,
        connector_repository: Any | None = None,
    ) -> None:
        self._execution_service = execution_service
        self._connector_repository = connector_repository

    def list_tools(self, *, workspace_id: str) -> list[Tool]:
        usage_by_tool = self._connector_usage_by_tool(workspace_id=workspace_id)
        tools: list[Tool] = []
        for definition in self._execution_service.external_tool_definitions(
            workspace_id=workspace_id,
            limit=None,
        ):
            function = definition["function"]
            tools.append(
                Tool(
                    name=function["name"],
                    description=self._describe(
                        function.get("description"),
                        usage_by_tool.get(function["name"]),
                    ),
                    inputSchema=function.get("parameters") or {"type": "object", "properties": {}},
                    annotations=ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                        idempotentHint=False,
                        openWorldHint=True,
                    ),
                )
            )
        return tools

    def _connector_usage_by_tool(self, *, workspace_id: str) -> dict[str, str]:
        """Map exposed tool name -> "[connector] usage_description".

        The agent runtime sees a connector's `usage_description` in its prompt; an external
        client saw nothing, which is why in the 2026-09-03 probe the model called
        OpenMetadata search without any filter and gave up on the noise. Prefixing the
        admin's guidance onto every tool of that connector is the cheapest way to hand the
        same hint to any client.
        """
        if self._connector_repository is None:
            return {}
        usage_by_connector: dict[str, str] = {}
        for connector in self._connector_repository.list(workspace_id=workspace_id, limit=None):
            usage = getattr(connector.connection, "usage_description", None)
            if isinstance(usage, str) and usage.strip():
                usage_by_connector[connector.name] = f"[{connector.name}] {usage.strip()}"
        if not usage_by_connector:
            return {}
        return {
            item.exposed_tool_name: usage_by_connector[item.connector_name]
            for item in self._connector_repository.list_active_tools(
                workspace_id=workspace_id, limit=None
            )
            if item.connector_name in usage_by_connector
        }

    @staticmethod
    def _describe(description: str | None, usage: str | None) -> str | None:
        if not usage:
            return description
        return f"{usage}\n\n{description}" if description else usage

    def has_tool(self, *, workspace_id: str, name: str) -> bool:
        return self._execution_service.is_external_tool_available(
            workspace_id=workspace_id,
            exposed_tool_name=name,
        )

    def call_tool(
        self,
        *,
        workspace_id: str,
        name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        payload = self._execution_service.execute_external(
            workspace_id=workspace_id,
            exposed_tool_name=name,
            arguments=arguments,
        )
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                )
            ],
            structuredContent=payload,
            isError=payload.get("ok") is not True,
        )
