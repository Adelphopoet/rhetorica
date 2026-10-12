from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from rhetorica_backend.infra.postgres.mcp_connector_repository import ResolvedMcpConnector


class McpRemoteClientError(RuntimeError):
    """Remote MCP call failed in a way safe to expose as a tool error code."""


class McpRemoteClient:
    """Thin streamable-http MCP client wrapper."""

    def list_tools(self, connector: ResolvedMcpConnector) -> list[dict[str, Any]]:
        return self._run(self._list_tools(connector))

    def call_tool(
        self,
        connector: ResolvedMcpConnector,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return self._run(self._call_tool(connector, tool_name=tool_name, arguments=arguments))

    async def _list_tools(self, connector: ResolvedMcpConnector) -> list[dict[str, Any]]:
        async with (
            self._http_client(connector) as http_client,
            streamable_http_client(url=connector.url, http_client=http_client) as streams,
        ):
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await self._list_all_tools(session)
                return [self._tool_to_dict(tool) for tool in tools]

    @staticmethod
    async def _list_all_tools(session: Any) -> list[Any]:
        """Read every exact MCP tools/list page without truncating the catalog."""
        result = await session.list_tools()
        tools = list(result.tools)
        seen_cursors: set[str] = set()
        next_cursor = getattr(result, "nextCursor", None)
        while next_cursor is not None:
            if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
                raise McpRemoteClientError("Invalid or repeated MCP tools/list cursor")
            seen_cursors.add(next_cursor)
            result = await session.list_tools(cursor=next_cursor)
            tools.extend(result.tools)
            next_cursor = getattr(result, "nextCursor", None)
        return tools

    async def _call_tool(
        self,
        connector: ResolvedMcpConnector,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        async with (
            self._http_client(connector) as http_client,
            streamable_http_client(url=connector.url, http_client=http_client) as streams,
        ):
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return self._result_to_dict(result)

    @staticmethod
    def _http_client(connector: ResolvedMcpConnector) -> httpx.AsyncClient:
        timeout = httpx.Timeout(
            connector.timeout_seconds,
            read=max(connector.timeout_seconds, 300.0),
        )
        return httpx.AsyncClient(
            headers=connector.headers,
            timeout=timeout,
            verify=connector.verify_tls,
            follow_redirects=True,
        )

    @staticmethod
    def _tool_to_dict(tool: Any) -> dict[str, Any]:
        input_schema = getattr(tool, "inputSchema", None)
        if input_schema is None:
            input_schema = getattr(tool, "input_schema", None)
        if not isinstance(input_schema, dict):
            input_schema = {}
        return {
            "name": str(getattr(tool, "name", "") or ""),
            "description": getattr(tool, "description", None),
            "input_schema": input_schema,
        }

    @staticmethod
    def _result_to_dict(result: Any) -> dict[str, Any]:
        if hasattr(result, "model_dump"):
            payload = result.model_dump(mode="json")
            if isinstance(payload, dict):
                return payload
        return {
            "content": [McpRemoteClient._content_to_dict(item) for item in getattr(result, "content", [])],
            "isError": bool(getattr(result, "isError", False)),
            "structuredContent": getattr(result, "structuredContent", None),
        }

    @staticmethod
    def _content_to_dict(item: Any) -> dict[str, Any]:
        if hasattr(item, "model_dump"):
            payload = item.model_dump(mode="json")
            if isinstance(payload, dict):
                return payload
        text = getattr(item, "text", None)
        if isinstance(text, str):
            return {"type": "text", "text": text}
        return {"type": "unknown", "value": str(item)}

    @staticmethod
    def _run(awaitable: Awaitable[Any]) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)

        def runner() -> Any:
            return asyncio.run(awaitable)

        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(runner).result()
