"""Project native planning lifecycle into the ordinary chat system log."""

import json
from collections.abc import Callable
from typing import Any

from openai.types.responses import ResponseReasoningSummaryTextDeltaEvent, ResponseTextDeltaEvent
from rhetorica_backend.contracts.chat import (
    ChunkEvent,
    StreamEvent,
    ThinkingEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

from agents import RunHooks
from agents.tool_context import ToolContext


class PlanningChatHooks(RunHooks[Any]):
    def __init__(self, emit: Callable[[StreamEvent], None]):
        self._emit = emit

    def response_event(self, event):
        if isinstance(event, ResponseTextDeltaEvent):
            self._emit(ChunkEvent(content=event.delta))
        elif isinstance(event, ResponseReasoningSummaryTextDeltaEvent):
            self._emit(ThinkingEvent(content=event.delta, delta=True))

    async def on_tool_start(self, context, agent, tool):
        if not isinstance(context, ToolContext):
            return
        arguments = json.loads(context.tool_arguments)
        # Planning tools expose paths and a task title, never connector secrets
        # or complete repository bodies in the browser log.
        visible = {
            key: arguments[key]
            for key in ("path", "start_line", "end_line", "task")
            if key in arguments
        }
        self._emit(
            ToolCallStartEvent(
                tool_name=tool.name, tool_call_id=context.tool_call_id, arguments=visible
            )
        )

    async def on_tool_end(self, context, agent, tool, result):
        if not isinstance(context, ToolContext):
            return
        value = json.loads(result)
        blocked = value.get("status") == "blocked"
        summaries = {
            "planning_read_file": "Файл прочитан.",
            "planning_list_tree": "Список файлов получен.",
            "propose_coding_plan": "План подготовлен. Разработка ждёт подтверждения в чате.",
        }
        self._emit(
            ToolCallEndEvent(
                tool_name=tool.name,
                tool_call_id=context.tool_call_id,
                success=not blocked,
                result_summary=(
                    f"GitLab отклонил чтение: HTTP {value['http_status']}."
                    if blocked
                    else summaries[tool.name]
                ),
            )
        )
