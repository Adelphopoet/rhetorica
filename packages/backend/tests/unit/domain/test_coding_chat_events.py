import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

from openai.types.responses import ResponseOutputMessage
from rhetorica_backend.domain.coding.chat_events import CodingSpecialistChatHooks

from agents.tool_context import ToolContext


def hooks():
    runs = Mock()
    task = SimpleNamespace(id="task", run_id="run", workspace_id="default", owner_user_id="owner")
    return CodingSpecialistChatHooks(runs=runs, task=task), runs


def context(arguments):
    return ToolContext(
        context=None,
        tool_name="repository_read",
        tool_call_id="call",
        tool_arguments=json.dumps(arguments),
    )


def test_specialist_tool_events_exclude_patch_content_and_report_denial():
    hook, runs = hooks()
    ctx = context({"path": "models/example.sql", "patch": "private repository body", "offset": 0})
    tool = SimpleNamespace(name="repository_read")
    asyncio.run(hook.on_tool_start(ctx, None, tool))
    assert runs.record_chat_event.call_args.kwargs["payload"]["arguments"] == {
        "path": "models/example.sql",
        "offset": 0,
    }
    asyncio.run(hook.on_tool_end(ctx, None, tool, '{"status":"denied"}'))
    assert runs.record_chat_event.call_args.kwargs["payload"]["success"] is False
    asyncio.run(hook.on_tool_end(ctx, None, tool, '{"exit_code":1}'))
    assert runs.record_chat_event.call_args.kwargs["payload"]["success"] is False


def test_non_ascii_messages_fit_durable_event_limit_without_exposing_reasoning():
    hook, runs = hooks()
    message = ResponseOutputMessage(
        id="m",
        role="assistant",
        status="completed",
        type="message",
        content=[{"type": "output_text", "text": "Привет 🌍\n" * 2000, "annotations": []}],
    )
    response = SimpleNamespace(
        response_id="response",
        output=[SimpleNamespace(type="reasoning", content="private"), message],
    )
    asyncio.run(hook.on_llm_end(None, None, response))
    event = runs.record_chat_event.call_args.kwargs
    assert event["event_type"] == "agent.message"
    assert event["payload"]["truncated"] is True
    assert len(json.dumps({**event["payload"], "event_key": event["event_key"]}).encode()) <= 4096
    assert "private" not in event["payload"]["content"]


def test_command_arguments_and_exit_code_are_visible_but_secrets_are_not():
    hook, runs = hooks()
    ctx = context({"argv": ["pytest", "tests/test_model.py", "--token", "do-not-show", "password=hidden"]})
    tool = SimpleNamespace(name="repository_run_command")
    asyncio.run(hook.on_tool_start(ctx, None, tool))
    visible = runs.record_chat_event.call_args.kwargs["payload"]["arguments"]
    assert visible["argv"][:2] == ["pytest", "tests/test_model.py"]
    assert "do-not-show" not in json.dumps(visible)
    assert "hidden" not in json.dumps(visible)
    asyncio.run(hook.on_tool_end(ctx, None, tool, json.dumps({"exit_code": 2, "stdout": "private rows", "duration_ms": 70})))
    payload = runs.record_chat_event.call_args.kwargs["payload"]
    assert payload["success"] is False
    assert "exit code: 2" in payload["result_summary"]
    assert "private rows" not in json.dumps(payload)


def test_nested_dbt_arguments_are_bounded_and_exclude_variables():
    hook, runs = hooks()
    ctx = context({"request": {"command": "compile", "selectors": ["кириллица" * 500] * 50, "project_subdir": ".", "variables": [{"name": "password", "value": "private"}]}})
    asyncio.run(hook.on_tool_start(ctx, None, SimpleNamespace(name="run_dbt")))
    payload = runs.record_chat_event.call_args.kwargs["payload"]
    assert len(json.dumps(payload).encode()) < 4096
    assert "private" not in json.dumps(payload)
    assert "compile" in json.dumps(payload)
