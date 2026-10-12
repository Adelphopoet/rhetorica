"""Shared session and turn orchestration flow for sync and streaming shells."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.llm.prompt_builder import PromptBuilder
from rhetorica_backend.domain.chat.orchestration.capability_registry import (
    capability_trace_from_runtime,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    CompletedSessionFlow,
    NoRuntimeTransportArtifact,
    PreparedRuntimeTurnInput,
    RuntimeCompletionKind,
    RuntimeExecutionResult,
    RuntimeTransportArtifact,
)
from rhetorica_backend.domain.chat.orchestration.response_assembly import (
    ChatResponseAssembler,
)
from rhetorica_backend.domain.chat.orchestration.session import (
    OrchestrationSession,
    OrchestrationSessionFactory,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.transport_finalization import (
    HistoryPersistencePolicy,
)
from rhetorica_backend.domain.chat.orchestration.turn_builder import TurnBuilder

TState = TypeVar("TState", bound=ToolOrchestrationState)
TTransportArtifact = TypeVar("TTransportArtifact")


class OrchestrationSessionTurnFlowService(Generic[TState, TTransportArtifact]):
    """Runs the shared session bootstrap, turn preparation, and post-loop flow."""

    def __init__(
        self,
        *,
        session_factory: OrchestrationSessionFactory,
        turn_builder: TurnBuilder,
        response_assembler: ChatResponseAssembler,
        create_llm_client: Callable[[Any], Any],
        requires_metadata_lookup: Callable[[ChatAskRequest], bool],
        postprocess_llm_payload: Callable[..., dict[str, Any]] | None = None,
        history_persistence_policy: HistoryPersistencePolicy | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._turn_builder = turn_builder
        self._response_assembler = response_assembler
        self._create_llm_client = create_llm_client
        self._requires_metadata_lookup = requires_metadata_lookup
        self._postprocess_llm_payload = postprocess_llm_payload
        self._history_persistence_policy = history_persistence_policy or HistoryPersistencePolicy()

    def run(
        self,
        *,
        request: ChatAskRequest,
        create_conversation_without_llm: bool,
        state_factory: Callable[[], TState],
        execute_runtime: Callable[
            [PreparedRuntimeTurnInput[TState]],
            RuntimeExecutionResult,
        ],
    ) -> CompletedSessionFlow:
        session = self._session_factory.build(
            request,
            create_conversation_without_llm=create_conversation_without_llm,
        )
        warnings = list(session.warnings)

        if session.llm_connection is None:
            self._extend_unique(warnings, ["llm_connector_missing"])
            persisted_user_message_id = self._persist_user_message(session=session)
            return self.complete_payload(
                session=session,
                llm_payload=self._llm_connector_missing_payload(
                    session=session,
                    warnings=warnings,
                ),
                warnings=warnings,
                completion_kind="llm_connector_missing",
                persisted_user_message_id=persisted_user_message_id,
            )

        prepared_turn = self._turn_builder.prepare_turn(
            request=self._resolved_runtime_request(session),
            db_connectors=session.db_connectors,
            conversation_state=session.conversation_state,
            conversation_id=session.conversation_id,
            initial_warnings=warnings,
            requires_metadata_lookup=self._requires_metadata_lookup(request),
            workspace_snapshot=session.workspace_snapshot,
            state_factory=state_factory,
        )
        persisted_user_message_id = self._persist_user_message(session=session)
        runtime_input = PreparedRuntimeTurnInput(
            session=session,
            request=self._resolved_runtime_request(session),
            db_connectors=session.db_connectors,
            messages=prepared_turn.messages,
            tools=prepared_turn.tools,
            state=prepared_turn.state,
            tool_executor=prepared_turn.tool_executor,
            llm_client=self._create_llm_client(session.llm_connection),
            agent_session=session.agent_session,
        )
        runtime_result = execute_runtime(runtime_input)
        completed = self.complete_payload(
            session=session,
            llm_payload=runtime_result.llm_payload,
            warnings=warnings,
            completion_kind="runtime_completed",
            transport_artifact=runtime_result.transport_artifact,
            llm_usages=prepared_turn.state.llm_usages,
            persisted_user_message_id=persisted_user_message_id,
        )
        if "cancelled" in completed.turn.warnings:
            return completed.model_copy(update={"completion_kind": "cancelled"})
        return completed

    async def run_async(
        self,
        *,
        request: ChatAskRequest,
        create_conversation_without_llm: bool,
        state_factory: Callable[[], TState],
        execute_runtime: Callable[
            [PreparedRuntimeTurnInput[TState]],
            RuntimeExecutionResult | Awaitable[RuntimeExecutionResult],
        ],
    ) -> CompletedSessionFlow:
        session = self._session_factory.build(
            request,
            create_conversation_without_llm=create_conversation_without_llm,
        )
        warnings = list(session.warnings)

        if session.llm_connection is None:
            self._extend_unique(warnings, ["llm_connector_missing"])
            persisted_user_message_id = self._persist_user_message(session=session)
            return self.complete_payload(
                session=session,
                llm_payload=self._llm_connector_missing_payload(
                    session=session,
                    warnings=warnings,
                ),
                warnings=warnings,
                completion_kind="llm_connector_missing",
                persisted_user_message_id=persisted_user_message_id,
            )

        prepared_turn = self._turn_builder.prepare_turn(
            request=self._resolved_runtime_request(session),
            db_connectors=session.db_connectors,
            conversation_state=session.conversation_state,
            conversation_id=session.conversation_id,
            initial_warnings=warnings,
            requires_metadata_lookup=self._requires_metadata_lookup(request),
            workspace_snapshot=session.workspace_snapshot,
            state_factory=state_factory,
        )
        persisted_user_message_id = self._persist_user_message(session=session)
        runtime_input = PreparedRuntimeTurnInput(
            session=session,
            request=self._resolved_runtime_request(session),
            db_connectors=session.db_connectors,
            messages=prepared_turn.messages,
            tools=prepared_turn.tools,
            state=prepared_turn.state,
            tool_executor=prepared_turn.tool_executor,
            llm_client=self._create_llm_client(session.llm_connection),
            agent_session=session.agent_session,
        )
        runtime_result = execute_runtime(runtime_input)
        if inspect.isawaitable(runtime_result):
            runtime_result = await runtime_result
        completed = self.complete_payload(
            session=session,
            llm_payload=runtime_result.llm_payload,
            warnings=warnings,
            completion_kind="runtime_completed",
            transport_artifact=runtime_result.transport_artifact,
            llm_usages=prepared_turn.state.llm_usages,
            persisted_user_message_id=persisted_user_message_id,
        )
        if "cancelled" in completed.turn.warnings:
            return completed.model_copy(update={"completion_kind": "cancelled"})
        return completed

    @staticmethod
    def _resolved_runtime_request(session: OrchestrationSession) -> ChatAskRequest:
        connector_name = session.resolved_llm_connector_name or session.request.llm_connector_name
        if connector_name == session.request.llm_connector_name:
            return session.request
        return session.request.model_copy(
            update={"llm_connector_name": connector_name},
        )

    def complete_payload(
        self,
        *,
        session: OrchestrationSession,
        llm_payload: dict[str, Any],
        warnings: list[str],
        completion_kind: RuntimeCompletionKind,
        transport_artifact: RuntimeTransportArtifact | None = None,
        llm_usages: list[Any] | None = None,
        persisted_user_message_id: str | None = None,
    ) -> CompletedSessionFlow:
        resolved_transport_artifact = transport_artifact or NoRuntimeTransportArtifact()
        merged_warnings = list(warnings)
        payload = dict(llm_payload)
        self._extend_unique(
            merged_warnings,
            self._payload_warnings(payload),
        )
        payload = self._postprocess_payload(
            request=session.request,
            llm_payload=payload,
            db_connectors=session.db_connectors,
            warnings=merged_warnings,
        )
        self._extend_unique(
            merged_warnings,
            self._payload_warnings(payload),
        )
        selected_connector_name = ChatResponseAssembler.select_connector_name(
            request=session.request,
            llm_payload=payload,
            db_connectors=session.db_connectors,
        )
        turn = self._response_assembler.assemble_turn(
            llm_payload=payload,
            warnings=merged_warnings,
            capability_surface=session.capability_surface,
            selected_connector_name=selected_connector_name,
            llm_usages=llm_usages,
        )
        return CompletedSessionFlow(
            completion_kind=completion_kind,
            session=session,
            turn=turn,
            llm_usages=ChatResponseAssembler._serialize_llm_usages(llm_usages),
            transport_artifact=resolved_transport_artifact,
            history_persistence=self._history_persistence_policy.decide(
                conversation_id=session.conversation_id,
                warnings=turn.warnings,
            ),
            persisted_user_message_id=persisted_user_message_id,
        )

    def _persist_user_message(self, *, session: OrchestrationSession) -> str | None:
        if session.conversation_id is None:
            return None
        save_user_to_history = getattr(
            self._response_assembler,
            "save_user_to_history",
            None,
        )
        if not callable(save_user_to_history):
            return None
        return save_user_to_history(
            conversation_id=session.conversation_id,
            request=session.request,
        )

    def _llm_connector_missing_payload(
        self,
        *,
        session: OrchestrationSession,
        warnings: list[str],
    ) -> dict[str, Any]:
        capability_surface = session.capability_surface
        return {
            "answer": "LLM connector is not configured. Add it in admin.",
            "sql": None,
            "target": None,
            "capability_trace": capability_trace_from_runtime(
                available_tools=capability_surface.available_tools,
                used_tools=[],
                used_providers=[],
                metadata_sources=[],
                warnings=warnings,
                has_execution=False,
                degraded_capabilities=capability_surface.degraded_capabilities,
                policy_decisions=capability_surface.policy_decisions,
            ).model_dump(mode="json"),
            "prompt_version": PromptBuilder.get_prompt_version(),
            "warnings": list(warnings),
        }

    def _postprocess_payload(
        self,
        *,
        request: ChatAskRequest,
        llm_payload: dict[str, Any],
        db_connectors: list[ConnectorItem],
        warnings: list[str],
    ) -> dict[str, Any]:
        if self._postprocess_llm_payload is None:
            return llm_payload
        return self._postprocess_llm_payload(
            request=request,
            llm_payload=llm_payload,
            db_connectors=db_connectors,
            warnings=warnings,
        )

    @staticmethod
    def _payload_warnings(llm_payload: dict[str, Any]) -> list[str]:
        payload_warnings = llm_payload.get("warnings")
        if not isinstance(payload_warnings, list):
            return []
        return [str(item) for item in payload_warnings if isinstance(item, str)]

    @staticmethod
    def _extend_unique(target: list[str], values: list[str]) -> None:
        for value in values:
            if value not in target:
                target.append(value)
