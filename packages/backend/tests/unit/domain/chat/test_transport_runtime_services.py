from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from rhetorica_backend.contracts.chat import ChunkEvent, ErrorEvent, ThinkingEvent
from rhetorica_backend.domain.chat.orchestration.agent_runtime import AgentRuntimeRunItemEvent
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.transport_runtime_services import (
    StreamingResponseContentService,
    StreamingRuntimeLoopService,
    SyncToolLoopRuntimeService,
)


def _resolve_success(tool_result: dict[str, object]) -> bool:
    """Resolve success: prefer explicit 'ok' field, fall back to checking 'status'."""
    return tool_result.get("ok", tool_result.get("status") == "succeeded")


class TestToolCallEndSuccessResolution:
    """Tests for success field resolution in on_tool_call_end callback."""

    def test_success_true_when_ok_field_is_true(self) -> None:
        """Direct tool execution with 'ok: True' should resolve success=True."""
        tool_result = {"ok": True, "data": "result"}
        assert _resolve_success(tool_result) is True

    def test_success_false_when_ok_field_is_false(self) -> None:
        """Direct tool execution with 'ok: False' should resolve success=False."""
        tool_result = {"ok": False, "error": "something went wrong"}
        assert _resolve_success(tool_result) is False

    def test_success_true_when_status_is_succeeded(self) -> None:
        """Worker compact payload with 'status: succeeded' should resolve success=True."""
        tool_result = {"status": "succeeded", "evidence_ids": ["exec-1"]}
        assert _resolve_success(tool_result) is True

    def test_success_false_when_status_is_failed(self) -> None:
        """Worker compact payload with 'status: failed' should resolve success=False."""
        tool_result = {"status": "failed", "blocker": "connection_error"}
        assert _resolve_success(tool_result) is False

    def test_success_false_when_status_is_blocked(self) -> None:
        """Worker compact payload with 'status: blocked' should resolve success=False."""
        tool_result = {"status": "blocked", "blocker": "row_budget_exceeded"}
        assert _resolve_success(tool_result) is False

    def test_success_false_when_status_is_cancelled(self) -> None:
        """Worker compact payload with 'status: cancelled' should resolve success=False."""
        tool_result = {"status": "cancelled"}
        assert _resolve_success(tool_result) is False

    def test_ok_takes_precedence_over_status(self) -> None:
        """When both 'ok' and 'status' are present, 'ok' takes precedence."""
        # This shouldn't happen in practice, but if it does, 'ok' should win
        tool_result = {"ok": False, "status": "succeeded"}
        assert _resolve_success(tool_result) is False

    def test_ok_true_takes_precedence_over_failed_status(self) -> None:
        """Explicit 'ok: True' overrides 'status: failed'."""
        tool_result = {"ok": True, "status": "failed"}
        assert _resolve_success(tool_result) is True

    def test_success_false_for_empty_dict(self) -> None:
        """Empty dict with no 'ok' or 'status' should resolve success=False."""
        tool_result = {}
        assert _resolve_success(tool_result) is False


def test_final_answer_tool_result_has_success_summary() -> None:
    result = {"ok": True, "status": "succeeded"}

    assert _resolve_success(result) is True
    assert StreamingResponseContentService.summarize_tool_result(result) == "Success"


def test_streaming_response_content_service_extracts_thinking_blocks() -> None:
    service = StreamingResponseContentService()
    content = (
        "<think>plan first</think>\n"
        "<thinking>then execute</thinking>\n"
        '{"answer":"Done","sql":null,"target":{}}'
    )

    events = service.process_content_for_thinking(content)

    assert len(events) == 3
    assert isinstance(events[0], ThinkingEvent)
    assert events[0].content == "plan first"
    assert isinstance(events[1], ThinkingEvent)
    assert events[1].content == "then execute"
    assert isinstance(events[2], ChunkEvent)
    assert "<think>" not in events[2].content
    assert "<thinking>" not in events[2].content


def test_streaming_response_content_service_emits_provider_reasoning_separately() -> None:
    service = StreamingResponseContentService()

    events = service.process_content_for_thinking(
        "Visible answer",
        reasoning_content="Provider reasoning",
    )

    assert len(events) == 2
    assert isinstance(events[0], ThinkingEvent)
    assert events[0].content == "Provider reasoning"
    assert isinstance(events[1], ChunkEvent)
    assert events[1].content == "Visible answer"


def test_streaming_response_content_service_parses_fenced_json_with_thinking() -> None:
    parsed, parsed_ok = StreamingResponseContentService.parse_json_payload(
        """
<think>internal reasoning</think>
```json
{"answer":"Привет!","sql":null,"target":{"connector_name":"dwh"}}
```
        """.strip()
    )

    assert parsed_ok is True
    assert parsed["answer"] == "Привет!"
    assert parsed["target"] == {"connector_name": "dwh"}


