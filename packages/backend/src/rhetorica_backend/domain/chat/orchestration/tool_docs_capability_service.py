"""RAG insight retrieval tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.error_contract import (
    ErrorCategory,
    build_tool_error,
    retry_idempotent,
)
from rhetorica_backend.domain.chat.orchestration.agent_connector_policy import (
    AgentConnectorPolicy,
)
from rhetorica_backend.domain.rag.retrieval_service import RagInsightRetriever

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import (
        ToolOrchestrationState,
    )

class DocsToolSupport(Protocol):
    """Shared ToolExecutor helpers used by docs-family services."""

    def _bounded_int(
        self,
        value: Any,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int: ...

    def _tool_error_from_exception(
        self,
        exc: Exception,
        *,
        default_error_code: str,
        can_continue: bool,
    ) -> dict[str, Any]: ...


class ToolDocsCapabilityService:
    """Capability-family service for RAG insights."""

    def __init__(
        self,
        *,
        support: DocsToolSupport,
        rag_retriever: RagInsightRetriever | None = None,
    ) -> None:
        self._support = support
        self._rag_retriever = rag_retriever

    def execute_search_rag_insights(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        """Execute search_rag_insights tool."""
        if self._rag_retriever is None:
            state.warnings.append("rag_not_configured")
            return build_tool_error(
                error_code="rag_not_configured",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="RAG retriever is not configured.",
                retry_recommended=False,
                can_continue=True,
            )

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return build_tool_error(
                error_code="rag_query_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="RAG query is required.",
                retry_recommended=False,
                can_continue=True,
            )

        limit = self._support._bounded_int(
            arguments.get("limit"),
            default=5,
            minimum=1,
            maximum=10,
        )
        connector_name = arguments.get("connector_name")
        decision = AgentConnectorPolicy.resolve_connector_name(
            requested_name=connector_name,
            allowed_names=request.agent_allowed_rag_connector_names,
            family="rag",
        )
        if not decision.allowed:
            if decision.warning:
                state.warnings.append(decision.warning)
            return build_tool_error(
                error_code=decision.warning or "agent_rag_connector_denied",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="RAG connector is not allowed for the selected agent.",
                retry_recommended=False,
                can_continue=True,
            )
        connector_name = decision.connector_name

        try:
            results, rag_warnings = retry_idempotent(
                lambda: self._rag_retriever.search(
                    workspace_id=request.workspace_id,
                    query=query.strip(),
                    limit=limit,
                    rag_connector_name=connector_name,
                ),
                max_attempts=3,
            )
        except Exception as exc:
            state.warnings.append("rag_search_failed")
            return self._support._tool_error_from_exception(
                exc,
                default_error_code="rag_search_failed",
                can_continue=True,
            )

        state.warnings.extend(rag_warnings)
        if results:
            state.metadata_sources.add("rag")

        return {
            "ok": True,
            "count": len(results),
            "metadata_sources": sorted(state.metadata_sources),
            "results": results,
            "warnings": rag_warnings,
        }
