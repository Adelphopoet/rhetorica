"""MCP routes for AI agent connectivity."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Request, Response, WebSocket
from fastapi.websockets import WebSocketDisconnect
from rhetorica_backend.mcp import McpRequestHandler, McpServerFactory

from rhetorica_api.app.auth import (
    AuthError,
    authenticate_websocket,
    bind_auth_context,
    ensure_workspace_access,
    get_current_auth_context,
    reset_auth_context,
)
from rhetorica_api.app.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])

_mcp_server_factory = McpServerFactory()


def _mcp_handler() -> McpRequestHandler:
    return McpRequestHandler(
        server=_mcp_server_factory.get_server(database_url=settings.database_url)
    )


@router.get("/")
async def mcp_root() -> dict[str, Any]:
    """MCP server info endpoint."""
    return {
        "name": "Rhetorica MCP Server",
        "version": "0.1.0",
        "description": "Model Context Protocol bridge for Rhetorica analytics workspace",
        "endpoints": {
            "sse": "/mcp/sse",
            "messages": "/mcp/messages",
        },
    }


@router.get("/sse")
async def mcp_sse(request: Request) -> Response:
    """MCP SSE transport endpoint for client-to-server messages.

    This endpoint implements the SSE (Server-Sent Events) transport
    for MCP protocol, allowing agents to receive streaming responses.
    """
    from sse_starlette import EventSourceResponse

    async def event_generator():
        """Generate SSE events from MCP server."""
        # Note: Full SSE implementation would require more complex setup
        # This is a placeholder for the SSE transport layer
        yield {
            "event": "message",
            "data": '{"status": "connected", "server": "Rhetorica MCP"}',
        }

    return EventSourceResponse(event_generator())


@router.post("/messages")
async def mcp_messages(request: Request) -> dict[str, Any]:
    """MCP message endpoint for tool calls and resource access.

    This endpoint handles MCP protocol messages including:
    - tools/list: List available tools
    - tools/call: Invoke a tool with parameters
    - resources/list: List available resources

    Args:
        request: FastAPI request with MCP message payload

    Returns:
        MCP response message
    """
    try:
        body = await request.json()
        try:
            auth_context = get_current_auth_context()
        except AuthError:
            auth_context = None
        method = body.get("method")
        params = body.get("params", {})
        workspace_id = (
            params.get("arguments", {}).get("workspace_id")
            if isinstance(params, dict)
            else None
        )
        workspace_id = workspace_id or (
            auth_context.workspace_id if auth_context is not None else "default"
        )
        if auth_context is not None and method == "tools/call":
            ensure_workspace_access(workspace_id, auth_context)
        return _mcp_handler().handle_message(
            body=body,
            workspace_id=workspace_id,
            current_user_id=auth_context.user_id if auth_context else None,
        )

    except Exception as exc:
        logger.exception("MCP message handling failed")
        return {
            "jsonrpc": "2.0",
            "id": body.get("id") if 'body' in locals() else None,
            "error": {
                "code": -32603,
                "message": f"Internal error: {exc}",
            },
        }


@router.websocket("/ws")
async def mcp_websocket(websocket: WebSocket) -> None:
    """Reject unauthorized websocket upgrades until MCP WS auth lands for real."""
    try:
        authenticate_websocket(websocket)
    except AuthError:
        await websocket.close(code=4401, reason="Unauthorized")
        return

    await websocket.accept()
    try:
        await websocket.send_json(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32000,
                    "message": "MCP WebSocket transport is not implemented",
                },
            }
        )
    finally:
        await websocket.close(code=1000)


@router.websocket("/ws/agent")
async def websocket_agent(websocket: WebSocket):
    """WebSocket endpoint for persistent agent connections.

    This endpoint provides a persistent connection for AI agents,
    allowing bidirectional communication with workspace isolation.

    Protocol:
    1. Agent connects and sends handshake with workspace_id
    2. Server creates session bound to workspace
    3. All subsequent messages are scoped to that workspace
    """
    try:
        auth_context = authenticate_websocket(websocket)
    except AuthError:
        await websocket.close(code=4401, reason="Unauthorized")
        return

    await websocket.accept()
    context_token = bind_auth_context(auth_context)

    try:
        # Wait for handshake
        data = await websocket.receive_json()
        workspace_id = data.get("workspace_id", auth_context.workspace_id)
        ensure_workspace_access(workspace_id, auth_context)

        # Send handshake response
        await websocket.send_json({
            "type": "handshake",
            "status": "connected",
            "workspace_id": workspace_id,
            "server": "Rhetorica MCP",
        })

        # Handle messages
        while True:
            try:
                message = await websocket.receive_json()
                method = message.get("method")
                params = message.get("params", {})

                # Add workspace_id to all tool calls
                if method == "tools/call":
                    if "arguments" not in params:
                        params["arguments"] = {}
                    params["arguments"]["workspace_id"] = workspace_id

                # Forward to MCP message handler
                # Create a mock request for the MCP handler
                from unittest.mock import Mock
                mock_request = Mock()
                message_id = message.get("id")

                async def _json_payload(
                    request_method: str | None = method,
                    request_params: dict[str, Any] = params,
                    request_id: Any = message_id,
                ) -> dict[str, Any]:
                    return {
                        "method": request_method,
                        "params": request_params,
                        "id": request_id,
                    }

                mock_request.json = _json_payload

                response = await mcp_messages(mock_request)
                await websocket.send_json(response)

            except WebSocketDisconnect:
                break
            except Exception as exc:
                logger.exception("WebSocket message handling failed")
                await websocket.send_json({
                    "type": "error",
                    "message": str(exc),
                })

    except WebSocketDisconnect:
        logger.info("Agent disconnected")
    except Exception as exc:
        logger.exception("WebSocket connection failed")
        with suppress(Exception):
            await websocket.send_json({
                "type": "error",
                "message": str(exc),
            })
    finally:
        reset_auth_context(context_token)
