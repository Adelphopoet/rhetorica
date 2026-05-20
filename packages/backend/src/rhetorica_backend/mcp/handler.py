"""JSON-RPC request handling for MCP HTTP and WebSocket transports."""

from __future__ import annotations

import logging
from typing import Any

from rhetorica_backend.mcp.server import MCPServer

logger = logging.getLogger(__name__)

_TOOLS = [
    {
        "name": "run_sql",
        "description": "Execute a SQL query with governance and safety checks",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "SQL query to execute"},
                "workspace_id": {"type": "string", "description": "Workspace identifier"},
                "connector_name": {"type": "string", "description": "Specific database connector for the query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_metadata",
        "description": "Search for tables and columns in metadata registry",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search_query": {"type": "string", "description": "Search term"},
                "workspace_id": {"type": "string", "description": "Workspace identifier"},
                "metadata_connector_name": {"type": "string", "description": "Specific connector"},
            },
            "required": ["search_query"],
        },
    },
    {
        "name": "ask_chat",
        "description": "Ask a natural language question about analytics data",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Natural language question"},
                "workspace_id": {"type": "string", "description": "Workspace identifier"},
                "conversation_id": {"type": "string", "description": "Conversation ID for context"},
                "llm_connector_name": {"type": "string", "description": "LLM connector name"},
                "metadata_connector_name": {"type": "string", "description": "Metadata connector name"},
                "database_connector_name": {"type": "string", "description": "Database connector name"},
                "run_sql": {"type": "boolean", "description": "Execute generated SQL"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "list_connectors",
        "description": "List available connectors for the workspace",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "description": "Workspace identifier"},
                "connector_type": {"type": "string", "description": "Filter: database, llm, metadata"},
            },
        },
    },
    {
        "name": "get_conversation",
        "description": "Get conversation history by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Conversation identifier"},
                "workspace_id": {"type": "string", "description": "Workspace identifier"},
            },
            "required": ["conversation_id"],
        },
    },
    {
        "name": "list_conversations",
        "description": "List conversations for a workspace",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "description": "Workspace identifier"},
                "limit": {"type": "integer", "description": "Maximum conversations to return"},
            },
        },
    },
]


class McpRequestHandler:
    """Route-agnostic JSON-RPC handler for MCP tool traffic."""

    def __init__(self, *, server: MCPServer) -> None:
        self._server = server

    def handle_message(
        self,
        *,
        body: dict[str, Any],
        workspace_id: str,
        current_user_id: str | None = None,
    ) -> dict[str, Any]:
        method = body.get("method")
        params = body.get("params", {})
        message_id = body.get("id")

        try:
            if method == "tools/list":
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {"tools": list(_TOOLS)},
                }
            if method != "tools/call":
                return self._error_response(
                    message_id=message_id,
                    code=-32601,
                    message=f"Method not found: {method}",
                )

            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            if not tool_name:
                return self._error_response(
                    message_id=message_id,
                    code=-32602,
                    message="Missing tool name",
                )

            content = self._execute_tool(
                tool_name=tool_name,
                tool_args=tool_args,
                workspace_id=workspace_id,
                current_user_id=current_user_id,
            )
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "content": [{"type": "text", "text": str(content)}],
                },
            }
        except Exception as exc:
            logger.exception("MCP message handling failed")
            return self._error_response(
                message_id=message_id,
                code=-32603,
                message=f"Internal error: {exc}",
            )

    def _execute_tool(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        workspace_id: str,
        current_user_id: str | None,
    ) -> Any:
        if tool_name == "run_sql":
            return self._server.run_sql_tool(
                query=tool_args.get("query", ""),
                workspace_id=workspace_id,
                connector_name=tool_args.get("connector_name"),
            )
        if tool_name == "search_metadata":
            return self._server.search_metadata_tool(
                search_query=tool_args.get("search_query", ""),
                workspace_id=workspace_id,
                metadata_connector_name=tool_args.get("metadata_connector_name"),
            )
        if tool_name == "ask_chat":
            return self._server.ask_chat_tool(
                question=tool_args.get("question", ""),
                workspace_id=workspace_id,
                conversation_id=tool_args.get("conversation_id"),
                llm_connector_name=tool_args.get("llm_connector_name"),
                metadata_connector_name=tool_args.get("metadata_connector_name"),
                database_connector_name=tool_args.get("database_connector_name"),
                run_sql=tool_args.get("run_sql", True),
                current_user_id=current_user_id,
            )
        if tool_name == "list_connectors":
            return self._server.list_connectors_tool(
                workspace_id=workspace_id,
                connector_type=tool_args.get("connector_type"),
            )
        if tool_name == "get_conversation":
            conversation_id = tool_args.get("conversation_id")
            if not isinstance(conversation_id, str) or not conversation_id.strip():
                return {"error": "Missing conversation_id"}
            return self._server.get_conversation_tool(
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                current_user_id=current_user_id,
            )
        if tool_name == "list_conversations":
            return self._server.list_conversations_tool(
                workspace_id=workspace_id,
                limit=tool_args.get("limit", 50),
                current_user_id=current_user_id,
            )
        raise ValueError(f"Unknown tool: {tool_name}")

    @staticmethod
    def _error_response(
        *,
        message_id: Any,
        code: int,
        message: str,
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {
                "code": code,
                "message": message,
            },
        }
