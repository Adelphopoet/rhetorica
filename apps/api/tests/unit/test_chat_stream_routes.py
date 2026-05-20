"""Tests for streaming chat endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from rhetorica_api.app.chat_runtime import get_chat_service_factory
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import chat_stream as chat_stream_routes
from rhetorica_api.app.routes import confluence_connectors as confluence_connector_routes
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.routes import openmetadata_connectors as openmetadata_connector_routes
from rhetorica_api.app.settings import settings


def _client(tmp_path: Path) -> TestClient:
    """Create test client with fresh database."""
    settings.database_url = f"sqlite:///{tmp_path}/api_chat_stream.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    confluence_connector_routes._repository.cache_clear()
    openmetadata_connector_routes._repository.cache_clear()
    get_chat_service_factory.cache_clear()

    # Clear streaming factory cache if exists
    chat_stream_routes._get_factory.cache_clear()

    # Create database schema
    from rhetorica_backend.domain.execution import build_execution_service

    _, db_runtime = build_execution_service(settings.database_url)
    db_runtime.create_schema()

    return TestClient(create_app())


def _parse_sse_response(response: httpx.Response) -> list[dict]:
    """Parse SSE response into list of events."""
    events = []
    content = response.text

    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("event:"):
            event_type = line[6:].strip()
            # Next line should be data:
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("data:"):
                data_line = lines[i + 1].strip()
                data_json = data_line[5:].strip()
                try:
                    event_data = json.loads(data_json)
                    event_data["_event_type"] = event_type
                    events.append(event_data)
                except json.JSONDecodeError:
                    pass
                i += 2
            else:
                i += 1
        else:
            i += 1

    return events


def test_stream_factory_includes_workspace_skill_repository(tmp_path: Path) -> None:
    _client(tmp_path)

    factory = chat_stream_routes._get_factory()

    assert factory._workspace_skill_repository is not None


def test_stream_endpoint_returns_sse_events(tmp_path: Path, monkeypatch) -> None:
    """Test that streaming endpoint returns proper SSE events."""
    client = _client(tmp_path)

    # Setup LLM connector
    llm_create = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "test-llm",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4",
                "api_key": "test-key",
                "extra_settings": {"structured_output_enabled": True, "support_tool_calls": True},
            },
        },
    )
    assert llm_create.status_code == 200

    # Mock LLM response - use _async_request_chat_completion for streaming service
    async def mock_async_completion(*_args, **_kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"answer": "Here\'s the result", "sql": null, "target": null}',
                    }
                }
            ]
        }

    from rhetorica_backend.domain.chat.orchestration.streaming_orchestrator import (
        StreamingChatOrchestrator,
    )

    async def mock_run_tool_loop(*args, **kwargs):
        return type(
            "ToolLoopResult",
            (),
            {
                "payload": {
                    "answer": "Here's the result",
                    "sql": None,
                    "target": None,
                    "selected_database_connector": None,
                    "metadata_sources": [],
                    "context_tables": [],
                    "execution": None,
                    "warnings": [],
                },
                "events": [],
            },
        )()

    monkeypatch.setattr(
        StreamingChatOrchestrator,
        "_run_tool_loop_and_collect",
        mock_run_tool_loop,
    )

    # Make streaming request
    response = client.post(
        "/v1/chat/ask/stream",
        json={
            "question": "Test question",
            "workspace_id": "default",
            "run_sql": False,
        },
        headers={"Accept": "text/event-stream"},
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    # Parse SSE events
    events = _parse_sse_response(response)

    # Should have at least a done event
    assert len(events) >= 1

    # Find done event
    done_events = [e for e in events if e.get("_event_type") == "done"]
    assert len(done_events) == 1

    done_event = done_events[0]
    assert done_event["answer"] == "Here's the result"
    assert done_event["conversation_id"] is not None
    assert done_event["assistant_message_id"] is not None


def test_stream_endpoint_logs_accepted_request(tmp_path: Path, monkeypatch, caplog) -> None:
    client = _client(tmp_path)

    llm_create = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "test-llm",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4",
                "api_key": "test-key",
                "extra_settings": {"structured_output_enabled": True, "support_tool_calls": True},
            },
        },
    )
    assert llm_create.status_code == 200

    from rhetorica_backend.domain.chat.orchestration.streaming_orchestrator import (
        StreamingChatOrchestrator,
    )

    async def mock_run_tool_loop(*args, **kwargs):
        return type(
            "ToolLoopResult",
            (),
            {
                "payload": {
                    "answer": "Logged",
                    "sql": None,
                    "target": None,
                    "selected_database_connector": None,
                    "metadata_sources": [],
                    "context_tables": [],
                    "execution": None,
                    "warnings": [],
                },
                "events": [],
            },
        )()

    monkeypatch.setattr(
        StreamingChatOrchestrator,
        "_run_tool_loop_and_collect",
        mock_run_tool_loop,
    )

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/v1/chat/ask/stream",
            json={
                "question": "сколько заказов было отменено в августе 2025?",
                "workspace_id": "default",
                "run_sql": True,
            },
            headers={"Accept": "text/event-stream"},
        )

    assert response.status_code == 200
    accepted = next(
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict)
        and record.msg.get("event") == "api.chat.ask_stream.accepted"
    )
    assert accepted["workspace_id"] == "default"
    assert accepted["run_sql"] is True
    assert accepted["question"] == "сколько заказов было отменено в августе 2025?"


def test_stream_endpoint_rejects_transport_level_connector_pinning(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/chat/ask/stream",
        json={
            "question": "Test",
            "workspace_id": "default",
            "run_sql": True,
            "llm_connector_name": "forced-llm",
            "metadata_connector_name": "forced-metadata",
            "database_connector_name": "forced-db",
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    locations = {tuple(item["loc"]) for item in detail}
    assert ("body", "llm_connector_name") in locations
    assert ("body", "metadata_connector_name") in locations
    assert ("body", "database_connector_name") in locations


def test_stream_endpoint_creates_conversation(tmp_path: Path, monkeypatch) -> None:
    """Test that streaming endpoint creates conversation on first ask."""
    client = _client(tmp_path)

    # Setup LLM connector
    llm_create = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "test-llm",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4",
                "api_key": "test-key",
                "extra_settings": {"structured_output_enabled": True, "support_tool_calls": True},
            },
        },
    )
    assert llm_create.status_code == 200

    # Mock LLM response
    from rhetorica_backend.domain.chat.orchestration.streaming_orchestrator import (
        StreamingChatOrchestrator,
    )

    async def mock_run_tool_loop(*args, **kwargs):
        return type(
            "ToolLoopResult",
            (),
            {
                "payload": {
                    "answer": "Hello!",
                    "sql": None,
                    "target": None,
                    "selected_database_connector": None,
                    "metadata_sources": [],
                    "context_tables": [],
                    "execution": None,
                    "warnings": [],
                },
                "events": [],
            },
        )()

    monkeypatch.setattr(
        StreamingChatOrchestrator,
        "_run_tool_loop_and_collect",
        mock_run_tool_loop,
    )

    response = client.post(
        "/v1/chat/ask/stream",
        json={
            "question": "Hello",
            "workspace_id": "default",
            "run_sql": False,
        },
    )

    assert response.status_code == 200
    events = _parse_sse_response(response)

    done_events = [e for e in events if e.get("_event_type") == "done"]
    assert len(done_events) == 1

    conversation_id = done_events[0]["conversation_id"]
    assert conversation_id is not None
    assert len(conversation_id) == 36  # UUID length
    assert done_events[0]["assistant_message_id"] is not None


def test_stream_endpoint_with_thinking_tags(tmp_path: Path, monkeypatch) -> None:
    """Test that thinking tags are parsed and sent as separate events."""
    client = _client(tmp_path)

    # Setup LLM connector
    llm_create = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "test-llm",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4",
                "api_key": "test-key",
                "extra_settings": {"structured_output_enabled": True, "support_tool_calls": True},
            },
        },
    )
    assert llm_create.status_code == 200

    # Mock LLM response with thinking tags in content
    from rhetorica_backend.contracts.chat import ThinkingEvent
    from rhetorica_backend.domain.chat.orchestration.streaming_orchestrator import (
        StreamingChatOrchestrator,
    )

    async def mock_run_tool_loop_thinking(*args, **kwargs):
        return type(
            "ToolLoopResult",
            (),
            {
                "payload": {
                    "answer": "Here's the answer",
                    "sql": None,
                    "target": None,
                    "selected_database_connector": None,
                    "metadata_sources": [],
                    "context_tables": [],
                    "execution": None,
                    "warnings": [],
                },
                "events": [ThinkingEvent(content="Analyzing the question...")],
            },
        )()

    monkeypatch.setattr(
        StreamingChatOrchestrator,
        "_run_tool_loop_and_collect",
        mock_run_tool_loop_thinking,
    )

    response = client.post(
        "/v1/chat/ask/stream",
        json={
            "question": "Test",
            "workspace_id": "default",
            "run_sql": False,
        },
    )

    assert response.status_code == 200
    events = _parse_sse_response(response)

    # Should have thinking event and done event
    thinking_events = [e for e in events if e.get("_event_type") == "thinking"]
    done_events = [e for e in events if e.get("_event_type") == "done"]

    assert len(done_events) == 1
    assert len(thinking_events) >= 1
    assert thinking_events[0]["content"] == "Analyzing the question..."


def test_stream_endpoint_with_tool_calls(tmp_path: Path, monkeypatch) -> None:
    """Test that tool calls are sent as events."""
    client = _client(tmp_path)

    # Setup LLM connector
    llm_create = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "test-llm",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4",
                "api_key": "test-key",
                "extra_settings": {"structured_output_enabled": True, "support_tool_calls": True},
            },
        },
    )
    assert llm_create.status_code == 200

    # Setup metadata connector
    metadata_create = client.post(
        "/v1/admin/openmetadata-connectors",
        json={
            "name": "test-metadata",
            "workspace_id": "default",
            "connection": {
                "provider": "openmetadata",
                "base_url": "https://openmetadata.local",
                "api_token": "token",
            },
        },
    )
    assert metadata_create.status_code == 200

    # Mock LLM responses - first tool call, then final answer
    from rhetorica_backend.contracts.chat import ToolCallEndEvent, ToolCallStartEvent
    from rhetorica_backend.domain.chat.orchestration.streaming_orchestrator import (
        StreamingChatOrchestrator,
    )

    async def mock_run_tool_loop_tools(*args, **kwargs):
        return type(
            "ToolLoopResult",
            (),
            {
                "payload": {
                    "answer": "Found tables",
                    "sql": None,
                    "target": None,
                    "selected_database_connector": None,
                    "metadata_sources": ["openmetadata"],
                    "context_tables": ["svc.db.schema.table_gmv"],
                    "execution": None,
                    "warnings": [],
                },
                "events": [
                    ToolCallStartEvent(
                        tool_name="fetch_metadata_tables", arguments={"search": "gmv", "limit": 10}
                    ),
                    ToolCallEndEvent(
                        tool_name="fetch_metadata_tables",
                        result_summary="Found 1 items",
                        success=True,
                    ),
                ],
            },
        )()

    monkeypatch.setattr(
        StreamingChatOrchestrator,
        "_run_tool_loop_and_collect",
        mock_run_tool_loop_tools,
    )

    response = client.post(
        "/v1/chat/ask/stream",
        json={
            "question": "Find gmv tables",
            "workspace_id": "default",
            "run_sql": False,
        },
    )

    assert response.status_code == 200
    events = _parse_sse_response(response)

    # Should have tool call events
    tool_start_events = [e for e in events if e.get("_event_type") == "tool_call_start"]
    tool_end_events = [e for e in events if e.get("_event_type") == "tool_call_end"]

    assert len(tool_start_events) >= 1
    assert len(tool_end_events) >= 1

    # Verify tool name
    assert tool_start_events[0]["tool_name"] == "fetch_metadata_tables"


def test_stream_endpoint_error_handling(tmp_path: Path, monkeypatch) -> None:
    """Test error handling in streaming endpoint."""
    client = _client(tmp_path)

    # No LLM connector - should return error
    response = client.post(
        "/v1/chat/ask/stream",
        json={
            "question": "Test",
            "workspace_id": "default",
            "run_sql": False,
        },
    )

    assert response.status_code == 200
    events = _parse_sse_response(response)

    # Should have error event
    error_events = [e for e in events if e.get("_event_type") == "error"]
    assert len(error_events) >= 1

    error_event = error_events[0]
    assert error_event["code"] == "llm_connector_missing"


def test_stream_endpoint_content_type(tmp_path: Path) -> None:
    """Test that streaming endpoint returns correct content type."""
    client = _client(tmp_path)

    # No LLM connector - quick error response
    response = client.post(
        "/v1/chat/ask/stream",
        json={
            "question": "Test",
            "workspace_id": "default",
            "run_sql": False,
        },
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "no-cache" in response.headers["cache-control"]


def test_stream_endpoint_with_conversation_id(tmp_path: Path, monkeypatch) -> None:
    """Test streaming with existing conversation ID."""
    client = _client(tmp_path)

    # Setup LLM connector
    llm_create = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "test-llm",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4",
                "api_key": "test-key",
                "extra_settings": {"structured_output_enabled": True, "support_tool_calls": True},
            },
        },
    )
    assert llm_create.status_code == 200

    # Create conversation first
    conv_response = client.post(
        "/v1/chat/conversation",
        json={
            "workspace_id": "default",
            "title": "Test Conversation",
        },
    )
    assert conv_response.status_code == 200
    conversation_id = conv_response.json()["id"]

    # Mock LLM response
    from rhetorica_backend.domain.chat.orchestration.streaming_orchestrator import (
        StreamingChatOrchestrator,
    )

    async def mock_run_tool_loop_conv(*args, **kwargs):
        return type(
            "ToolLoopResult",
            (),
            {
                "payload": {
                    "answer": "Follow-up answer",
                    "sql": None,
                    "target": None,
                    "selected_database_connector": None,
                    "metadata_sources": [],
                    "context_tables": [],
                    "execution": None,
                    "warnings": [],
                },
                "events": [],
            },
        )()

    monkeypatch.setattr(
        StreamingChatOrchestrator,
        "_run_tool_loop_and_collect",
        mock_run_tool_loop_conv,
    )

    # Stream with conversation ID
    response = client.post(
        "/v1/chat/ask/stream",
        json={
            "question": "Follow-up question",
            "workspace_id": "default",
            "conversation_id": conversation_id,
            "run_sql": False,
        },
    )

    assert response.status_code == 200
    events = _parse_sse_response(response)

    done_events = [e for e in events if e.get("_event_type") == "done"]
    assert len(done_events) == 1
    assert done_events[0]["conversation_id"] == conversation_id


def test_stream_endpoint_keepalive_does_not_cancel_pending_event(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keepalive ping must not terminate stream before the next event arrives."""
    client = _client(tmp_path)

    llm_create = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "test-llm",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4",
                "api_key": "test-key",
                "extra_settings": {"structured_output_enabled": True, "support_tool_calls": True},
            },
        },
    )
    assert llm_create.status_code == 200

    from rhetorica_api.app.routes import chat_stream as chat_stream_routes
    from rhetorica_backend.contracts.chat import DoneEvent, ThinkingEvent

    class _FakeStreamingOrchestrator:
        async def stream_ask(self, payload, cancel_event=None):
            _ = payload, cancel_event
            yield ThinkingEvent(content="working")
            await asyncio.sleep(0.03)
            yield DoneEvent(
                answer="final answer",
                metadata_sources=[],
                context_tables=[],
                warnings=[],
            )

    class _FakeFactory:
        def create_streaming_orchestrator(self, current_user_id=None):
            _ = current_user_id
            return _FakeStreamingOrchestrator()

    monkeypatch.setattr(chat_stream_routes, "SSE_KEEPALIVE_SECONDS", 0.01)
    monkeypatch.setattr(chat_stream_routes, "_get_factory", lambda: _FakeFactory())

    response = client.post(
        "/v1/chat/ask/stream",
        json={
            "question": "Test keepalive",
            "workspace_id": "default",
            "run_sql": False,
        },
        headers={"Accept": "text/event-stream"},
    )

    assert response.status_code == 200
    assert ": ping" in response.text
    events = _parse_sse_response(response)
    done_events = [e for e in events if e.get("_event_type") == "done"]
    assert len(done_events) == 1
    assert done_events[0]["answer"] == "final answer"


