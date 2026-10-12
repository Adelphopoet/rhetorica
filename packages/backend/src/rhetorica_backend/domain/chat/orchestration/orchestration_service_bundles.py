"""Shared builders for sync and streaming Agents SDK orchestration services."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from rhetorica_backend.domain.chat.orchestration import shared_kernel
from rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime import (
    AgentsSdkTurnRuntime,
)
from rhetorica_backend.domain.chat.orchestration.finalization_support_services import (
    FinalPayloadBuilderService,
)
from rhetorica_backend.domain.chat.orchestration.runtime_execution_core import (
    RuntimeExecutionCore,
)
from rhetorica_backend.domain.chat.orchestration.session_turn_flow import (
    OrchestrationSessionTurnFlowService,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.tool_execution_policy import (
    ToolExecutionPolicy,
)
from rhetorica_backend.domain.chat.orchestration.transport_finalization import (
    PostAssemblyTransportFinalizationService,
    SessionFlowTransportFinalizationInputFactory,
    StreamingChatTransportAdapter,
    SyncChatTransportAdapter,
)

TState = TypeVar("TState", bound=ToolOrchestrationState)
TRuntimeResult = TypeVar("TRuntimeResult")
TTransportResult = TypeVar("TTransportResult")


@dataclass(slots=True)
class OrchestrationServiceBundle(Generic[TState, TRuntimeResult, TTransportResult]):
    """Collaborators needed by a transport orchestrator shell."""

    tool_execution_policy: ToolExecutionPolicy
    session_turn_flow_service: OrchestrationSessionTurnFlowService[TState, Any]
    transport_finalization_service: PostAssemblyTransportFinalizationService[TTransportResult]
    transport_finalization_input_factory: SessionFlowTransportFinalizationInputFactory
    execution_core: RuntimeExecutionCore[TState, TRuntimeResult, TTransportResult]


def build_tool_execution_policy() -> ToolExecutionPolicy:
    return ToolExecutionPolicy()


def _build_runtime(
    *,
    logger: logging.Logger,
    final_payload_builder: FinalPayloadBuilderService,
    tool_execution_policy: ToolExecutionPolicy,
) -> AgentsSdkTurnRuntime[Any, Any]:
    return AgentsSdkTurnRuntime(
        logger=logger,
        emit_diagnostic_event=shared_kernel.emit_diagnostic_event,
        tool_execution_policy=tool_execution_policy,
        build_final_payload_from_parsed=(final_payload_builder.build_final_payload_from_parsed),
        llm_request_failed_payload=shared_kernel.llm_request_failed_payload,
    )


def build_sync_orchestration_service_bundle(
    *,
    logger: logging.Logger,
    final_payload_builder: FinalPayloadBuilderService,
    session_factory: Any,
    turn_builder: Any,
    response_assembler: Any,
    create_llm_client: Any,
    history_persistence_policy: Any,
    history_persistence_service: Any,
    transport_finalization_input_factory: SessionFlowTransportFinalizationInputFactory,
    postprocess_llm_payload: Any,
) -> OrchestrationServiceBundle[ToolOrchestrationState, dict[str, Any], Any]:
    tool_execution_policy = build_tool_execution_policy()
    session_turn_flow_service = OrchestrationSessionTurnFlowService[
        ToolOrchestrationState,
        None,
    ](
        session_factory=session_factory,
        turn_builder=turn_builder,
        response_assembler=response_assembler,
        create_llm_client=create_llm_client,
        requires_metadata_lookup=shared_kernel.requires_metadata_lookup,
        postprocess_llm_payload=postprocess_llm_payload,
        history_persistence_policy=history_persistence_policy,
    )
    transport_finalization_service = PostAssemblyTransportFinalizationService(
        history_persistence_service=history_persistence_service,
        adapter=SyncChatTransportAdapter(),
    )
    execution_core = RuntimeExecutionCore[
        ToolOrchestrationState,
        dict[str, Any],
        Any,
    ](
        session_turn_flow_service=session_turn_flow_service,
        agent_runtime=_build_runtime(
            logger=logger,
            final_payload_builder=final_payload_builder,
            tool_execution_policy=tool_execution_policy,
        ),
        transport_finalization_service=transport_finalization_service,
        transport_finalization_input_factory=transport_finalization_input_factory,
    )
    return OrchestrationServiceBundle(
        tool_execution_policy=tool_execution_policy,
        session_turn_flow_service=session_turn_flow_service,
        transport_finalization_service=transport_finalization_service,
        transport_finalization_input_factory=transport_finalization_input_factory,
        execution_core=execution_core,
    )


def build_streaming_orchestration_service_bundle(
    *,
    logger: logging.Logger,
    final_payload_builder: FinalPayloadBuilderService,
    session_factory: Any,
    turn_builder: Any,
    response_assembler: Any,
    create_llm_client: Any,
    history_persistence_policy: Any,
    history_persistence_service: Any,
    transport_finalization_input_factory: SessionFlowTransportFinalizationInputFactory,
) -> OrchestrationServiceBundle[TState, Any, Any]:
    tool_execution_policy = build_tool_execution_policy()
    session_turn_flow_service = OrchestrationSessionTurnFlowService[TState, Any](
        session_factory=session_factory,
        turn_builder=turn_builder,
        response_assembler=response_assembler,
        create_llm_client=create_llm_client,
        requires_metadata_lookup=shared_kernel.requires_metadata_lookup,
        history_persistence_policy=history_persistence_policy,
    )
    transport_finalization_service = PostAssemblyTransportFinalizationService[Any](
        history_persistence_service=history_persistence_service,
        adapter=StreamingChatTransportAdapter(),
    )
    execution_core = RuntimeExecutionCore[TState, Any, Any](
        session_turn_flow_service=session_turn_flow_service,
        agent_runtime=_build_runtime(
            logger=logger,
            final_payload_builder=final_payload_builder,
            tool_execution_policy=tool_execution_policy,
        ),
        transport_finalization_service=transport_finalization_service,
        transport_finalization_input_factory=transport_finalization_input_factory,
    )
    return OrchestrationServiceBundle(
        tool_execution_policy=tool_execution_policy,
        session_turn_flow_service=session_turn_flow_service,
        transport_finalization_service=transport_finalization_service,
        transport_finalization_input_factory=transport_finalization_input_factory,
        execution_core=execution_core,
    )
