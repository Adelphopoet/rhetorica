from __future__ import annotations

import re
from typing import Any

from rhetorica_backend.domain.rag.pipeline_service import FastEmbedProvider
from rhetorica_backend.infra.postgres.rag_connector_repository import RagConnectorRepository
from rhetorica_backend.infra.qdrant.insights_store import QdrantInsightsStore


class RagInsightRetriever:
    """Search reusable insights from Qdrant for chat-time grounding."""

    _DEFAULT_RETRIEVAL_THRESHOLD = 0.55
    _DEFAULT_CONTEXT_BUDGET_TOKENS = 900
    _MIN_THRESHOLD = 0.45
    _MAX_THRESHOLD = 0.75

    def __init__(self, rag_connector_repository: RagConnectorRepository):
        self._rag_connector_repository = rag_connector_repository

    def is_available(self, workspace_id: str, rag_connector_name: str | None = None) -> bool:
        return (
            self._rag_connector_repository.get_connection(
                workspace_id=workspace_id,
                name=rag_connector_name,
            )
            is not None
        )

    def search(
        self,
        workspace_id: str,
        query: str,
        limit: int,
        rag_connector_name: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        connection = self._rag_connector_repository.get_connection(
            workspace_id=workspace_id,
            name=rag_connector_name,
        )
        if connection is None:
            return [], ["rag_connector_missing"]

        embedder = FastEmbedProvider(connection.embedding_model)
        vector = embedder.embed(query.strip())
        if not vector:
            return [], ["rag_embedding_failed"]

        store = QdrantInsightsStore(connection)
        warnings: list[str] = []
        adaptive_threshold = self._adaptive_threshold(
            query=query,
            extra_settings=connection.extra_settings,
        )
        default_threshold = self._default_threshold(connection.extra_settings)

        merged = self._search_with_threshold(
            store=store,
            workspace_id=workspace_id,
            vector=vector,
            limit=limit,
            threshold=adaptive_threshold,
            query=query,
        )
        if not merged and adaptive_threshold != default_threshold:
            warnings.append("rag_threshold_fallback_default")
            merged = self._search_with_threshold(
                store=store,
                workspace_id=workspace_id,
                vector=vector,
                limit=limit,
                threshold=default_threshold,
                query=query,
            )

        context_budget = self._context_budget_tokens(connection.extra_settings)
        snippets, budget_warning = self._pack_snippets_with_budget(
            records=merged,
            budget_tokens=context_budget,
        )
        if budget_warning:
            warnings.append(budget_warning)

        return snippets, warnings

    @staticmethod
    def _default_threshold(extra_settings: dict[str, object]) -> float:
        raw = extra_settings.get("retrieval_similarity_threshold")
        if isinstance(raw, float | int):
            value = float(raw)
            if 0 <= value <= 1:
                return value
        return RagInsightRetriever._DEFAULT_RETRIEVAL_THRESHOLD

    @staticmethod
    def _adaptive_threshold(query: str, extra_settings: dict[str, object]) -> float:
        default = RagInsightRetriever._default_threshold(extra_settings)
        min_threshold = RagInsightRetriever._float_setting(
            extra_settings, "retrieval_similarity_threshold_min", RagInsightRetriever._MIN_THRESHOLD
        )
        max_threshold = RagInsightRetriever._float_setting(
            extra_settings, "retrieval_similarity_threshold_max", RagInsightRetriever._MAX_THRESHOLD
        )
        if min_threshold > max_threshold:
            min_threshold, max_threshold = max_threshold, min_threshold
        default = max(min_threshold, min(max_threshold, default))

        tokens = [token for token in re.split(r"\W+", query.lower()) if token]
        if len(tokens) <= 3:
            return min_threshold
        if len(tokens) >= 8:
            return max_threshold
        return default

    @staticmethod
    def _float_setting(extra_settings: dict[str, object], key: str, default: float) -> float:
        raw = extra_settings.get(key)
        if isinstance(raw, float | int):
            value = float(raw)
            if 0 <= value <= 1:
                return value
        return default

    @staticmethod
    def _to_str_list(raw: object) -> list[str]:
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, str)]

    @staticmethod
    def _merge_ranked_results(
        verified: list[dict[str, Any]],
        pending: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        seen_ids: set[str] = set()
        result: list[dict[str, Any]] = []

        for bucket in (verified, pending):
            for item in bucket:
                item_id = str(item.get("id") or "")
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                result.append(item)
                if len(result) >= limit:
                    return result
        return result

    def _search_with_threshold(
        self,
        store: QdrantInsightsStore,
        workspace_id: str,
        vector: list[float],
        limit: int,
        threshold: float,
        query: str,
    ) -> list[dict[str, Any]]:
        verified = store.search_by_status(
            workspace_id=workspace_id,
            verification_status="verified",
            vector=vector,
            limit=limit,
            score_threshold=threshold,
        )
        pending = store.search_by_status(
            workspace_id=workspace_id,
            verification_status="pending",
            vector=vector,
            limit=limit,
            score_threshold=threshold,
        )

        merged = self._merge_ranked_results(verified=verified, pending=pending, limit=limit * 2)
        scored = self._hybrid_rank(records=merged, query=query)
        return scored[:limit]

    def _hybrid_rank(
        self,
        records: list[dict[str, Any]],
        query: str,
    ) -> list[dict[str, Any]]:
        query_terms = [token for token in re.split(r"\W+", query.lower()) if token]
        scored: list[tuple[float, dict[str, Any]]] = []
        for record in records:
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            vector_score = float(record.get("score") or 0.0)
            lexical_score = self._lexical_score(query_terms=query_terms, payload=payload)
            verification_status = str(payload.get("verification_status") or "pending")
            status_boost = 0.1 if verification_status == "verified" else 0.0
            hybrid_score = (vector_score * 0.75) + (lexical_score * 0.25) + status_boost
            record_copy = dict(record)
            record_copy["hybrid_score"] = hybrid_score
            scored.append((hybrid_score, record_copy))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored]

    @staticmethod
    def _lexical_score(query_terms: list[str], payload: dict[str, Any]) -> float:
        if not query_terms:
            return 0.0
        corpus_parts = [
            str(payload.get("summary") or ""),
            str(payload.get("article_markdown") or ""),
            " ".join(item for item in payload.get("keywords", []) if isinstance(item, str)),
            " ".join(item for item in payload.get("context_tables", []) if isinstance(item, str)),
        ]
        corpus = " ".join(corpus_parts).lower()
        if not corpus:
            return 0.0
        matches = sum(1 for term in query_terms if term in corpus)
        return matches / max(1, len(query_terms))

    @classmethod
    def _context_budget_tokens(cls, extra_settings: dict[str, object]) -> int:
        raw = extra_settings.get("rag_context_budget_tokens")
        if isinstance(raw, int | float):
            value = int(raw)
            if 100 <= value <= 4_000:
                return value
        return cls._DEFAULT_CONTEXT_BUDGET_TOKENS

    def _pack_snippets_with_budget(
        self,
        records: list[dict[str, Any]],
        budget_tokens: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
        snippets: list[dict[str, Any]] = []
        spent = 0
        for record in records:
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            snippet = self._build_snippet(record=record, payload=payload)
            snippet_tokens = self._estimate_tokens_for_snippet(snippet)
            if spent + snippet_tokens > budget_tokens:
                return snippets, "rag_context_budget_reached"
            snippets.append(snippet)
            spent += snippet_tokens
        return snippets, None

    def _build_snippet(
        self,
        record: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        article_markdown = str(payload.get("article_markdown") or "")
        summary = str(payload.get("summary") or "").strip()
        if not summary:
            summary = article_markdown[:220].strip()
        key_facts = self._extract_key_facts(article_markdown)
        citation = str(
            payload.get("citation")
            or payload.get("source_url")
            or payload.get("source")
            or record.get("id")
            or ""
        )
        compact_article = article_markdown[:500]
        return {
            "id": str(record.get("id") or ""),
            "score": float(record.get("hybrid_score") or record.get("score") or 0.0),
            "verification_status": str(payload.get("verification_status") or "pending"),
            "summary": summary[:300],
            "key_facts": key_facts,
            "citation": citation,
            "article_markdown": compact_article,
            "keywords": self._to_str_list(payload.get("keywords")),
            "context_tables": self._to_str_list(payload.get("context_tables")),
        }

    @staticmethod
    def _extract_key_facts(article_markdown: str) -> list[str]:
        lines = [line.strip("-* \t") for line in article_markdown.splitlines() if line.strip()]
        if not lines:
            return []
        facts: list[str] = []
        for line in lines:
            if line.startswith("#"):
                continue
            facts.append(line[:180])
            if len(facts) >= 3:
                break
        return facts

    @staticmethod
    def _estimate_tokens_for_snippet(snippet: dict[str, Any]) -> int:
        joined = " ".join(
            str(snippet.get(key) or "")
            for key in (
                "summary",
                "article_markdown",
                "citation",
            )
        )
        joined += " ".join(snippet.get("keywords", []))
        joined += " ".join(snippet.get("context_tables", []))
        return max(1, len(joined) // 4)
