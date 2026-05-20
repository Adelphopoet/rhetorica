"""Shared execution core for sync and streaming orchestration transports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.models import (
    PreparedRuntimeTurnInput,
    RuntimeExecutionResult,
)
from rhetorica_backend.domain.chat.orchestration.runtime_loop_coordination import (
    RuntimeLoopCoordinationService,
    RuntimeLoopInput,
    RuntimeLoopTransportAdapter,
)
from rhetorica_backend.domain.chat.orchestration.session_turn_flow import (
    OrchestrationSessionTurnFlowService,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.transport_finalization import (
    PostAssemblyTransportFinalizationService,
    SessionFlowTransportFinalizationInputFactory,
)

TState = TypeVar("TState", bound=ToolOrchestrationState)
TRuntimeResult = TypeVar("TRuntimeResult")
TTransportResult = TypeVar("TTransportResult")


class RuntimeExecutionCore(Generic[TState, TRuntimeResult, TTransportResult]):
    """Runs the shared session flow, loop coordination, and transport finalization."""

    def __init__(
        self,
        *,
        session_turn_flow_service: OrchestrationSessionTurnFlowService[TState, Any],
        runtime_loop_coordination_service: RuntimeLoopCoordinationService[
            TState, TRuntimeResult
        ],
        transport_finalization_service: PostAssemblyTransportFinalizationService[
            TTransportResult
        ],
        transport_finalization_input_factory: SessionFlowTransportFinalizationInputFactory,
    ) -> None:
        self._session_turn_flow_service = session_turn_flow_service
        self._runtime_loop_coordination_service = runtime_loop_coordination_service
        self._transport_finalization_service = transport_finalization_service
        self._transport_finalization_input_factory = transport_finalization_input_factory

    def run_loop(
        self,
        *,
        runtime: RuntimeLoopInput[TState],
        transport: RuntimeLoopTransportAdapter[TState, TRuntimeResult],
    ) -> TRuntimeResult:
        return self._runtime_loop_coordination_service.run(
            runtime=runtime,
            transport=transport,
        )

    def execute_session(
        self,
        *,
        request: ChatAskRequest,
        create_conversation_without_llm: bool,
        state_factory: Callable[[], TState],
        execute_runtime: Callable[
            [PreparedRuntimeTurnInput[TState]],
            RuntimeExecutionResult,
        ],
    ) -> TTransportResult:
        flow = self._session_turn_flow_service.run(
            request=request,
            create_conversation_without_llm=create_conversation_without_llm,
            state_factory=state_factory,
            execute_runtime=execute_runtime,
        )
        return self._transport_finalization_service.finalize(
            finalization_input=self._transport_finalization_input_factory.build(
                request=request,
                flow=flow,
            )
        )

    async def execute_session_async(
        self,
        *,
        request: ChatAskRequest,
        create_conversation_without_llm: bool,
        state_factory: Callable[[], TState],
        execute_runtime: Callable[
            [PreparedRuntimeTurnInput[TState]],
            RuntimeExecutionResult | Awaitable[RuntimeExecutionResult],
        ],
    ) -> TTransportResult:
        flow = await self._session_turn_flow_service.run_async(
            request=request,
            create_conversation_without_llm=create_conversation_without_llm,
            state_factory=state_factory,
            execute_runtime=execute_runtime,
        )
        return self._transport_finalization_service.finalize(
            finalization_input=self._transport_finalization_input_factory.build(
                request=request,
                flow=flow,
            )
        )
