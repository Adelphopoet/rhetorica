"""Persist specialist messages and bounded tool lifecycle for ordinary chat."""

import asyncio
import hashlib
import json

from openai.types.responses import ResponseOutputMessage
from rhetorica_backend.contracts.coding import AgentTask
from rhetorica_backend.domain.coding.chat_tool_projection import (
    bounded_text as _bounded_text,
)
from rhetorica_backend.domain.coding.chat_tool_projection import (
    visible_arguments,
    visible_result,
)
from rhetorica_backend.infra.postgres.coding_run_repository import CodingRunRepository

from agents import RunHooks
from agents.tool_context import ToolContext


class CodingSpecialistChatHooks(RunHooks):
    def __init__(self, *, runs: CodingRunRepository, task: AgentTask):
        self._runs, self._task = runs, task

    async def _record(self, event_type, event_key, payload):
        await asyncio.to_thread(
            self._runs.record_chat_event,
            run_id=self._task.run_id,
            task_id=self._task.id,
            workspace_id=self._task.workspace_id,
            owner_user_id=self._task.owner_user_id,
            event_type=event_type,
            event_key=event_key,
            payload=payload,
        )

    async def on_tool_start(self, context, agent, tool):
        if not isinstance(context, ToolContext):
            return
        arguments = json.loads(context.tool_arguments)
        visible = visible_arguments(arguments)
        await self._record(
            "agent.tool_started",
            context.tool_call_id,
            {
                "tool_name": tool.name,
                "tool_call_id": context.tool_call_id,
                "arguments": visible,
            },
        )

    async def on_tool_end(self, context, agent, tool, result):
        if not isinstance(context, ToolContext):
            return
        value = json.loads(result)
        success, summary = visible_result(value)
        await self._record(
            "agent.tool_completed",
            context.tool_call_id,
            {
                "tool_name": tool.name,
                "tool_call_id": context.tool_call_id,
                "success": success,
                "result_summary": summary,
            },
        )

    async def on_llm_end(self, context, agent, response):
        for item in response.output:
            if not isinstance(item, ResponseOutputMessage):
                continue
            text = "\n".join(part.text for part in item.content if part.type == "output_text")
            if not text.strip():
                continue
            visible = _bounded_text(text, 3000)
            key = hashlib.sha256(f"{response.response_id}:{item.id}:{text}".encode()).hexdigest()
            await self._record(
                "agent.message",
                key,
                {
                    "content": visible,
                    "truncated": visible != text,
                },
            )
