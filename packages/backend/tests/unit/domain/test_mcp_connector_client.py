from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from rhetorica_backend.domain.mcp_connectors.client import (
    McpRemoteClient,
    McpRemoteClientError,
)


@dataclass(frozen=True)
class _ToolPage:
    tools: list[Any]
    nextCursor: str | None = None


class _PaginatedSession:
    def __init__(self, pages: dict[str | None, _ToolPage]) -> None:
        self._pages = pages
        self.cursors: list[str | None] = []

    async def list_tools(self, cursor: str | None = None) -> _ToolPage:
        self.cursors.append(cursor)
        return self._pages[cursor]


@pytest.mark.asyncio
async def test_list_all_tools_follows_every_mcp_cursor_page() -> None:
    session = _PaginatedSession({
        None: _ToolPage(tools=["first"], nextCursor="page-2"),
        "page-2": _ToolPage(tools=["second"], nextCursor="page-3"),
        "page-3": _ToolPage(tools=["third"]),
    })

    tools = await McpRemoteClient._list_all_tools(session)

    assert tools == ["first", "second", "third"]
    assert session.cursors == [None, "page-2", "page-3"]


@pytest.mark.asyncio
async def test_list_all_tools_rejects_repeated_mcp_cursor() -> None:
    session = _PaginatedSession({
        None: _ToolPage(tools=["first"], nextCursor="same"),
        "same": _ToolPage(tools=["second"], nextCursor="same"),
    })

    with pytest.raises(McpRemoteClientError, match="repeated MCP tools/list cursor"):
        await McpRemoteClient._list_all_tools(session)