def test_stream_done_event_contains_execution_payload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Execution payload from run_sql should be available in done event."""
    client = _client(tmp_path)

    llm_create = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "test-llm",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4",
                "api_key": "test-key",
                "extra_settings": {"structured_output_enabled": True, "support_tool_calls": True},
            },
        },
    )
    assert llm_create.status_code == 200

    from rhetorica_backend.domain.chat.orchestration.streaming_orchestrator import (
        StreamingChatOrchestrator,
    )

    async def mock_run_tool_loop_execution(*args, **kwargs):
        return type(
            "ToolLoopResult",
            (),
            {
                "payload": {
                    "answer": "Пример данных:",
                    "sql": "SELECT 1",
                    "target": None,
                    "selected_database_connector": "clickhouse-main",
                    "metadata_sources": [],
                    "context_tables": [],
                    "execution": {
                        "execution_id": "exec-1",
                        "status": "succeeded",
                        "engine": "clickhouse",
                        "query_id": "query-1",
                        "normalized_sql": "SELECT 1",
                        "applied_row_limit": 5000,
                        "row_count": 1,
                        "duration_ms": 12,
                        "warnings": [],
                        "violations": [],
                        "preview": {"columns": ["value"], "rows": [[1]]},
                        "error_message": None,
                    },
                    "warnings": [],
                },
                "events": [],
            },
        )()

    monkeypatch.setattr(
        StreamingChatOrchestrator,
        "_run_tool_loop_and_collect",
        mock_run_tool_loop_execution,
    )

    response = client.post(
        "/v1/chat/ask/stream",
        json={
            "question": "дай пример данных",
            "workspace_id": "default",
            "run_sql": True,
        },
    )
    assert response.status_code == 200
    events = _parse_sse_response(response)

    done_events = [e for e in events if e.get("_event_type") == "done"]
    assert len(done_events) == 1
    execution = done_events[0].get("execution")
    assert isinstance(execution, dict)
    assert execution["status"] == "succeeded"
    assert execution["preview"]["columns"] == ["value"]
    assert execution["preview"]["rows"] == [[1]]
    visualization = done_events[0].get("visualization")
    assert visualization is None


def test_stream_disables_tools_for_small_talk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Small-talk request should run with empty tool set."""
    client = _client(tmp_path)

    llm_create = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "test-llm",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4",
                "api_key": "test-key",
                "extra_settings": {"structured_output_enabled": True, "support_tool_calls": True},
            },
        },
    )
    assert llm_create.status_code == 200

    db_create = client.post(
        "/v1/admin/connectors",
        json={
            "name": "clickhouse",
            "workspace_id": "default",
            "connection": {
                "engine": "clickhouse",
                "host": "db.local",
                "port": 8123,
                "username": "analyst",
                "password": "secret",
                "database": "analytics",
                "secure": False,
                "verify": True,
                "extra_settings": {},
            },
        },
    )
    assert db_create.status_code == 200

    metadata_create = client.post(
        "/v1/admin/openmetadata-connectors",
        json={
            "name": "test-metadata",
            "workspace_id": "default",
            "connection": {
                "provider": "openmetadata",
                "base_url": "https://openmetadata.local",
                "api_token": "token",
            },
        },
    )
    assert metadata_create.status_code == 200

    from rhetorica_backend.domain.chat.orchestration.streaming_orchestrator import (
        StreamingChatOrchestrator,
    )

    captured_tools: list[list[dict]] = []

    class _FakeLlmClient:
        def responses_completion(
            self,
            messages=None,
            input_items=None,
            tools=None,
            response_schema=None,
            previous_response_id=None,
            tool_choice="auto",
        ):
            _ = messages, input_items, response_schema, previous_response_id, tool_choice
            captured_tools.append(tools if isinstance(tools, list) else [])
            return type(
                "Resp",
                (),
                {
                    "response_id": "resp-1",
                    "output_text": (
                        '{"answer_markdown":"Привет!","sql":null,'
                        '"target":null,"claims":[],'
                        '"evidence":[],"claim_evidence_links":[],'
                        '"uncertainty_flags":[]}'
                    ),
                    "function_calls": [],
                    "raw_body": {},
                },
            )

        def collect_usages(self) -> list:
            return []

    monkeypatch.setattr(
        StreamingChatOrchestrator,
        "_create_llm_client",
        lambda _self, _connection: _FakeLlmClient(),
    )

    response = client.post(
        "/v1/chat/ask/stream",
        json={
            "question": "привет",
            "workspace_id": "default",
            "run_sql": True,
        },
    )
    assert response.status_code == 200
    events = _parse_sse_response(response)
    done_events = [e for e in events if e.get("_event_type") == "done"]
    assert len(done_events) == 1
    assert done_events[0]["answer"] == "Привет!"
    assert captured_tools and captured_tools[0] == []


