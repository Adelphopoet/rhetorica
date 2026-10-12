from __future__ import annotations

from typing import Any

from rhetorica_backend.domain.documentation.embedding import OpenAiCompatibleEmbeddingProvider
from rhetorica_backend.infra.postgres.documentation_connector_repository import (
    DocumentationConnectorRepository,
)
from rhetorica_backend.infra.qdrant.documentation_store import QdrantDocumentationStore


class DocumentationRetrievalService:
    """Hybrid lexical + semantic retrieval for external documentation."""

    _DEFAULT_LIMIT = 5
    _MAX_LIMIT = 10
    _DEFAULT_CONTEXT_BUDGET_TOKENS = 1600

    def __init__(self, repository: DocumentationConnectorRepository):
        self._repository = repository

    def is_available(self, workspace_id: str, connector_name: str | None = None) -> bool:
        return self._repository.get_connection(workspace_id=workspace_id, name=connector_name) is not None

    def default_connector_names(self, workspace_id: str) -> list[str]:
        connectors = [
            item
            for item in self._repository.list(workspace_id=workspace_id, limit=100)
            if item.is_active and item.visible_to_default_assistant
        ]
        if connectors:
            return [item.name for item in connectors]
        builtin = self._repository.get(workspace_id=workspace_id, name=None)
        if builtin is None or not builtin.is_active or not builtin.visible_to_default_assistant:
            return []
        return [builtin.name]

    def search(
        self,
        *,
        workspace_id: str,
        query: str,
        limit: int | None = None,
        connector_name: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        bounded_limit = max(1, min(self._MAX_LIMIT, int(limit or self._DEFAULT_LIMIT)))
        connector, connector_warnings = self._resolve_search_connector(
            workspace_id=workspace_id,
            connector_name=connector_name,
        )
        if connector is None:
            return [], connector_warnings or ["documentation_connector_missing"]
        connection = self._repository.resolve_secrets(
            connector.connection,
            workspace_id=workspace_id,
            connector_id=connector.id,
        )
        connector = connector.model_copy(update={"connection": connection})
        execution_scope = self._repository.execution_scope(
            workspace_id=workspace_id,
            name=connector.name,
        )
        if execution_scope is None:
            return [], [*connector_warnings, "documentation_connector_missing"]
        source_workspace_id, source_connector_name = execution_scope
        lexical = self._repository.search_chunks(
            workspace_id=source_workspace_id,
            query=query,
            connector_name=source_connector_name,
            limit=bounded_limit * 2,
        )
        semantic: list[dict[str, Any]] = []
        warnings: list[str] = []
        try:
            vector = OpenAiCompatibleEmbeddingProvider(connector.connection).embed(query)
            semantic_hits = QdrantDocumentationStore(connector.connection).search(
                workspace_id=source_workspace_id,
                connector_name=source_connector_name,
                vector=vector,
                limit=bounded_limit * 2,
            )
            by_point = self._repository.get_chunks_by_point_ids(
                workspace_id=source_workspace_id,
                point_ids=[hit["point_id"] for hit in semantic_hits],
            )
            for hit in semantic_hits:
                point_id = hit["point_id"]
                record = by_point.get(point_id)
                if record is None:
                    continue
                semantic.append(
                    {
                        **record,
                        "connector_name": connector.name,
                        "semantic_score": hit["semantic_score"],
                    }
                )
        except Exception:
            warnings.append("documentation_semantic_search_unavailable")

        lexical = [{**item, "connector_name": connector.name} for item in lexical]
        merged = self._merge_results(lexical=lexical, semantic=semantic)
        return merged[:bounded_limit], [*connector_warnings, *warnings]

    def _resolve_search_connector(
        self,
        *,
        workspace_id: str,
        connector_name: str | None,
    ):
        if connector_name:
            return self._repository.get(workspace_id=workspace_id, name=connector_name), []
        connectors = [
            item
            for item in self._repository.list(workspace_id=workspace_id, limit=100)
            if item.is_active
        ]
        if len(connectors) == 1:
            return connectors[0], []
        if len(connectors) > 1:
            return None, ["documentation_connector_required"]
        return self._repository.get(workspace_id=workspace_id, name=None), []

    def read_page(
        self,
        *,
        workspace_id: str,
        page_ref: str,
        connector_name: str | None = None,
        section: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        source_workspace_id = workspace_id
        source_connector_name = connector_name
        if connector_name:
            execution_scope = self._repository.execution_scope(
                workspace_id=workspace_id,
                name=connector_name,
            )
            if execution_scope is None:
                return None, ["documentation_page_missing"]
            source_workspace_id, source_connector_name = execution_scope
        page = self._repository.get_page(
            workspace_id=source_workspace_id,
            page_ref=page_ref,
            connector_name=source_connector_name,
        )
        if page is None:
            return None, ["documentation_page_missing"]
        body = str(page.get("body_text") or "")
        if section:
            body = self._extract_section(body, section) or body
        budget = max(200, min(4000, int(max_tokens or self._DEFAULT_CONTEXT_BUDGET_TOKENS)))
        packed_body, truncated = self._pack_text(body, budget)
        result = {
            "connector_name": connector_name or page["connector_name"],
            "page_ref": page["page_ref"],
            "url": page["url"],
            "title": page["title"],
            "section": section,
            "content": packed_body,
            "body_hash": page["body_hash"],
            "updated_at": page["updated_at"],
        }
        warnings = ["documentation_read_budget_reached"] if truncated else []
        return result, warnings

    @staticmethod
    def _merge_results(
        *,
        lexical: list[dict[str, Any]],
        semantic: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        rank_order = 0
        for item in lexical:
            key = str(item.get("chunk_ref") or item.get("point_id"))
            if key in merged:
                continue
            merged[key] = {**item, "semantic_score": 0.0, "_rank_order": rank_order}
            rank_order += 1
        for item in semantic:
            key = str(item.get("chunk_ref") or item.get("point_id"))
            semantic_score = float(item.get("semantic_score") or 0.0)
            existing = merged.get(key)
            if existing is None:
                merged[key] = {**item, "semantic_score": semantic_score, "_rank_order": rank_order}
                rank_order += 1
                continue
            merged[key] = {**existing, "semantic_score": semantic_score}

        def sort_key(item: dict[str, Any]) -> tuple[int, float, int]:
            semantic_score = float(item.get("semantic_score") or 0.0)
            return (
                0 if semantic_score > 0 else 1,
                -semantic_score,
                int(item.get("_rank_order") or 0),
            )

        results = sorted(merged.values(), key=sort_key)
        page_refs: set[str] = set()
        deduped_results: list[dict[str, Any]] = []
        for item in results:
            item.pop("_rank_order", None)
            page_ref = str(item.get("page_ref") or "")
            if page_ref and page_ref in page_refs:
                continue
            if page_ref:
                page_refs.add(page_ref)
            deduped_results.append(item)
        return deduped_results

    @staticmethod
    def _pack_text(text: str, budget_tokens: int) -> tuple[str, bool]:
        words = text.split()
        max_words = max(1, int(budget_tokens * 0.75))
        if len(words) <= max_words:
            return text, False
        return " ".join(words[:max_words]), True

    @staticmethod
    def _extract_section(text: str, section: str) -> str | None:
        marker = section.casefold()
        lines = text.splitlines()
        collected: list[str] = []
        in_section = False
        for line in lines:
            stripped = line.strip()
            is_heading = stripped.startswith("#")
            if is_heading and marker in stripped.casefold():
                in_section = True
                collected.append(line)
                continue
            if in_section and is_heading:
                break
            if in_section:
                collected.append(line)
        return "\n".join(collected).strip() or None
