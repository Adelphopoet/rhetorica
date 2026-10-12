"""Canonical MCP server runtime for the API process."""

from __future__ import annotations

from rhetorica_backend.mcp import MCPServer, McpServerFactory

from rhetorica_api.app.settings import settings

_mcp_server_factory = McpServerFactory()


def get_mcp_server() -> MCPServer:
    allowed_hosts = tuple(
        value.strip() for value in settings.mcp_allowed_hosts.split(",") if value.strip()
    )
    allowed_origins = tuple(
        value.strip() for value in settings.mcp_allowed_origins.split(",") if value.strip()
    )
    return _mcp_server_factory.get_server(
        database_url=settings.database_url,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
