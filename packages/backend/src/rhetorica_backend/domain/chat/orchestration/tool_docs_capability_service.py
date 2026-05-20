"""Docs-family orchestration tools: RAG and Confluence search."""

from __future__ import annotations

import base64
import html
import re
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from rhetorica_backend.contracts.admin_integrations import ConfluenceConnectionConfig
from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.error_contract import (
    ErrorCategory,
    build_tool_error,
    retry_idempotent,
)
from rhetorica_backend.domain.rag.retrieval_service import RagInsightRetriever
from rhetorica_backend.infra.postgres.confluence_connector_repository import (
    ConfluenceConnectorRepository,
)

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import (
        ToolOrchestrationState,
    )

HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


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
    """Capability-family service for RAG and Confluence tools."""

    def __init__(
        self,
        *,
        support: DocsToolSupport,
        rag_retriever: RagInsightRetriever | None = None,
        confluence_connector_repository: ConfluenceConnectorRepository | None = None,
        search_confluence_pages=None,
    ) -> None:
        self._support = support
        self._rag_retriever = rag_retriever
        self._confluence_connector_repository = confluence_connector_repository
        self._search_confluence_pages_impl = (
            search_confluence_pages or self._search_confluence_pages
        )

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
        if not isinstance(connector_name, str):
            connector_name = None

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

    def execute_search_confluence_pages(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        """Execute search_confluence_pages tool."""
        if self._confluence_connector_repository is None:
            state.warnings.append("confluence_not_configured")
            return build_tool_error(
                error_code="confluence_not_configured",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Confluence integration is not configured.",
                retry_recommended=False,
                can_continue=True,
            )

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return build_tool_error(
                error_code="confluence_query_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Confluence query is required.",
                retry_recommended=False,
                can_continue=True,
            )

        connector_name = arguments.get("connector_name")
        if not isinstance(connector_name, str):
            connector_name = None
        connection = self._confluence_connector_repository.get_connection(
            workspace_id=request.workspace_id,
            name=connector_name,
        )
        if connection is None:
            state.warnings.append("confluence_connector_missing")
            return build_tool_error(
                error_code="confluence_connector_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Confluence connector is missing.",
                retry_recommended=False,
                can_continue=True,
            )

        space_key = arguments.get("space_key")
        if not isinstance(space_key, str) or not space_key.strip():
            space_key = connection.space_key
        limit = self._support._bounded_int(
            arguments.get("limit"),
            default=connection.page_limit,
            minimum=1,
            maximum=20,
        )
        try:
            pages, warnings = retry_idempotent(
                lambda: self._search_confluence_pages_impl(
                    connection=connection,
                    query=query.strip(),
                    limit=limit,
                    space_key=space_key,
                ),
                max_attempts=3,
            )
        except Exception as exc:
            state.warnings.append("confluence_search_failed")
            return self._support._tool_error_from_exception(
                exc,
                default_error_code="confluence_search_failed",
                can_continue=True,
            )

        state.warnings.extend(warnings)
        state.metadata_sources.add("confluence")
        return {
            "ok": True,
            "count": len(pages),
            "metadata_sources": sorted(state.metadata_sources),
            "results": pages,
            "warnings": warnings,
        }

    def _search_confluence_pages(
        self,
        *,
        connection: ConfluenceConnectionConfig,
        query: str,
        limit: int,
        space_key: str | None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        warnings: list[str] = []
        endpoint = f"{connection.base_url.rstrip('/')}/rest/api/content/search"
        escaped_query = query.replace('"', '\\"')
        cql_parts = ['type=page', f'text~"{escaped_query}"']
        if space_key:
            cql_parts.append(f'space="{space_key.strip()}"')

        headers = {"Accept": "application/json"}
        if connection.api_token:
            if connection.auth_type == "basic" and connection.username:
                raw = f"{connection.username}:{connection.api_token}"
                encoded = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
                headers["Authorization"] = f"Basic {encoded}"
            else:
                headers["Authorization"] = f"Bearer {connection.api_token}"

        with httpx.Client(timeout=20.0, verify=connection.verify_tls) as client:
            response = client.get(
                endpoint,
                params={
                    "cql": " and ".join(cql_parts),
                    "limit": limit,
                    "expand": "body.storage",
                },
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()

        results = body.get("results")
        if not isinstance(results, list):
            return [], warnings

        pages: list[dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            page_id = item.get("id")
            title = item.get("title")
            if not isinstance(title, str) or not title.strip():
                continue

            storage = item.get("body") if isinstance(item.get("body"), dict) else {}
            raw_html = (
                storage.get("storage", {})
                if isinstance(storage.get("storage"), dict)
                else {}
            ).get("value")
            excerpt = self._clean_confluence_html(raw_html)
            links = item.get("_links") if isinstance(item.get("_links"), dict) else {}
            webui = links.get("webui") if isinstance(links, dict) else None
            page_url = None
            if isinstance(webui, str) and webui.strip():
                page_url = f"{connection.base_url.rstrip('/')}{webui}"

            pages.append(
                {
                    "id": str(page_id) if page_id is not None else None,
                    "title": title.strip(),
                    "url": page_url,
                    "excerpt": excerpt[:600],
                }
            )

        return pages, warnings

    @staticmethod
    def _clean_confluence_html(raw_html: Any) -> str:
        if not isinstance(raw_html, str) or not raw_html.strip():
            return ""
        without_tags = HTML_TAG_RE.sub(" ", raw_html)
        unescaped = html.unescape(without_tags)
        return WHITESPACE_RE.sub(" ", unescaped).strip()
