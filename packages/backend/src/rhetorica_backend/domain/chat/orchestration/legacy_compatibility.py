"""Explicit legacy compatibility seams for sync and streaming orchestrators."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from rhetorica_backend.contracts.chat import ChatAskRequest, StreamEvent
from rhetorica_backend.domain.chat.orchestration.models import (
    PreparedRuntimeTurnInput,
    RuntimeExecutionResult,
    StreamingRuntimeTransportArtifact,
)
from rhetorica_backend.domain.chat.orchestration.session import (
    OrchestrationSessionFactory,
)
from rhetorica_backend.domain.chat.orchestration.session_turn_flow import (
    OrchestrationSessionTurnFlowService,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


class LegacyCollectedToolLoopResult(Protocol):
    """Legacy runtime result shape expected by streaming monkeypatch shims."""

    payload: dict[str, Any]
    events: list[StreamEvent]


class LegacySyncOrchestratorCompatibilityAdapter:
    """Preserves monkeypatch-based sync route tests behind an explicit adapter seam."""

    def __init__(
        self,
        *,
        session_factory: OrchestrationSessionFactory,
        session_turn_flow_service: OrchestrationSessionTurnFlowService[
            ToolOrchestrationState, object
        ],
        default_execute_orchestration: Callable[..., dict[str, Any]],
    ) -> None:
        self._session_factory = session_factory
        self._session_turn_flow_service = session_turn_flow_service
        self._default_execute_orchestration = default_execute_orchestration

    def run(
        self,
        *,
        orchestrator: Any,
        request: ChatAskRequest,
        modern_flow: Callable[[], Any],
    ) -> Any:
        if (
            type(orchestrator)._execute_llm_orchestration
            is self._default_execute_orchestration
        ):
            return modern_flow()

        session = self._session_factory.build(request)
        warnings = list(session.warnings)
        llm_payload = orchestrator._execute_llm_orchestration(
            request=request,
            db_connectors=session.db_connectors,
            conversation_id=session.conversation_id,
            warnings=warnings,
        )
        return self._session_turn_flow_service.complete_payload(
            session=session,
            llm_payload=llm_payload,
            warnings=warnings,
            completion_kind="runtime_completed",
        )


class LegacyStreamingRuntimeCompatibilityAdapter:
    """Preserves monkeypatch-based streaming route tests behind an explicit adapter seam."""

    def __init__(
        self,
        *,
        default_collect_tool_loop: Callable[..., Awaitable[Any]],
    ) -> None:
        self._default_collect_tool_loop = default_collect_tool_loop

    async def execute_runtime(
        self,
        *,
        orchestrator: Any,
        runtime: PreparedRuntimeTurnInput[object],
        cancel_event: Any,
        emit: Callable[[StreamEvent], None] | None,
        modern_runtime: Callable[[], LegacyCollectedToolLoopResult],
    ) -> RuntimeExecutionResult:
        if (
            type(orchestrator)._run_tool_loop_and_collect
            is self._default_collect_tool_loop
        ):
            runtime_result = modern_runtime()
        else:
            runtime_result = await orchestrator._run_tool_loop_and_collect(
                session=runtime.session,
                cancel_event=cancel_event,
                on_event=emit,
            )

        return RuntimeExecutionResult(
            llm_payload=runtime_result.payload,
            transport_artifact=StreamingRuntimeTransportArtifact(
                events=list(runtime_result.events)
            ),
        )
