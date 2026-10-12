"""MCP module for Rhetorica backend."""

from rhetorica_backend.mcp.factory import McpServerFactory
from rhetorica_backend.mcp.server import MCPServer

__all__ = ["MCPServer", "McpServerFactory"]
