"""Shared builders for sync and streaming orchestration service bundles."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from rhetorica_backend.domain.chat.orchestration import shared_kernel
from rhetorica_backend.domain.chat.orchestration.finalization_support_services import (
    FinalPayloadBuilderService,
    RecoveryFinalizationService,
)
from rhetorica_backend.domain.chat.orchestration.llm_finalization_policy import (
    LlmFinalizationPolicy,
)
from rhetorica_backend.domain.chat.orchestration.runtime_execution_core import (
    RuntimeExecutionCore,
)
from rhetorica_backend.domain.chat.orchestration.runtime_loop_coordination import (
    RuntimeLoopCoordinationService,
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
from rhetorica_backend.domain.chat.orchestration.turn_completion_policy import (
    TurnCompletionPolicy,
)
from rhetorica_backend.domain.chat.orchestration.turn_decision_policy import (
    TurnDecisionPolicy,
)

TState = TypeVar("TState", bound=ToolOrchestrationState)
TRuntimeResult = TypeVar("TRuntimeResult")
TTransportResult = TypeVar("TTransportResult")


@dataclass(slots=True)
class OrchestrationServiceBundle(Generic[TState, TRuntimeResult, TTransportResult]):
    """Assembled collaborators needed by a transport orchestrator shell."""

    llm_finalization_policy: LlmFinalizationPolicy
    tool_execution_policy: ToolExecutionPolicy
    session_turn_flow_service: OrchestrationSessionTurnFlowService[TState, Any]
    transport_finalization_service: PostAssemblyTransportFinalizationService[
        TTransportResult
    ]
    transport_finalization_input_factory: SessionFlowTransportFinalizationInputFactory
    execution_core: RuntimeExecutionCore[TState, TRuntimeResult, TTransportResult]


def build_llm_finalization_policy(
    *,
    logger: logging.Logger,
    final_payload_builder: FinalPayloadBuilderService,
) -> LlmFinalizationPolicy:
    return LlmFinalizationPolicy(
        logger=logger,
        build_finalizer_context=shared_kernel.build_finalizer_context,
        build_final_payload_from_parsed=(
            final_payload_builder.build_final_payload_from_parsed
        ),
        fallback_final_payload=final_payload_builder.fallback_final_payload,
        deterministic_finalizer_answer=shared_kernel.deterministic_finalizer_answer,
        execution_recovery_prompt=shared_kernel.execution_recovery_prompt,
        extract_message_content=shared_kernel.extract_message_content,
        has_needs_user_clarification_flag=(
            shared_kernel.has_needs_user_clarification_flag
        ),
        is_acceptable_final_payload=shared_kernel.is_acceptable_final_payload,
        merge_uncertainty_flags=shared_kernel.merge_uncertainty_flags,
        preserve_raw_markdown_answer=shared_kernel.preserve_raw_markdown_answer,
        extract_uncertainty_flags=shared_kernel.extract_uncertainty_flags,
        successful_execution_attempts=shared_kernel.successful_execution_attempts,
        looks_like_nonfinal_raw_payload=shared_kernel.looks_like_nonfinal_raw_payload,
        to_optional_str=shared_kernel.to_optional_str,
        strip_thinking_tags=shared_kernel.strip_thinking_tags,
    )


def build_tool_execution_policy() -> ToolExecutionPolicy:
    return ToolExecutionPolicy(
        latest_successful_execution_attempt=shared_kernel.latest_successful_execution_attempt,
        runtime_context_payload_from_state=shared_kernel.runtime_context_payload_from_state,
        build_capability_trace_from_state=shared_kernel.build_capability_trace_from_state,
        successful_execution_attempts=shared_kernel.successful_execution_attempts,
    )


def build_sync_orchestration_service_bundle(
    *,
    logger: logging.Logger,
    final_payload_builder: FinalPayloadBuilderService,
    recovery_finalization_service: RecoveryFinalizationService,
    session_factory: Any,
    turn_builder: Any,
    response_assembler: Any,
    create_llm_client: Any,
    history_persistence_policy: Any,
    history_persistence_service: Any,
    transport_finalization_input_factory: SessionFlowTransportFinalizationInputFactory,
    postprocess_llm_payload: Any,
    incomplete_tool_orchestration_payload: Any,
) -> OrchestrationServiceBundle[ToolOrchestrationState, dict[str, Any], Any]:
    llm_finalization_policy = build_llm_finalization_policy(
        logger=logger,
        final_payload_builder=final_payload_builder,
    )
    turn_decision_policy = TurnDecisionPolicy(
        logger=logger,
        ensure_dynamic_tools=shared_kernel.ensure_dynamic_tools,
        assistant_message_for_history=shared_kernel.assistant_message_for_history,
        synthetic_assistant_tool_message=shared_kernel.synthetic_assistant_tool_message,
        protocol_repair_message=shared_kernel.protocol_repair_message,
        forced_tool_repair_message=shared_kernel.forced_tool_repair_message,
        extract_message_content=shared_kernel.extract_message_content,
        extract_tool_name=shared_kernel.extract_tool_name,
        extract_tool_arguments=shared_kernel.extract_tool_arguments,
        emit_diagnostic_event=shared_kernel.emit_diagnostic_event,
        decorate_final_answer=lambda *, answer, state: answer,
    )
    turn_completion_policy = TurnCompletionPolicy(
        build_final_payload_from_parsed=(
            final_payload_builder.build_final_payload_from_parsed
        ),
        fallback_final_payload=final_payload_builder.fallback_final_payload,
        finalize_raw_answer=llm_finalization_policy.finalize_raw_answer,
        has_needs_user_clarification_flag=shared_kernel.has_needs_user_clarification_flag,
        incomplete_tool_orchestration_payload=incomplete_tool_orchestration_payload,
        is_acceptable_final_payload=shared_kernel.is_acceptable_final_payload,
        strip_thinking_tags=shared_kernel.strip_thinking_tags,
        to_optional_str=shared_kernel.to_optional_str,
        validate_or_repair_final_payload=(
            llm_finalization_policy.validate_or_repair_final_payload
        ),
    )
    tool_execution_policy = build_tool_execution_policy()
    runtime_loop_coordination_service = RuntimeLoopCoordinationService[
        ToolOrchestrationState,
        dict[str, Any],
    ](
        logger=logger,
        emit_diagnostic_event=shared_kernel.emit_diagnostic_event,
        ensure_dynamic_tools=shared_kernel.ensure_dynamic_tools,
        assistant_message_for_history=shared_kernel.assistant_message_for_history,
        extract_message_content=shared_kernel.extract_message_content,
        extract_tool_name=shared_kernel.extract_tool_name,
        extract_tool_arguments=shared_kernel.extract_tool_arguments,
        tool_names=shared_kernel.tool_names,
        decision_policy=turn_decision_policy,
        completion_policy=turn_completion_policy,
        tool_execution_policy=tool_execution_policy,
        direct_plain_text_finalizer=final_payload_builder.finalize_direct_plain_text,
        llm_request_failed_payload=shared_kernel.llm_request_failed_payload,
        attempt_duplicate_loop_recovery_finalization=(
            recovery_finalization_service.attempt_duplicate_loop_recovery_finalization
        ),
        attempt_execution_recovery_finalization=(
            recovery_finalization_service.attempt_execution_recovery_finalization
        ),
        successful_execution_attempts=shared_kernel.successful_execution_attempts,
    )
    session_turn_flow_service = OrchestrationSessionTurnFlowService[
        ToolOrchestrationState,
        None,
    ](
        session_factory=session_factory,
        turn_builder=turn_builder,
        response_assembler=response_assembler,
        create_llm_client=create_llm_client,
        is_capabilities_question=shared_kernel.is_capabilities_question,
        build_capabilities_answer=shared_kernel.build_capabilities_answer,
        requires_metadata_lookup=shared_kernel.requires_metadata_lookup,
        postprocess_llm_payload=postprocess_llm_payload,
        history_persistence_policy=history_persistence_policy,
        checkpoint_persistence_service=history_persistence_service,
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
        runtime_loop_coordination_service=runtime_loop_coordination_service,
        transport_finalization_service=transport_finalization_service,
        transport_finalization_input_factory=transport_finalization_input_factory,
    )
    return OrchestrationServiceBundle(
        llm_finalization_policy=llm_finalization_policy,
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
    recovery_finalization_service: RecoveryFinalizationService,
    session_factory: Any,
    turn_builder: Any,
    response_assembler: Any,
    create_llm_client: Any,
    history_persistence_policy: Any,
    history_persistence_service: Any,
    transport_finalization_input_factory: SessionFlowTransportFinalizationInputFactory,
    incomplete_tool_orchestration_payload: Any,
) -> OrchestrationServiceBundle[TState, Any, Any]:
    llm_finalization_policy = build_llm_finalization_policy(
        logger=logger,
        final_payload_builder=final_payload_builder,
    )
    turn_decision_policy = TurnDecisionPolicy(
        logger=logger,
        ensure_dynamic_tools=shared_kernel.ensure_dynamic_tools,
        assistant_message_for_history=shared_kernel.assistant_message_for_history,
        synthetic_assistant_tool_message=shared_kernel.synthetic_assistant_tool_message,
        protocol_repair_message=shared_kernel.protocol_repair_message,
        forced_tool_repair_message=shared_kernel.forced_tool_repair_message,
        extract_message_content=shared_kernel.extract_message_content,
        extract_tool_name=shared_kernel.extract_tool_name,
        extract_tool_arguments=shared_kernel.extract_tool_arguments,
        emit_diagnostic_event=shared_kernel.emit_diagnostic_event,
        decorate_final_answer=lambda *, answer, state: answer,
    )
    turn_completion_policy = TurnCompletionPolicy(
        build_final_payload_from_parsed=(
            final_payload_builder.build_final_payload_from_parsed
        ),
        fallback_final_payload=final_payload_builder.fallback_final_payload,
        finalize_raw_answer=llm_finalization_policy.finalize_raw_answer,
        has_needs_user_clarification_flag=shared_kernel.has_needs_user_clarification_flag,
        incomplete_tool_orchestration_payload=incomplete_tool_orchestration_payload,
        is_acceptable_final_payload=shared_kernel.is_acceptable_final_payload,
        strip_thinking_tags=shared_kernel.strip_thinking_tags,
        to_optional_str=shared_kernel.to_optional_str,
        validate_or_repair_final_payload=(
            llm_finalization_policy.validate_or_repair_final_payload
        ),
    )
    tool_execution_policy = build_tool_execution_policy()
    runtime_loop_coordination_service = RuntimeLoopCoordinationService[TState, Any](
        logger=logger,
        emit_diagnostic_event=shared_kernel.emit_diagnostic_event,
        ensure_dynamic_tools=shared_kernel.ensure_dynamic_tools,
        assistant_message_for_history=shared_kernel.assistant_message_for_history,
        extract_message_content=shared_kernel.extract_message_content,
        extract_tool_name=shared_kernel.extract_tool_name,
        extract_tool_arguments=shared_kernel.extract_tool_arguments,
        tool_names=shared_kernel.tool_names,
        decision_policy=turn_decision_policy,
        completion_policy=turn_completion_policy,
        tool_execution_policy=tool_execution_policy,
        direct_plain_text_finalizer=final_payload_builder.finalize_direct_plain_text,
        llm_request_failed_payload=shared_kernel.llm_request_failed_payload,
        attempt_duplicate_loop_recovery_finalization=(
            recovery_finalization_service.attempt_duplicate_loop_recovery_finalization
        ),
        attempt_execution_recovery_finalization=(
            recovery_finalization_service.attempt_execution_recovery_finalization
        ),
        successful_execution_attempts=shared_kernel.successful_execution_attempts,
    )
    session_turn_flow_service = OrchestrationSessionTurnFlowService[TState, Any](
        session_factory=session_factory,
        turn_builder=turn_builder,
        response_assembler=response_assembler,
        create_llm_client=create_llm_client,
        is_capabilities_question=shared_kernel.is_capabilities_question,
        build_capabilities_answer=shared_kernel.build_capabilities_answer,
        requires_metadata_lookup=shared_kernel.requires_metadata_lookup,
        history_persistence_policy=history_persistence_policy,
        checkpoint_persistence_service=history_persistence_service,
    )
    transport_finalization_service = PostAssemblyTransportFinalizationService[Any](
        history_persistence_service=history_persistence_service,
        adapter=StreamingChatTransportAdapter(),
    )
    execution_core = RuntimeExecutionCore[TState, Any, Any](
        session_turn_flow_service=session_turn_flow_service,
        runtime_loop_coordination_service=runtime_loop_coordination_service,
        transport_finalization_service=transport_finalization_service,
        transport_finalization_input_factory=transport_finalization_input_factory,
    )
    return OrchestrationServiceBundle(
        llm_finalization_policy=llm_finalization_policy,
        tool_execution_policy=tool_execution_policy,
        session_turn_flow_service=session_turn_flow_service,
        transport_finalization_service=transport_finalization_service,
        transport_finalization_input_factory=transport_finalization_input_factory,
        execution_core=execution_core,
    )
