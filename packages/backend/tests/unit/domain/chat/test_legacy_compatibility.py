from __future__ import annotations

from types import SimpleNamespace

import pytest
from rhetorica_backend.contracts.chat import ChatAskRequest, ThinkingEvent
from rhetorica_backend.domain.chat.orchestration.legacy_compatibility import (
    LegacyStreamingRuntimeCompatibilityAdapter,
    LegacySyncOrchestratorCompatibilityAdapter,
)


def _default_execute(*_args, **_kwargs):
    return {"answer": "default"}


async def _default_collect(*_args, **_kwargs):
    return SimpleNamespace(payload={"answer": "default"}, events=[])


def test_sync_legacy_compatibility_adapter_uses_modern_flow_when_not_overridden() -> None:
    adapter = LegacySyncOrchestratorCompatibilityAdapter(
        session_factory=SimpleNamespace(build=lambda *_args, **_kwargs: None),
        session_turn_flow_service=SimpleNamespace(complete_payload=lambda **_kwargs: None),
        default_execute_orchestration=_default_execute,
    )

    class _DefaultOrchestrator:
        _execute_llm_orchestration = staticmethod(_default_execute)

    modern_flow_calls: list[str] = []

    result = adapter.run(
        orchestrator=_DefaultOrchestrator(),
        request=ChatAskRequest(question="hi", workspace_id="ws-1"),
        modern_flow=lambda: modern_flow_calls.append("modern") or "modern-result",
    )

    assert result == "modern-result"
    assert modern_flow_calls == ["modern"]


def test_sync_legacy_compatibility_adapter_materializes_completed_flow_from_payload() -> None:
    completed_calls: list[dict[str, object]] = []
    session = SimpleNamespace(
        warnings=["history_trimmed"],
        db_connectors=["warehouse"],
        conversation_id="conv-1",
    )

    class _PatchedOrchestrator:
        @staticmethod
        def _execute_llm_orchestration(**kwargs):
            return {
                "answer": "legacy payload",
                "warnings": ["legacy_warning"],
                "kwargs": kwargs,
            }

    adapter = LegacySyncOrchestratorCompatibilityAdapter(
        session_factory=SimpleNamespace(build=lambda *_args, **_kwargs: session),
        session_turn_flow_service=SimpleNamespace(
            complete_payload=lambda **kwargs: completed_calls.append(kwargs)
            or "completed-flow"
        ),
        default_execute_orchestration=_default_execute,
    )

    result = adapter.run(
        orchestrator=_PatchedOrchestrator(),
        request=ChatAskRequest(question="hi", workspace_id="ws-1"),
        modern_flow=lambda: pytest.fail("modern flow must not run"),
    )

    assert result == "completed-flow"
    assert completed_calls[0]["session"] is session
    assert completed_calls[0]["warnings"] == ["history_trimmed"]
    assert completed_calls[0]["completion_kind"] == "runtime_completed"
    assert completed_calls[0]["llm_payload"]["answer"] == "legacy payload"


@pytest.mark.asyncio
async def test_streaming_legacy_compatibility_adapter_wraps_events_in_typed_artifact() -> None:
    adapter = LegacyStreamingRuntimeCompatibilityAdapter(
        default_collect_tool_loop=_default_collect
    )

    class _PatchedOrchestrator:
        async def _run_tool_loop_and_collect(self, **_kwargs):
            return SimpleNamespace(
                payload={"answer": "legacy payload"},
                events=[ThinkingEvent(content="working")],
            )

    runtime = SimpleNamespace(session="session")

    result = await adapter.execute_runtime(
        orchestrator=_PatchedOrchestrator(),
        runtime=runtime,
        cancel_event=None,
        emit=None,
        modern_runtime=lambda: pytest.fail("modern runtime must not run"),
    )

    assert result.llm_payload["answer"] == "legacy payload"
    assert result.transport_artifact.artifact_type == "streaming_events"
    assert len(result.transport_artifact.events) == 1
    assert isinstance(result.transport_artifact.events[0], ThinkingEvent)
    assert result.transport_artifact.events[0].content == "working"
