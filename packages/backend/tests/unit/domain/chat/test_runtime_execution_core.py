from __future__ import annotations

from types import SimpleNamespace

import pytest
from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.runtime_execution_core import (
    RuntimeExecutionCore,
)


def test_runtime_execution_core_run_loop_delegates_to_coordination_service() -> None:
    runtime = object()
    transport = object()
    coordination_calls: list[tuple[object, object]] = []
    core = RuntimeExecutionCore(
        session_turn_flow_service=SimpleNamespace(),
        runtime_loop_coordination_service=SimpleNamespace(
            run=lambda **kwargs: coordination_calls.append(
                (kwargs["runtime"], kwargs["transport"])
            )
            or "loop-result"
        ),
        transport_finalization_service=SimpleNamespace(),
        transport_finalization_input_factory=SimpleNamespace(),
    )

    result = core.run_loop(runtime=runtime, transport=transport)

    assert result == "loop-result"
    assert coordination_calls == [(runtime, transport)]


def test_runtime_execution_core_execute_session_finalizes_flow() -> None:
    request = ChatAskRequest(question="hi", workspace_id="ws-1")
    session_calls: list[dict[str, object]] = []
    finalize_inputs: list[object] = []
    built_inputs: list[tuple[ChatAskRequest, object]] = []
    core = RuntimeExecutionCore(
        session_turn_flow_service=SimpleNamespace(
            run=lambda **kwargs: session_calls.append(kwargs) or "flow"
        ),
        runtime_loop_coordination_service=SimpleNamespace(),
        transport_finalization_service=SimpleNamespace(
            finalize=lambda **kwargs: finalize_inputs.append(
                kwargs["finalization_input"]
            )
            or "finalized"
        ),
        transport_finalization_input_factory=SimpleNamespace(
            build=lambda **kwargs: built_inputs.append((kwargs["request"], kwargs["flow"]))
            or "finalization-input"
        ),
    )

    result = core.execute_session(
        request=request,
        create_conversation_without_llm=True,
        state_factory=SimpleNamespace,
        execute_runtime=lambda _runtime: pytest.fail("runtime should not execute in stub"),
    )

    assert result == "finalized"
    assert session_calls[0]["request"] == request
    assert built_inputs == [(request, "flow")]
    assert finalize_inputs == ["finalization-input"]


@pytest.mark.asyncio
async def test_runtime_execution_core_execute_session_async_finalizes_flow() -> None:
    request = ChatAskRequest(question="hi", workspace_id="ws-1")
    built_inputs: list[tuple[ChatAskRequest, object]] = []
    core = RuntimeExecutionCore(
        session_turn_flow_service=SimpleNamespace(
            run_async=lambda **_kwargs: _async_value("flow-async")
        ),
        runtime_loop_coordination_service=SimpleNamespace(),
        transport_finalization_service=SimpleNamespace(
            finalize=lambda **kwargs: f"final:{kwargs['finalization_input']}"
        ),
        transport_finalization_input_factory=SimpleNamespace(
            build=lambda **kwargs: built_inputs.append((kwargs["request"], kwargs["flow"]))
            or "async-input"
        ),
    )

    result = await core.execute_session_async(
        request=request,
        create_conversation_without_llm=False,
        state_factory=SimpleNamespace,
        execute_runtime=lambda _runtime: pytest.fail("runtime should not execute in stub"),
    )

    assert result == "final:async-input"
    assert built_inputs == [(request, "flow-async")]


async def _async_value(value: object) -> object:
    return value
