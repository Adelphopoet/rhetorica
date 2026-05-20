"""Shared response assembly for sync and streaming chat orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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
    optimization_trace: OptimizationTrace
    execution: ExecuteSqlResponse | None
    analysis_document: AnalysisDocument | None
    visualization: VisualizationSpec | None
    analysis_plan: dict[str, Any] | None = None
    worker_results: list[dict[str, Any]] = field(default_factory=list)
    checkpoint_digest: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    llm_usages: list[dict[str, Any]] = field(default_factory=list)
    clarification_request: ClarificationRequest | None = None

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
            analysis_plan=self.analysis_plan,
            worker_results=self.worker_results,
            checkpoint_digest=self.checkpoint_digest,
            warnings=self.warnings,
            assistant_message_id=assistant_message_id,
            clarification_request=self.clarification_request,
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
            analysis_plan=self.analysis_plan,
            worker_results=self.worker_results,
            checkpoint_digest=self.checkpoint_digest,
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
    ) -> str | None:
        if not conversation_id:
            return None

        user_message = self._conversation_manager.save_message(
            conversation_id=conversation_id,
            role="user",
            content=request.question,
        )

        capability_trace_payload = turn.capability_trace.model_dump(
            mode="json",
            by_alias=True,
        )
        runtime_context = dict(turn.runtime_context or {})
        if turn.analysis_plan is not None:
            runtime_context["analysis_plan"] = turn.analysis_plan
        if turn.worker_results:
            runtime_context["worker_results"] = turn.worker_results
        if turn.checkpoint_digest is not None:
            runtime_context["checkpoint_digest"] = turn.checkpoint_digest
        if runtime_context:
            capability_trace_payload["runtime_context"] = runtime_context
        assistant_message = self._conversation_manager.save_message(
            conversation_id=conversation_id,
            role="assistant",
            content=turn.answer,
            generated_sql=turn.generated_sql,
            execution_id=(turn.execution.execution_id if turn.execution is not None else None),
            context_tables=turn.context_tables,
            metadata_sources=turn.metadata_sources,
            claims=turn.claims,
            evidence=turn.evidence,
            claim_evidence_links=turn.claim_evidence_links,
            uncertainty_flags=turn.uncertainty_flags,
            capability_trace=capability_trace_payload,
            optimization_trace=turn.optimization_trace.model_dump(mode="json"),
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
        )
        persisted_messages = [
            message for message in (user_message, assistant_message) if message is not None
        ]
        if persisted_messages and hasattr(
            self._conversation_manager,
            "update_conversation_state_from_turn",
        ):
            checkpoint = runtime_context.get("checkpoint_digest")
            self._conversation_manager.update_conversation_state_from_turn(
                conversation_id=conversation_id,
                messages=persisted_messages,
                checkpoint_digest=checkpoint if isinstance(checkpoint, dict) else None,
                llm_usages=turn.llm_usages if hasattr(turn, "llm_usages") else None,
            )
        return assistant_message.id if assistant_message else None

    def persist_runtime_state(
        self,
        *,
        conversation_id: str | None,
        state: Any,
    ) -> None:
        """Persist compact live runtime state for reload/resume surfaces."""
        if not conversation_id:
            return
        checkpoint = getattr(state, "checkpoint", None)
        analysis_plan = getattr(state, "analysis_plan", None)
        worker_results = getattr(state, "worker_results", [])
        self._conversation_manager.update_analysis_runtime_state(
            conversation_id=conversation_id,
            checkpoint_digest=(checkpoint.digest_payload() if checkpoint is not None else None),
            analysis_plan=(
                analysis_plan.model_dump(mode="json") if analysis_plan is not None else None
            ),
            worker_results=[result.model_dump(mode="json") for result in worker_results[-5:]],
        )


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
        analysis_plan = self._extract_optional_dict(
            llm_payload.get("analysis_plan") or runtime_context.get("analysis_plan")
        )
        worker_results = self._extract_dict_list(
            llm_payload.get("worker_results") or runtime_context.get("worker_results")
        )
        checkpoint_digest = self._extract_optional_dict(
            llm_payload.get("checkpoint_digest") or runtime_context.get("checkpoint_digest")
        )

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
            optimization_trace=self._extract_optimization_trace(llm_payload),
            execution=execution,
            analysis_document=analysis_document,
            visualization=primary_visualization,
            analysis_plan=analysis_plan,
            worker_results=worker_results,
            checkpoint_digest=checkpoint_digest,
            warnings=turn_warnings,
            llm_usages=self._serialize_llm_usages(llm_usages),
            clarification_request=self._extract_clarification_request(llm_payload),
        )

    def save_to_history(
        self,
        *,
        conversation_id: str | None,
        request: ChatAskRequest,
        turn: AssembledChatTurn,
    ) -> str | None:
        return self._history_persistence_service.save_turn(
            conversation_id=conversation_id,
            request=request,
            turn=turn,
        )

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
    def _extract_optimization_trace(llm_payload: dict[str, Any]) -> OptimizationTrace:
        payload = llm_payload.get("optimization_trace")
        if isinstance(payload, dict):
            try:
                return OptimizationTrace.model_validate(payload)
            except Exception:
                return OptimizationTrace()
        return OptimizationTrace()

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
                # Handle frozen dataclass
                result.append({
                    f.name: getattr(usage, f.name) for f in usage.__dataclass_fields__.values()
                })
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
