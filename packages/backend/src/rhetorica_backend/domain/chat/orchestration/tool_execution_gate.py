"""Bound certified independent reads; serialize everything else against all reads."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class ToolExecutionGate:
    CERTIFIED_READS = frozenset({
        "inspect_tool_artifact",
        "read_tool_artifact",
        "find_tool_artifact_text",
    })

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    @asynccontextmanager
    async def enter(self, name: str) -> AsyncIterator[None]:
        read = self._enabled and name in self.CERTIFIED_READS
        async with self._condition:
            if read:
                await self._condition.wait_for(
                    lambda: not self._writer and not self._waiting_writers and self._readers < 4
                )
                self._readers += 1
            else:
                self._waiting_writers += 1
                try:
                    await self._condition.wait_for(lambda: not self._writer and self._readers == 0)
                    self._writer = True
                finally:
                    self._waiting_writers -= 1
                    self._condition.notify_all()
        try:
            yield
        finally:
            async with self._condition:
                if read:
                    self._readers -= 1
                else:
                    self._writer = False
                self._condition.notify_all()
