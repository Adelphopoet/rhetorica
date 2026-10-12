import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rhetorica_api.app.routes import chat_stream
from rhetorica_api.app.routes.chat_stream import _active_chat_runs, _coding_event_generator
from rhetorica_backend.domain.coding.errors import CodingRunError


def test_planning_failure_emits_one_safe_error_and_cleans_up(monkeypatch):
    log = Mock()
    monkeypatch.setattr(chat_stream, "logger", log)

    class Planner:
        async def stream(self, **kwargs):
            raise RuntimeError("secret upstream response")
            yield

    async def collect():
        return [
            e
            async for e in _coding_event_generator(
                planner=Planner(),
                payload=SimpleNamespace(conversation_id="chat"),
                user=SimpleNamespace(id="owner", workspace_id="ws"),
                run_id="failed-planner",
            )
        ]

    events = asyncio.run(collect())
    assert len(events) == 1 and "coding_planning_failed" in events[0]
    assert "secret upstream" not in events[0]
    assert "failed-planner" not in _active_chat_runs
    log.error.assert_called_once()
    fields = log.error.call_args.kwargs
    assert fields["error_type"] == "RuntimeError"
    assert fields["error_code"] == "coding_planning_failed"
    assert fields["conversation_id"] == "chat"
    assert "secret upstream" not in str(log.error.call_args)


@pytest.mark.parametrize(
    "code, expected",
    [
        ("coding_planning_iteration_limit_reached", "обычный лимит итераций чата"),
        ("coding_planning_timeout", "Модель не ответила"),
    ],
)
def test_planning_stop_explains_actual_reason_without_starting_coding(code, expected):
    class Planner:
        async def stream(self, **kwargs):
            raise CodingRunError(code)
            yield

    async def collect():
        return [
            event
            async for event in _coding_event_generator(
                planner=Planner(),
                payload=SimpleNamespace(conversation_id="chat"),
                user=SimpleNamespace(id="owner", workspace_id="ws"),
                run_id="stopped-planner",
            )
        ]

    events = asyncio.run(collect())
    assert len(events) == 1 and code in events[0] and expected in events[0]
    assert "Разработка не запускалась" in events[0]
    assert "stopped-planner" not in _active_chat_runs


def test_cancel_stops_pending_model_and_releases_chat_registration():
    finalized = []
    started = asyncio.Event()

    class Planner:
        async def stream(self, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
                yield
            finally:
                finalized.append(True)

    async def collect():
        stream = _coding_event_generator(
            planner=Planner(),
            payload=SimpleNamespace(conversation_id="chat"),
            user=SimpleNamespace(id="owner", workspace_id="ws"),
            run_id="cancel-planner",
        )
        response = asyncio.create_task(anext(stream))
        await started.wait()
        _active_chat_runs["cancel-planner"].cancel_event.set()
        event = await response
        await stream.aclose()
        return event

    assert '"cancelled":true' in asyncio.run(collect())
    assert finalized == [True]
    assert "cancel-planner" not in _active_chat_runs
