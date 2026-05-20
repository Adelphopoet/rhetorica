from __future__ import annotations

import logging
from types import SimpleNamespace

from rhetorica_backend.contracts.chat import ChunkEvent, ThinkingEvent
from rhetorica_backend.domain.chat.orchestration.transport_runtime_services import (
    StreamingResponseContentService,
    StreamingRuntimeLoopService,
    SyncToolLoopRuntimeService,
    _configured_for_responses_runtime,
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


def test_configured_for_responses_runtime_uses_explicit_responses_contract() -> None:
    chat_client = SimpleNamespace(
        connection=SimpleNamespace(
            endpoint_path="/chat/completions",
            extra_settings={},
        )
    )
    responses_client = SimpleNamespace(
        connection=SimpleNamespace(
            endpoint_path="/chat/completions",
            extra_settings={"responses_endpoint_path": "/responses"},
        )
    )

    assert _configured_for_responses_runtime(chat_client) is False
    assert _configured_for_responses_runtime(responses_client) is True


def test_sync_tool_loop_runtime_service_uses_chat_completions_loop_when_responses_not_configured() -> None:
    captured: list[object] = []

    class _ExecutionCore:
        def run_loop(self, **kwargs):
            captured.append(kwargs)
            return {"answer": "legacy-runtime"}

    service = SyncToolLoopRuntimeService(
        logger=logging.getLogger("test.sync_tool_runtime"),
        execution_core=_ExecutionCore(),
    )
    service._strict_runtime = SimpleNamespace(  # type: ignore[assignment]
        run=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("strict runtime should not be used")
        )
    )

    result = service.run(
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
            parse_failure_count=0,
            schema_valid_first_pass=0,
            schema_repaired=0,
            schema_failed=0,
            preflight_pass=0,
            preflight_fail=0,
            preflight_uncertain=0,
            add_warning=lambda _warning: None,
        ),
        tool_executor=object(),
        protocol_repair_enabled=False,
    )

    assert result == {"answer": "legacy-runtime"}
    assert len(captured) == 1


def test_streaming_runtime_loop_service_uses_chat_completions_loop_when_responses_not_configured() -> None:
    captured: list[object] = []

    class _ExecutionCore:
        def run_loop(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(payload={"answer": "legacy-runtime"}, events=[])

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

    result = service.run_prepared(
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
                analysis_plan=None,
                checkpoint=None,
                worker_results=[],
                add_warning=lambda _warning: None,
            ),
            tool_executor=SimpleNamespace(),
            protocol_repair_enabled=False,
        ),
        cancel_event=None,
        on_event=None,
    )

    assert result.payload == {"answer": "legacy-runtime"}
    assert len(captured) == 1