def test_streaming_response_content_service_emits_thinking_diagnostics() -> None:
    captured: list[tuple[str, dict[str, object]]] = []

    def emit(event: str, **fields: object) -> None:
        captured.append((event, fields))

    StreamingResponseContentService.emit_thinking_diagnostics(
        "<think>plan first</think><thinking>then execute</thinking>",
        emit=emit,
    )

    assert captured == [
        (
            "llm.turn.thinking_block",
            {"block_index": 1, "content": "plan first"},
        ),
        (
            "llm.turn.thinking_block",
            {"block_index": 2, "content": "then execute"},
        ),
    ]


def test_streaming_response_content_service_emits_provider_reasoning_diagnostic() -> None:
    captured: list[tuple[str, dict[str, object]]] = []

    def emit(event: str, **fields: object) -> None:
        captured.append((event, fields))

    StreamingResponseContentService.emit_thinking_diagnostics(
        "",
        reasoning_content="Provider reasoning",
        emit=emit,
    )

    assert captured == [
        (
            "llm.turn.thinking_block",
            {"block_index": 1, "content": "Provider reasoning"},
        )
    ]


def test_sync_tool_loop_runtime_service_uses_chat_completions_loop_when_responses_not_configured() -> (
    None
):
    captured: list[object] = []

    class _ExecutionCore:
        async def run_loop(self, **kwargs):
            captured.append(kwargs)
            return {"answer": "sdk-runtime"}

    service = SyncToolLoopRuntimeService(
        logger=logging.getLogger("test.sync_tool_runtime"),
        execution_core=_ExecutionCore(),
    )
    service._strict_runtime = SimpleNamespace(  # type: ignore[assignment]
        run=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("strict runtime should not be used")
        )
    )

    result = asyncio.run(
        service.run(
            llm_client=SimpleNamespace(
                connection=SimpleNamespace(
                    endpoint_path="/chat/completions",
                    extra_settings={},
                )
            ),
            request=SimpleNamespace(),
            db_connectors=[],
            messages=[],
            tools=[],
            state=SimpleNamespace(
                warnings=[],
                context_tables=[],
                prompt_version="v-test",
                generated_sql=None,
                execution=None,
                tool_call_count=0,
                preflight_pass=0,
                preflight_fail=0,
                preflight_uncertain=0,
                add_warning=lambda _warning: None,
            ),
            tool_executor=object(),
        )
    )

    assert result == {"answer": "sdk-runtime"}
    assert len(captured) == 1


def test_streaming_runtime_loop_service_uses_chat_completions_loop_when_responses_not_configured() -> (
    None
):
    captured: list[object] = []

    class _ExecutionCore:
        async def run_loop(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(payload={"answer": "sdk-runtime"}, events=[])

    service = StreamingRuntimeLoopService(
        logger=logging.getLogger("test.streaming_tool_runtime"),
        execution_core=_ExecutionCore(),
        content_service=StreamingResponseContentService(),
        tool_loop_result_factory=lambda *, payload, events: SimpleNamespace(
            payload=payload,
            events=events,
        ),
    )
    service._strict_runtime = SimpleNamespace(  # type: ignore[assignment]
        run=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("strict runtime should not be used")
        )
    )

    result = asyncio.run(
        service.run_prepared(
            runtime=SimpleNamespace(
                llm_client=SimpleNamespace(
                    connection=SimpleNamespace(
                        endpoint_path="/chat/completions",
                        extra_settings={},
                    )
                ),
                request=SimpleNamespace(),
                db_connectors=[],
                messages=[],
                tools=[],
                state=SimpleNamespace(
                    warnings=[],
                    context_tables=[],
                    prompt_version="v-test",
                    generated_sql=None,
                    execution=None,
                    checkpoint=None,
                    add_warning=lambda _warning: None,
                ),
                tool_executor=SimpleNamespace(),
            ),
            cancel_event=None,
            on_event=None,
        )
    )

    assert result.payload == {"answer": "sdk-runtime"}
    assert len(captured) == 1


def test_streaming_runtime_loop_service_records_provider_reasoning_event() -> None:
    class _ExecutionCore:
        async def run_loop(self, **kwargs):
            kwargs["transport"].prepare_response_content("", "Provider reasoning")
            return kwargs["transport"].adapt_payload({"answer": "done"})

    service = StreamingRuntimeLoopService(
        logger=logging.getLogger("test.streaming_tool_runtime"),
        execution_core=_ExecutionCore(),
        content_service=StreamingResponseContentService(),
        tool_loop_result_factory=lambda *, payload, events: SimpleNamespace(
            payload=payload,
            events=events,
        ),
    )

    result = asyncio.run(
        service.run_prepared(
            runtime=SimpleNamespace(
                llm_client=SimpleNamespace(
                    connection=SimpleNamespace(
                        endpoint_path="/chat/completions",
                        extra_settings={},
                    )
                ),
                request=SimpleNamespace(),
                db_connectors=[],
                messages=[],
                tools=[],
                state=ToolOrchestrationState(),
                tool_executor=SimpleNamespace(),
            ),
            cancel_event=None,
            on_event=None,
        )
    )

    thinking_events = [event for event in result.events if isinstance(event, ThinkingEvent)]
    assert len(thinking_events) == 1
    assert thinking_events[0].content == "Provider reasoning"