def test_stream_disables_run_sql_for_metadata_definition_questions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Definition-style metadata questions should not expose run_sql."""
    client = _client(tmp_path)

    llm_create = client.post(
        "/v1/admin/llm-connectors",
        json={
            "name": "test-llm",
            "workspace_id": "default",
            "connection": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4",
                "api_key": "test-key",
                "extra_settings": {"structured_output_enabled": True, "support_tool_calls": True},
            },
        },
    )
    assert llm_create.status_code == 200

    db_create = client.post(
        "/v1/admin/connectors",
        json={
            "name": "clickhouse",
            "workspace_id": "default",
            "connection": {
                "engine": "clickhouse",
                "host": "db.local",
                "port": 8123,
                "username": "analyst",
                "password": "secret",
                "database": "analytics",
                "secure": False,
                "verify": True,
                "extra_settings": {},
            },
        },
    )
    assert db_create.status_code == 200

    metadata_create = client.post(
        "/v1/admin/openmetadata-connectors",
        json={
            "name": "test-metadata",
            "workspace_id": "default",
            "connection": {
                "provider": "openmetadata",
                "base_url": "https://openmetadata.local",
                "api_token": "token",
            },
        },
    )
    assert metadata_create.status_code == 200

    from rhetorica_backend.domain.chat.orchestration.streaming_orchestrator import (
        StreamingChatOrchestrator,
    )

    captured_tools: list[list[dict]] = []

    class _FakeLlmClient:
        def responses_completion(
            self,
            messages=None,
            input_items=None,
            tools=None,
            response_schema=None,
            previous_response_id=None,
            tool_choice="auto",
        ):
            _ = messages, input_items, response_schema, previous_response_id, tool_choice
            captured_tools.append(tools if isinstance(tools, list) else [])
            return type(
                "Resp",
                (),
                {
                    "response_id": "resp-1",
                    "output_text": (
                        '{"answer_markdown":"ft_order - это факт заказов.","sql":null,'
                        '"target":null,"claims":[],'
                        '"evidence":[],"claim_evidence_links":[],'
                        '"uncertainty_flags":[]}'
                    ),
                    "function_calls": [],
                    "raw_body": {},
                },
            )

        def collect_usages(self) -> list:
            return []

    monkeypatch.setattr(
        StreamingChatOrchestrator,
        "_create_llm_client",
        lambda _self, _connection: _FakeLlmClient(),
    )

    response = client.post(
        "/v1/chat/ask/stream",
        json={
            "question": "что такое ft_order",
            "workspace_id": "default",
            "run_sql": True,
        },
    )
    assert response.status_code == 200
    events = _parse_sse_response(response)
    done_events = [e for e in events if e.get("_event_type") == "done"]
    assert len(done_events) == 1

    tool_names = {
        tool["function"]["name"]
        for tool in captured_tools[0]
        if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
    }
    assert "run_sql" not in tool_names
    assert "search_metadata_catalog" in tool_names
