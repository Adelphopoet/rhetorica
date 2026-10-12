"""Documentation-family orchestration tools."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.error_contract import (
    ErrorCategory,
    build_tool_error,
    retry_idempotent,
)
from rhetorica_backend.domain.chat.orchestration.agent_connector_policy import (
    AgentConnectorPolicy,
)
from rhetorica_backend.domain.documentation import DocumentationRetrievalService


class ToolDocumentationCapabilityService:
    """Capability-family service for external documentation tools."""

    def __init__(
        self,
        *,
        documentation_retrieval_service: DocumentationRetrievalService | None,
        bounded_int,
        tool_error_from_exception,
    ) -> None:
        self._documentation_retrieval_service = documentation_retrieval_service
        self._bounded_int = bounded_int
        self._tool_error_from_exception = tool_error_from_exception

    def execute_search_documentation(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state,
    ) -> dict[str, Any]:
        if self._documentation_retrieval_service is None:
            state.warnings.append("documentation_not_configured")
            return build_tool_error(
                error_code="documentation_not_configured",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Documentation integration is not configured.",
                retry_recommended=False,
                can_continue=True,
            )
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return build_tool_error(
                error_code="documentation_query_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Documentation query is required.",
                retry_recommended=False,
                can_continue=True,
            )
        connector_name = arguments.get("connector_name")
        decision = AgentConnectorPolicy.resolve_connector_name(
            requested_name=connector_name,
            allowed_names=self._allowed_connector_names(request),
            family="documentation",
        )
        if not decision.allowed:
            if decision.warning:
                state.warnings.append(decision.warning)
            return build_tool_error(
                error_code=decision.warning or "agent_documentation_connector_denied",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Documentation connector is not allowed for the selected agent.",
                retry_recommended=False,
                can_continue=True,
            )
        limit = self._bounded_int(arguments.get("limit"), default=5, minimum=1, maximum=10)
        try:
            results, warnings = retry_idempotent(
                lambda: self._documentation_retrieval_service.search(
                    workspace_id=request.workspace_id,
                    query=query.strip(),
                    limit=limit,
                    connector_name=decision.connector_name,
                ),
                max_attempts=2,
            )
        except Exception as exc:
            state.warnings.append("documentation_search_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="documentation_search_failed",
                can_continue=True,
            )
        state.warnings.extend(warnings)
        if results:
            state.metadata_sources.add("documentation")
        return {
            "ok": True,
            "count": len(results),
            "metadata_sources": sorted(state.metadata_sources),
            "results": results,
            "warnings": warnings,
        }

    def execute_read_documentation_page(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state,
    ) -> dict[str, Any]:
        if self._documentation_retrieval_service is None:
            state.warnings.append("documentation_not_configured")
            return build_tool_error(
                error_code="documentation_not_configured",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Documentation integration is not configured.",
                retry_recommended=False,
                can_continue=True,
            )
        page_ref = arguments.get("page_ref")
        if not isinstance(page_ref, str) or not page_ref.strip():
            return build_tool_error(
                error_code="documentation_page_ref_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Documentation page_ref is required.",
                retry_recommended=False,
                can_continue=True,
            )
        connector_name = arguments.get("connector_name")
        decision = AgentConnectorPolicy.resolve_connector_name(
            requested_name=connector_name,
            allowed_names=self._allowed_connector_names(request),
            family="documentation",
        )
        if not decision.allowed:
            if decision.warning:
                state.warnings.append(decision.warning)
            return build_tool_error(
                error_code=decision.warning or "agent_documentation_connector_denied",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Documentation connector is not allowed for the selected agent.",
                retry_recommended=False,
                can_continue=True,
            )
        max_tokens = self._bounded_int(
            arguments.get("max_tokens"),
            default=1600,
            minimum=200,
            maximum=4000,
        )
        section = arguments.get("section")
        if not isinstance(section, str) or not section.strip():
            section = None
        try:
            page, warnings = retry_idempotent(
                lambda: self._documentation_retrieval_service.read_page(
                    workspace_id=request.workspace_id,
                    page_ref=page_ref.strip(),
                    connector_name=decision.connector_name,
                    section=section,
                    max_tokens=max_tokens,
                ),
                max_attempts=2,
            )
        except Exception as exc:
            state.warnings.append("documentation_read_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="documentation_read_failed",
                can_continue=True,
            )
        state.warnings.extend(warnings)
        if page is None:
            return build_tool_error(
                error_code="documentation_page_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Documentation page is missing.",
                retry_recommended=False,
                can_continue=True,
            )
        state.metadata_sources.add("documentation")
        return {
            "ok": True,
            "metadata_sources": sorted(state.metadata_sources),
            "page": page,
            "warnings": warnings,
        }

    def _allowed_connector_names(self, request: ChatAskRequest) -> list[str] | None:
        if request.agent_id is not None:
            return request.agent_allowed_documentation_connector_names
        if self._documentation_retrieval_service is None:
            return []
        return self._documentation_retrieval_service.default_connector_names(
            workspace_id=request.workspace_id
        )