def test_streaming_runtime_loop_service_does_not_emit_error_event_for_recoverable_tool_timeout() -> (
    None
):
    class _ExecutionCore:
        async def run_loop(self, **kwargs):
            kwargs["transport"].on_run_item(
                AgentRuntimeRunItemEvent(
                    kind="tool_output",
                    tool_name="search_rag_insights",
                    tool_call_id="call-1",
                    tool_result={
                        "ok": False,
                        "error_code": "tool_timeout",
                        "user_message": "Tool timed out.",
                        "timeout_seconds": 0.01,
                        "can_continue": True,
                    },
                )
            )
            return kwargs["transport"].adapt_payload({"answer": "timeout"})

    service = StreamingRuntimeLoopService(
        logger=logging.getLogger("test.streaming_tool_runtime"),
        execution_core=_ExecutionCore(),
        content_service=StreamingResponseContentService(),
        tool_loop_result_factory=lambda *, payload, events: SimpleNamespace(
            payload=payload,
            events=events,
        ),
    )

    result = asyncio.run(
        service.run_prepared(
            runtime=SimpleNamespace(
                llm_client=SimpleNamespace(
                    connection=SimpleNamespace(
                        endpoint_path="/chat/completions",
                        extra_settings={},
                    )
                ),
                request=SimpleNamespace(),
                db_connectors=[],
                messages=[],
                tools=[],
                state=ToolOrchestrationState(),
                tool_executor=SimpleNamespace(),
            ),
            cancel_event=None,
            on_event=None,
        )
    )

    error_events = [event for event in result.events if isinstance(event, ErrorEvent)]
    assert error_events == []


def test_streaming_runtime_loop_service_emits_error_event_for_blocking_tool_timeout() -> None:
    class _ExecutionCore:
        async def run_loop(self, **kwargs):
            kwargs["transport"].on_run_item(
                AgentRuntimeRunItemEvent(
                    kind="tool_output",
                    tool_name="search_rag_insights",
                    tool_call_id="call-1",
                    tool_result={
                        "ok": False,
                        "error_code": "tool_timeout",
                        "user_message": "Tool timed out.",
                        "timeout_seconds": 0.01,
                        "can_continue": False,
                    },
                )
            )
            return kwargs["transport"].adapt_payload({"answer": "timeout"})

    service = StreamingRuntimeLoopService(
        logger=logging.getLogger("test.streaming_tool_runtime"),
        execution_core=_ExecutionCore(),
        content_service=StreamingResponseContentService(),
        tool_loop_result_factory=lambda *, payload, events: SimpleNamespace(
            payload=payload,
            events=events,
        ),
    )

    result = asyncio.run(
        service.run_prepared(
            runtime=SimpleNamespace(
                llm_client=SimpleNamespace(
                    connection=SimpleNamespace(
                        endpoint_path="/chat/completions",
                        extra_settings={},
                    )
                ),
                request=SimpleNamespace(),
                db_connectors=[],
                messages=[],
                tools=[],
                state=ToolOrchestrationState(),
                tool_executor=SimpleNamespace(),
            ),
            cancel_event=None,
            on_event=None,
        )
    )

    error_events = [event for event in result.events if isinstance(event, ErrorEvent)]
    assert len(error_events) == 1
    assert error_events[0].code == "tool_timeout"
    assert error_events[0].details["tool_name"] == "search_rag_insights"


def test_streaming_runtime_loop_service_marks_cancelled_runtime() -> None:
    cancel_event = asyncio.Event()
    cancel_event.set()
    state = ToolOrchestrationState()

    class _ExecutionCore:
        def run_loop(self, **_kwargs):
            raise AssertionError("cancelled runtime should not enter tool loop")

    service = StreamingRuntimeLoopService(
        logger=logging.getLogger("test.streaming_tool_runtime"),
        execution_core=_ExecutionCore(),
        content_service=StreamingResponseContentService(),
        tool_loop_result_factory=lambda *, payload, events: SimpleNamespace(
            payload=payload,
            events=events,
        ),
    )

    result = asyncio.run(
        service.run_prepared(
            runtime=SimpleNamespace(
                llm_client=SimpleNamespace(
                    connection=SimpleNamespace(
                        endpoint_path="/chat/completions",
                        extra_settings={},
                    )
                ),
                request=SimpleNamespace(),
                db_connectors=[],
                messages=[],
                tools=[],
                state=state,
                tool_executor=SimpleNamespace(),
            ),
            cancel_event=cancel_event,
            on_event=None,
        )
    )

    assert result.payload["warnings"] == ["cancelled"]
    assert state.warnings == ["cancelled"]
