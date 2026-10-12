from __future__ import annotations

import asyncio
import logging
from types import MethodType

from rhetorica_backend.contracts.chat import ChatAskRequest, ChunkEvent, ThinkingEvent
from rhetorica_backend.domain.chat.orchestration.streaming_orchestrator import (
    StreamingChatOrchestrator,
)


def test_process_content_extracts_provider_thinking_tags() -> None:
    orchestrator = object.__new__(StreamingChatOrchestrator)
    from rhetorica_backend.domain.chat.orchestration.transport_runtime_services import (
        StreamingResponseContentService,
    )

    orchestrator._streaming_content_service = StreamingResponseContentService()
    events = orchestrator._process_content_for_thinking(
        "<thinking>inspect evidence</thinking>Final answer"
    )

    assert [type(event) for event in events] == [ThinkingEvent, ChunkEvent]
    assert [event.content for event in events] == ["inspect evidence", "Final answer"]


def test_parse_json_payload_handles_fence_after_thinking() -> None:
    parsed, ok = StreamingChatOrchestrator._parse_json_payload(
        '<think>reason</think>```json\n{"answer_markdown":"done"}\n```'
    )

    assert ok is True
    assert parsed == {"answer_markdown": "done"}


def test_stream_disconnect_does_not_cancel_detached_chat_turn() -> None:
    async def scenario() -> None:
        orchestrator = object.__new__(StreamingChatOrchestrator)
        orchestrator._logger = logging.getLogger(__name__)
        orchestrator._detached_producers = set()
        release = asyncio.Event()
        completed = asyncio.Event()
        cancel_event = asyncio.Event()

        async def fake_run_stream_session(
            self,
            request,
            cancel_event=None,
            emit=None,
        ) -> None:
            _ = self, request, cancel_event
            assert emit is not None
            emit(ChunkEvent(content="started"))
            await release.wait()
            completed.set()

        orchestrator._run_stream_session = MethodType(
            fake_run_stream_session,
            orchestrator,
        )
        stream = orchestrator.stream_ask(
            ChatAskRequest(question="continue after disconnect"),
            cancel_event=cancel_event,
        )

        first = await stream.__anext__()
        assert first.content == "started"
        await stream.aclose()

        assert completed.is_set() is False
        assert cancel_event.is_set() is False
        release.set()
        await asyncio.wait_for(completed.wait(), timeout=1)
        await asyncio.sleep(0)
        assert orchestrator._detached_producers == set()

    asyncio.run(scenario())


def test_runtime_journal_captures_live_events_without_replaying_them():
    from types import SimpleNamespace

    from rhetorica_backend.contracts.chat import ToolCallEndEvent, ToolCallStartEvent

    async def scenario():
        orchestrator = object.__new__(StreamingChatOrchestrator)
        start = ToolCallStartEvent(tool_name="read", tool_call_id="call", arguments={"path": "file"})
        end = ToolCallEndEvent(tool_name="read", tool_call_id="call", success=True, result_summary="Read")
        async def run(**kwargs):
            kwargs["on_event"](start)
            kwargs["on_event"](end)
            kwargs["on_event"](ChunkEvent(content="Answer"))
            return SimpleNamespace(payload={"answer": "Answer"}, events=[])
        orchestrator._run_prepared_tool_loop_and_collect = run
        emitted = []
        result = await orchestrator._execute_stream_runtime_flow(runtime=None, cancel_event=None, emit=emitted.append)
        assert len(emitted) == 3
        assert result.transport_artifact.events == []
        assert result.transport_artifact.journal_events == [start.model_dump(mode="json"), end.model_dump(mode="json")]
    asyncio.run(scenario())
