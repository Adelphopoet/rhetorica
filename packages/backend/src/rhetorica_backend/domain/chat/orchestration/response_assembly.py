"""Shared response assembly for sync and streaming chat orchestration."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace
from typing import Any

from rhetorica_backend.contracts.analysis_document import AnalysisDocument
from rhetorica_backend.contracts.chat import (
    CapabilityTrace,
    ChatAskRequest,
    ChatAskResponse,
    ClarificationRequest,
    DoneEvent,
    OptimizationTrace,
)
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import ExecuteSqlResponse
from rhetorica_backend.contracts.files import FileArtifactRef
from rhetorica_backend.contracts.visualization import VisualizationSpec
from rhetorica_backend.domain.chat.conversation.conversation_manager import (
    ConversationManager,
)
from rhetorica_backend.domain.chat.orchestration.capability_registry import (
    WorkspaceCapabilitySurface,
    capability_trace_from_runtime,
)
from rhetorica_backend.domain.chat.orchestration.final_payload_policy import (
    FinalPayloadPolicy,
)
from rhetorica_backend.domain.results.analysis_document_builder import (
    AnalysisDocumentBuilder,
)
from rhetorica_backend.domain.results.visualization_planner import (
    VisualizationPlanner,
)

logger = logging.getLogger(__name__)


def _nonnegative_int(value: object) -> int:
    """Read persisted review counters without allowing malformed runtime data."""
    return value if isinstance(value, int) and value >= 0 else 0


@dataclass(frozen=True)
class AssembledChatTurn:
    """Normalized final turn payload used by sync and streaming responses."""

    answer: str
    generated_sql: str | None
    selected_database_connector: str | None
    metadata_sources: list[str]
    context_tables: list[str]
    runtime_context: dict[str, Any]
    claims: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    claim_evidence_links: list[dict[str, Any]]
    uncertainty_flags: list[str]
    capability_trace: CapabilityTrace
    optimization_trace: OptimizationTrace | None
    execution: ExecuteSqlResponse | None
    analysis_document: AnalysisDocument | None
    visualization: VisualizationSpec | None
    warnings: list[str] = field(default_factory=list)
    llm_usages: list[dict[str, Any]] = field(default_factory=list)
    clarification_request: ClarificationRequest | None = None
    files: list[FileArtifactRef] = field(default_factory=list)

    def to_chat_response(
        self,
        *,
        conversation_id: str | None,
        assistant_message_id: str | None,
    ) -> ChatAskResponse:
        return ChatAskResponse(
            answer=self.answer,
            conversation_id=conversation_id,
            generated_sql=self.generated_sql,
            selected_database_connector=self.selected_database_connector,
            metadata_sources=self.metadata_sources,
            context_tables=self.context_tables,
            claims=self.claims,
            evidence=self.evidence,
            claim_evidence_links=self.claim_evidence_links,
            uncertainty_flags=self.uncertainty_flags,
            capability_trace=self.capability_trace,
            optimization_trace=self.optimization_trace,
            execution=self.execution,
            analysis_document=self.analysis_document,
            visualization=self.visualization,
            warnings=self.warnings,
            assistant_message_id=assistant_message_id,
            clarification_request=self.clarification_request,
            files=self.files,
        )

    def to_done_event(
        self,
        *,
        conversation_id: str | None,
        assistant_message_id: str | None,
        cancelled: bool,
    ) -> DoneEvent:
        return DoneEvent(
            answer=self.answer,
            conversation_id=conversation_id,
            generated_sql=self.generated_sql,
            execution=self.execution,
            analysis_document=self.analysis_document,
            visualization=self.visualization,
            selected_database_connector=self.selected_database_connector,
            metadata_sources=self.metadata_sources,
            context_tables=self.context_tables,
            claims=self.claims,
            evidence=self.evidence,
            claim_evidence_links=self.claim_evidence_links,
            uncertainty_flags=self.uncertainty_flags,
            capability_trace=self.capability_trace,
            optimization_trace=self.optimization_trace,
            warnings=self.warnings,
            assistant_message_id=assistant_message_id,
            cancelled=cancelled,
            clarification_request=self.clarification_request,
            files=self.files,
        )


class ChatHistoryPersistenceService:
    """Persists assembled chat turns into conversation history."""

    def __init__(self, *, conversation_manager: ConversationManager) -> None:
        self._conversation_manager = conversation_manager

    def save_turn(
        self,
        *,
        conversation_id: str | None,
        request: ChatAskRequest,
        turn: AssembledChatTurn,
        persist_user_message: bool = True,
        persisted_user_message_id: str | None = None,
        journal_events: list[dict[str, object]] | None = None,
    ) -> str | None:
        if not conversation_id:
            return None

        if persist_user_message:
            user_message = self.save_user_message(
                conversation_id=conversation_id,
                request=request,
            )
        else:
            user_message = SimpleNamespace(
                id=persisted_user_message_id,
                conversation_id=conversation_id,
                role="user",
                content=request.question,
            )

        if not persist_user_message and user_message is not None and request.attachment_ids:
            self._conversation_manager.attach_files_to_message(
                artifact_ids=request.attachment_ids,
                workspace_id=request.workspace_id,
                message_id=user_message.id,
                conversation_id=conversation_id,
                relation="input",
            )

        capability_trace_payload = turn.capability_trace.model_dump(
            mode="json",
            by_alias=True,
        )
        runtime_context = dict(turn.runtime_context or {})
        if turn.llm_usages:
            runtime_context["llm_calls"] = turn.llm_usages
        if request.agent_id:
            trace_runtime_context = capability_trace_payload.get("runtime_context")
            if isinstance(trace_runtime_context, dict):
                trace_runtime_context.update(runtime_context)
                runtime_context = trace_runtime_context
            runtime_context["agent"] = {
                "id": request.agent_id,
                "has_prompt": bool(request.agent_prompt),
                "llm_connector_name": request.llm_connector_name,
            }
        if runtime_context:
            capability_trace_payload["runtime_context"] = runtime_context
        assistant_message = self._conversation_manager.save_message(
            conversation_id=conversation_id,
            role="assistant",
            content=turn.answer,
            journal_events=journal_events,
            generated_sql=turn.generated_sql,
            execution_id=(turn.execution.execution_id if turn.execution is not None else None),
            context_tables=turn.context_tables,
            metadata_sources=turn.metadata_sources,
            claims=turn.claims,
            evidence=turn.evidence,
            claim_evidence_links=turn.claim_evidence_links,
            uncertainty_flags=turn.uncertainty_flags,
            capability_trace=capability_trace_payload,
            optimization_trace=(
                turn.optimization_trace.model_dump(mode="json")
                if turn.optimization_trace is not None
                else None
            ),
            selected_connector=turn.selected_database_connector,
            analysis_document=(
                turn.analysis_document.model_dump(mode="json")
                if turn.analysis_document is not None
                else None
            ),
            visualization_spec=(
                turn.visualization.model_dump(mode="json")
                if turn.visualization is not None
                else None
            ),
            warnings=turn.warnings,
            agent_id=request.agent_id,
        )
        if assistant_message is not None and turn.files:
            self._conversation_manager.attach_files_to_message(
                artifact_ids=[item.id for item in turn.files],
                workspace_id=request.workspace_id,
                message_id=assistant_message.id,
                conversation_id=conversation_id,
                relation="output",
            )
        langfuse_trace_id = runtime_context.get("langfuse_trace_id")
        if (
            assistant_message is not None
            and isinstance(langfuse_trace_id, str)
            and langfuse_trace_id
        ):
            self._conversation_manager.save_answer_trace(
                assistant_message_id=assistant_message.id,
                conversation_id=conversation_id,
                trace_id=langfuse_trace_id,
                tool_call_count=_nonnegative_int(runtime_context.get("review_tool_call_count")),
                tool_error_count=_nonnegative_int(runtime_context.get("review_tool_error_count")),
            )
        persisted_messages = [
            message for message in (user_message, assistant_message) if message is not None
        ]
        if persisted_messages and hasattr(
            self._conversation_manager,
            "update_conversation_state_from_turn",
        ):
            self._conversation_manager.update_conversation_state_from_turn(
                conversation_id=conversation_id,
                messages=persisted_messages,
                llm_usages=turn.llm_usages if hasattr(turn, "llm_usages") else None,
            )
        return assistant_message.id if assistant_message else None

    def add_token_usage(
        self,
        *,
        conversation_id: str,
        llm_usages: list[dict[str, Any]],
    ) -> None:
        """Persist billed usage independently from optional conversation history."""
        self._conversation_manager.add_token_usage(
            conversation_id=conversation_id,
            llm_usages=llm_usages,
        )

    def save_user_message(
        self,
        *,
        conversation_id: str | None,
        request: ChatAskRequest,
    ) -> Any | None:
        """Persist the analyst question as soon as the turn is ready to run."""
        if not conversation_id:
            return None
        user_message = self._conversation_manager.save_message(
            conversation_id=conversation_id,
            role="user",
            content=request.question,
            agent_id=request.agent_id,
            source_provider=request.source_provider,
            source_message_id=request.source_message_id,
        )
        if user_message is not None and request.attachment_ids:
            self._conversation_manager.attach_files_to_message(
                artifact_ids=request.attachment_ids,
                workspace_id=request.workspace_id,
                message_id=user_message.id,
                conversation_id=conversation_id,
                relation="input",
            )
        return user_message


class ChatResponseAssembler:
    """Builds final chat responses and persists them into conversation history."""

    def __init__(
        self,
        *,
        conversation_manager: ConversationManager,
        visualization_planner: VisualizationPlanner,
        analysis_document_builder: AnalysisDocumentBuilder,
    ) -> None:
        self._conversation_manager = conversation_manager
        self._visualization_planner = visualization_planner
        self._analysis_document_builder = analysis_document_builder
        self._history_persistence_service = ChatHistoryPersistenceService(
            conversation_manager=conversation_manager
        )

    def assemble_turn(
        self,
        *,
        llm_payload: dict[str, Any],
        warnings: list[str],
        capability_surface: WorkspaceCapabilitySurface,
        selected_connector_name: str | None,
        llm_usages: list[Any] | None = None,
    ) -> AssembledChatTurn:
        turn_warnings = list(warnings)
        answer = self._extract_answer(llm_payload)
        execution = self._extract_execution(llm_payload)
        successful_executions = self._extract_successful_executions(llm_payload)
        if not successful_executions and execution is not None and execution.status == "succeeded":
            successful_executions = [execution]
        if (
            "llm_finalizer_failed" in turn_warnings
            and execution is not None
            and not isinstance(llm_payload.get("analysis_document"), dict)
        ):
            successful_executions = [execution]
        visualization = self._plan_visualization(
            execution=execution,
            llm_payload=llm_payload,
            warnings=turn_warnings,
        )
        analysis_document = self._plan_analysis_document(
            answer=answer,
            execution=execution,
            executions=successful_executions,
            llm_payload=llm_payload,
            visualization=visualization,
            warnings=turn_warnings,
        )
        primary_visualization = (
            AnalysisDocumentBuilder.primary_visualization(analysis_document) or visualization
        )
        metadata_sources = self._extract_metadata_sources(llm_payload)
        context_tables = self._extract_context_tables(llm_payload)
        runtime_context = self._extract_runtime_context(llm_payload)

        return AssembledChatTurn(
            answer=answer,
            generated_sql=self._extract_sql(llm_payload),
            selected_database_connector=selected_connector_name,
            metadata_sources=metadata_sources,
            context_tables=context_tables,
            runtime_context=runtime_context,
            claims=FinalPayloadPolicy.extract_claims(llm_payload),
            evidence=FinalPayloadPolicy.extract_evidence(llm_payload),
            claim_evidence_links=FinalPayloadPolicy.extract_claim_evidence_links(llm_payload),
            uncertainty_flags=FinalPayloadPolicy.extract_uncertainty_flags(llm_payload),
            capability_trace=self._extract_capability_trace(
                llm_payload=llm_payload,
                capability_surface=capability_surface,
                metadata_sources=metadata_sources,
                warnings=turn_warnings,
                execution=execution,
            ),
            optimization_trace=None,
            execution=execution,
            analysis_document=analysis_document,
            visualization=primary_visualization,
            warnings=turn_warnings,
            llm_usages=self._serialize_llm_usages(llm_usages),
            clarification_request=self._extract_clarification_request(llm_payload),
            files=self._extract_files(llm_payload),
        )

    @staticmethod
    def _extract_files(llm_payload: dict[str, Any]) -> list[FileArtifactRef]:
        raw_files = llm_payload.get("files")
        if not isinstance(raw_files, list):
            return []
        result: list[FileArtifactRef] = []
        for item in raw_files:
            try:
                result.append(FileArtifactRef.model_validate(item))
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "chat.invalid_file_reference",
                    extra={"error_type": type(exc).__name__},
                )
                continue
        return result

    def save_to_history(
        self,
        *,
        conversation_id: str | None,
        request: ChatAskRequest,
        turn: AssembledChatTurn,
        persist_user_message: bool = True,
        persisted_user_message_id: str | None = None,
    ) -> str | None:
        return self._history_persistence_service.save_turn(
            conversation_id=conversation_id,
            request=request,
            turn=turn,
            persist_user_message=persist_user_message,
            persisted_user_message_id=persisted_user_message_id,
        )

    def save_user_to_history(
        self,
        *,
        conversation_id: str | None,
        request: ChatAskRequest,
    ) -> str | None:
        message = self._history_persistence_service.save_user_message(
            conversation_id=conversation_id,
            request=request,
        )
        return str(message.id) if message is not None else None

    def _plan_visualization(
        self,
        *,
        execution: ExecuteSqlResponse | None,
        llm_payload: dict[str, Any],
        warnings: list[str],
    ) -> VisualizationSpec | None:
        llm_candidate = llm_payload.get("visualization")
        visualization = self._visualization_planner.plan(
            execution=execution,
            llm_candidate=llm_candidate if isinstance(llm_candidate, dict) else None,
        )
        if visualization is None:
            return None
        for warning in visualization.warnings:
            if warning not in warnings:
                warnings.append(warning)
        return visualization

    def _plan_analysis_document(
        self,
        *,
        answer: str,
        execution: ExecuteSqlResponse | None,
        executions: list[ExecuteSqlResponse],
        llm_payload: dict[str, Any],
        visualization: VisualizationSpec | None,
        warnings: list[str],
    ) -> AnalysisDocument | None:
        candidate = llm_payload.get("analysis_document")
        if not isinstance(candidate, dict):
            return None
        document = self._analysis_document_builder.build(
            answer=answer,
            execution=execution,
            executions=executions,
            llm_candidate=candidate,
            fallback_visualization=None,
            warnings=warnings,
        )
        if document is None:
            return None
        for warning in document.warnings:
            if warning not in warnings:
                warnings.append(warning)
        return document

    @staticmethod
    def _extract_capability_trace(
        *,
        llm_payload: dict[str, Any],
        capability_surface: WorkspaceCapabilitySurface,
        metadata_sources: list[str],
        warnings: list[str],
        execution: ExecuteSqlResponse | None,
    ) -> CapabilityTrace:
        trace_payload = llm_payload.get("capability_trace")
        if isinstance(trace_payload, dict):
            try:
                return CapabilityTrace.model_validate(trace_payload)
            except Exception:
                logger.debug("Failed to parse capability_trace payload")
        return capability_trace_from_runtime(
            available_tools=capability_surface.available_tools,
            used_tools=[],
            used_providers=[],
            metadata_sources=metadata_sources,
            warnings=warnings,
            has_execution=execution is not None,
            degraded_capabilities=capability_surface.degraded_capabilities,
            policy_decisions=capability_surface.policy_decisions,
        )

    @staticmethod
    def _extract_optional_dict(value: Any) -> dict[str, Any] | None:
        return value if isinstance(value, dict) else None

    @staticmethod
    def _extract_dict_list(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    @staticmethod
    def _extract_answer(llm_payload: dict[str, Any]) -> str:
        return llm_payload.get("answer") or "Model did not return a valid answer."

    @staticmethod
    def _extract_sql(llm_payload: dict[str, Any]) -> str | None:
        sql = llm_payload.get("sql") or llm_payload.get("generated_sql")
        return sql if isinstance(sql, str) else None

    @staticmethod
    def _extract_execution(llm_payload: dict[str, Any]) -> ExecuteSqlResponse | None:
        execution_payload = llm_payload.get("execution")
        if isinstance(execution_payload, dict):
            try:
                return ExecuteSqlResponse.model_validate(execution_payload)
            except Exception:
                return None
        return None

    @staticmethod
    def _extract_successful_executions(
        llm_payload: dict[str, Any],
    ) -> list[ExecuteSqlResponse]:
        raw_executions = llm_payload.get("successful_executions")
        if not isinstance(raw_executions, list):
            return []
        successful_executions: list[ExecuteSqlResponse] = []
        seen: set[str] = set()
        for raw_execution in raw_executions:
            if not isinstance(raw_execution, dict):
                continue
            try:
                execution = ExecuteSqlResponse.model_validate(raw_execution)
            except Exception:
                logger.debug("Failed to parse successful execution payload")
                continue
            if execution.status != "succeeded" or execution.execution_id in seen:
                continue
            seen.add(execution.execution_id)
            successful_executions.append(execution)
        return successful_executions

    @staticmethod
    def _extract_metadata_sources(llm_payload: dict[str, Any]) -> list[str]:
        sources = llm_payload.get("metadata_sources", [])
        return sources if isinstance(sources, list) else []

    @staticmethod
    def _extract_context_tables(llm_payload: dict[str, Any]) -> list[str]:
        tables = llm_payload.get("context_tables", [])
        return tables[:20] if isinstance(tables, list) else []

    @staticmethod
    def _extract_runtime_context(llm_payload: dict[str, Any]) -> dict[str, Any]:
        runtime_context = llm_payload.get("runtime_context")
        return runtime_context if isinstance(runtime_context, dict) else {}

    @staticmethod
    def select_connector_name(
        *,
        request: ChatAskRequest,
        llm_payload: dict[str, Any],
        db_connectors: list[ConnectorItem],
    ) -> str | None:
        if request.database_connector_name:
            return request.database_connector_name

        selected_connector = llm_payload.get("selected_database_connector")
        if isinstance(selected_connector, str) and selected_connector.strip():
            return selected_connector.strip()

        target = llm_payload.get("target")
        if isinstance(target, dict):
            connector_name = target.get("connector_name")
            if isinstance(connector_name, str) and connector_name.strip():
                return connector_name.strip()

        return db_connectors[0].name if db_connectors else None

    @staticmethod
    def _serialize_llm_usages(
        llm_usages: list[Any] | None,
    ) -> list[dict[str, Any]]:
        """Serialize LLM usage records to dict format for persistence."""
        if not llm_usages:
            return []
        result = []
        for usage in llm_usages:
            if hasattr(usage, "model_dump"):
                result.append(usage.model_dump())
            elif isinstance(usage, dict):
                result.append(usage)
            elif hasattr(usage, "__dataclass_fields__"):
                result.append(asdict(usage))
        return result

    @staticmethod
    def _extract_clarification_request(llm_payload: dict[str, Any]) -> ClarificationRequest | None:
        """Extract typed clarification request from LLM payload."""
        clarification_payload = llm_payload.get("clarification_request")
        if isinstance(clarification_payload, dict):
            try:
                return ClarificationRequest.model_validate(clarification_payload)
            except Exception:
                logger.debug("Failed to parse clarification_request payload")
        return None
