"""Governed metric registry service for business metric discovery and validation."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from rhetorica_backend.contracts.metrics import (
    MetricDefinition,
    MetricPreflightError,
    MetricPreflightResult,
    MetricRepositoryProtocol,
)
from rhetorica_backend.domain.chat.sql.semantic_preflight import (
    SemanticSqlPreflightService,
)

if TYPE_CHECKING:
    pass


T = TypeVar("T")


@dataclass
class _CacheEntry(Generic[T]):
    """A single cache entry with expiration timestamp."""

    value: T
    expires_at: float  # Unix timestamp


class _SimpleTtlCache:
    """Simple TTL cache for metric discovery results.

    This cache prevents repeated O(n) fuzzy matching operations by caching
    the results of list_all() calls per workspace.
    """

    DEFAULT_TTL_SECONDS = 300  # 5 minutes

    def __init__(self, ttl_seconds: int | None = None) -> None:
        """Initialize the cache.

        Args:
            ttl_seconds: Time-to-live in seconds. Defaults to 300 (5 minutes).
        """
        self._ttl = ttl_seconds if ttl_seconds is not None else self.DEFAULT_TTL_SECONDS
        self._store: dict[str, _CacheEntry[Any]] = {}

    def get(self, key: str) -> Any | None:
        """Get a cached value if it exists and hasn't expired.

        Args:
            key: The cache key.

        Returns:
            The cached value or None if not found or expired.
        """
        entry = self._store.get(key)
        if entry is None:
            return None
        now = datetime.now(tz=UTC).timestamp()
        if now < entry.expires_at:
            return entry.value
        # Entry expired, remove it
        del self._store[key]
        return None

    def set(self, key: str, value: Any) -> None:
        """Store a value in the cache with TTL.

        Args:
            key: The cache key.
            value: The value to cache.
        """
        now = datetime.now(tz=UTC).timestamp()
        self._store[key] = _CacheEntry(
            value=value,
            expires_at=now + self._ttl,
        )

    def invalidate(self, key: str) -> None:
        """Invalidate a specific cache entry.

        Args:
            key: The cache key to invalidate.
        """
        self._store.pop(key, None)

    def clear(self) -> None:
        """Clear all cache entries."""
        self._store.clear()


class MetricRegistryService:
    """Domain service for governed metric discovery and validation.

    This service enables the system to:
    - Discover metrics from natural language business terms
    - Validate SQL queries against metric contracts
    - Enforce allowed dimensions in BI queries
    - Provide certified metric context for SQL planning

    The service depends only on the MetricRepositoryProtocol abstraction, not on
    direct database access, enabling testability and separation of concerns.
    """

    _FORMULA_TRUNCATION_LIMIT = 200
    _MAX_SIMILARITY_MATCHES = 3
    _CACHE_TTL_SECONDS = 300  # 5 minutes

    def __init__(
        self,
        metric_repository: MetricRepositoryProtocol,
        sql_preflight: SemanticSqlPreflightService | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> None:
        """Initialize the service with a metric repository.

        Args:
            metric_repository: The repository for metric persistence operations.
            sql_preflight: Optional SQL preflight service for consistency checks.
                          If not provided, uses SemanticSqlPreflightService internally.
            cache_ttl_seconds: Optional TTL for the discovery cache. Defaults to 300s.
        """
        self._repository = metric_repository
        self._sql_preflight = sql_preflight or SemanticSqlPreflightService()
        self._cache = _SimpleTtlCache(ttl_seconds=cache_ttl_seconds)

    def _get_cached_metrics(self, workspace_id: str) -> list[MetricDefinition]:
        """Get metrics for a workspace, using cache if available.

        Args:
            workspace_id: The workspace identifier.

        Returns:
            List of metric definitions for the workspace.
        """
        cache_key = f"list_all:{workspace_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        metrics = self._repository.list_all(workspace_id=workspace_id)
        self._cache.set(cache_key, metrics)
        return metrics

    def invalidate_cache(self, workspace_id: str) -> None:
        """Invalidate the cache for a specific workspace.

        Call this after upsert or delete operations.

        Args:
            workspace_id: The workspace identifier.
        """
        self._cache.invalidate(f"list_all:{workspace_id}")

    def get_by_id(
        self,
        workspace_id: str,
        metric_id: str,
    ) -> MetricDefinition | None:
        """Retrieve a metric definition by its unique metric_id.

        Args:
            workspace_id: The workspace identifier.
            metric_id: The unique metric identifier.

        Returns:
            The metric definition if found, None otherwise.
        """
        return self._repository.get_by_id(workspace_id=workspace_id, metric_id=metric_id)

    def get_by_name(
        self,
        workspace_id: str,
        name: str,
    ) -> MetricDefinition | None:
        """Retrieve a metric definition by its name (case-insensitive).

        Args:
            workspace_id: The workspace identifier.
            name: The metric name to search for.

        Returns:
            The metric definition if found, None otherwise.
        """
        return self._repository.get_by_name(workspace_id=workspace_id, name=name)

    def list_all(self, workspace_id: str) -> list[MetricDefinition]:
        """List all metric definitions for a workspace.

        Results are cached to avoid repeated O(n) fetches.

        Args:
            workspace_id: The workspace identifier.

        Returns:
            List of all metric definitions in the workspace.
        """
        return self._get_cached_metrics(workspace_id)

    def search(
        self,
        name_query: str,
        workspace_id: str,
    ) -> list[MetricDefinition]:
        """Perform case-insensitive partial match search on metric names.

        Args:
            name_query: The search query string.
            workspace_id: The workspace identifier.

        Returns:
            List of matching metric definitions.
        """
        all_metrics = self._get_cached_metrics(workspace_id)
        query_lower = name_query.lower().strip()
        if not query_lower:
            return all_metrics

        return [
            metric
            for metric in all_metrics
            if query_lower in metric.name.lower()
        ]

    def find_by_business_term(
        self,
        term: str,
        workspace_id: str,
    ) -> list[MetricDefinition]:
        """Discover metrics by fuzzy matching a business term to metric names and descriptions.

        Uses string similarity (difflib.get_close_matches) to find metrics that
        semantically match the provided business term. Returns up to 3 results
        ranked by similarity score.

        Results are cached per workspace to avoid repeated O(n) fuzzy matching.

        Args:
            term: The business term or phrase to match (e.g., "revenue", "how much money").
            workspace_id: The workspace identifier.

        Returns:
            List of matching metric definitions, ordered by similarity (best first).
        """
        all_metrics = self._get_cached_metrics(workspace_id)
        if not all_metrics or not term:
            return []

        term_lower = term.lower().strip()
        term_words = set(term_lower.split())
        # Filter out common stop words for token matching
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "did", "do", "does",
            "how", "what", "when", "where", "which", "who", "much", "many", "we", "i", "you",
        }
        significant_term_words = term_words - stop_words

        candidates: list[tuple[str, str, float]] = []

        for metric in all_metrics:
            name_lower = metric.name.lower()
            desc_lower = metric.description.lower()

            # Exact substring match gets highest priority
            if term_lower in name_lower or term_lower in desc_lower:
                candidates.append((metric.metric_id, metric.name, 1.0))
                continue

            # Token-based matching: check if significant term words are in name/description
            desc_words = set(desc_lower.split())
            if significant_term_words & desc_words:
                # At least one significant word matches
                match_ratio = len(significant_term_words & desc_words) / max(len(significant_term_words), 1)
                candidates.append((metric.metric_id, metric.name, 0.6 + match_ratio * 0.2))
                continue

            # Also check reverse: key words from description in term
            name_words = set(name_lower.split())
            if significant_term_words & name_words:
                match_ratio = len(significant_term_words & name_words) / max(len(significant_term_words), 1)
                candidates.append((metric.metric_id, metric.name, 0.6 + match_ratio * 0.2))
                continue

            # Use difflib for fuzzy matching
            name_similarity = self._string_similarity(term_lower, name_lower)
            desc_similarity = self._string_similarity(term_lower, desc_lower) * 0.7
            max_similarity = max(name_similarity, desc_similarity)

            if max_similarity >= 0.4:
                candidates.append((metric.metric_id, metric.name, max_similarity))

        # Sort by similarity descending
        candidates.sort(key=lambda x: x[2], reverse=True)

        # Return up to MAX_SIMILARITY_MATCHES results
        seen_ids: set[str] = set()
        results: list[MetricDefinition] = []
        for metric_id, _name, _score in candidates:
            if metric_id in seen_ids:
                continue
            metric = self.get_by_id(workspace_id=workspace_id, metric_id=metric_id)
            if metric is not None:
                results.append(metric)
                seen_ids.add(metric_id)
                if len(results) >= self._MAX_SIMILARITY_MATCHES:
                    break

        return results

    def get_allowed_dimensions(self, metric: MetricDefinition) -> list[str]:
        """Return the allowed dimension columns for a metric.

        Args:
            metric: The metric definition to query.

        Returns:
            List of allowed dimension column names.
        """
        return list(metric.allowed_dimensions)

    def get_compact_card(self, metric: MetricDefinition) -> dict[str, Any]:
        """Generate a compact metric card for LLM prompt injection.

        This method strips the full SQL formula to a maximum of 200 characters
        as a context-window guardrail for prompt injection.

        Args:
            metric: The metric definition to create a card for.

        Returns:
            Dictionary containing the compact metric card data.
        """
        formula_summary = metric.formula[: self._FORMULA_TRUNCATION_LIMIT]
        if len(metric.formula) > self._FORMULA_TRUNCATION_LIMIT:
            formula_summary = formula_summary.rstrip() + "..."

        return {
            "metric_id": metric.metric_id,
            "name": metric.name,
            "formula_summary": formula_summary,
            "grain": metric.grain,
            "allowed_dimensions": list(metric.allowed_dimensions),
            "source_refs": list(metric.source_tables),
            "certified": metric.certified,
        }

    def is_certified(self, metric: MetricDefinition) -> bool:
        """Check if a metric has been certified by data governance.

        Args:
            metric: The metric definition to check.

        Returns:
            True if the metric is certified, False otherwise.
        """
        return metric.certified

    def get_freshness_warning(
        self,
        metric: MetricDefinition,
        last_updated: datetime | None = None,
    ) -> str | None:
        """Generate a freshness warning if the metric data is stale.

        Compares the metric's freshness_hours SLA against the last_updated timestamp
        to determine if the data should be considered stale.

        Args:
            metric: The metric definition to check.
            last_updated: The timestamp of the last data update (defaults to metric.updated_at).

        Returns:
            A warning message if stale, None if fresh.
        """
        if metric.freshness_hours is None:
            return None

        check_time = last_updated or metric.updated_at
        now = datetime.now(tz=UTC)
        age_hours = (now - check_time).total_seconds() / 3600

        if age_hours > metric.freshness_hours:
            return (
                f"Metric '{metric.name}' data is stale. "
                f"SLA: {metric.freshness_hours:.1f}h, Age: {age_hours:.1f}h."
            )
        return None

    def check_metric_consistency(
        self,
        sql: str,
        metric: MetricDefinition,
        context_tables: list[str],
        metadata_table_columns: dict[str, set[str]],
    ) -> MetricPreflightResult:
        """Check SQL query consistency against a metric's governance contract.

        This is a thin wrapper that delegates to SemanticSqlPreflightService,
        converting the result to MetricPreflightResult for API compatibility.

        Validates that:
        - All column references are in allowed_dimensions
        - No query references tables outside source_tables

        Args:
            sql: The SQL query to validate.
            metric: The metric definition to validate against.
            context_tables: Available tables in the metadata context.
            metadata_table_columns: Map of table FQNs to their column sets.

        Returns:
            MetricPreflightResult with pass/fail status and any errors.
        """
        # Delegate to SemanticSqlPreflightService
        spps_result = self._sql_preflight.check_metric_consistency(
            sql=sql,
            metric_id=metric.metric_id,
            metric_name=metric.name,
            metric_allowed_dimensions=list(metric.allowed_dimensions),
            metric_source_tables=list(metric.source_tables),
            context_tables=context_tables,
            metadata_table_columns=metadata_table_columns,
        )

        # Convert SqlPreflightResult to MetricPreflightResult
        errors = [
            MetricPreflightError(
                code=err.code,
                message=err.message,
                identifier=err.identifier,
                table=err.table,
                suggestions=err.suggestions,
            )
            for err in spps_result.errors
        ]

        return MetricPreflightResult(
            status="pass" if spps_result.status == "pass" else "fail",
            errors=errors,
        )

    def upsert(self, metric: MetricDefinition) -> MetricDefinition:
        """Create or update a metric definition.

        Args:
            metric: The metric definition to upsert.

        Returns:
            The persisted metric definition with updated timestamps.
        """
        result = self._repository.upsert(metric)
        # Invalidate cache after modification
        self.invalidate_cache(metric.workspace_id)
        return result

    @staticmethod
    def _string_similarity(s1: str, s2: str) -> float:
        """Calculate string similarity using SequenceMatcher ratio.

        Args:
            s1: First string.
            s2: Second string.

        Returns:
            Similarity ratio between 0.0 and 1.0.
        """
        if not s1 or not s2:
            return 0.0
        return difflib.SequenceMatcher(None, s1, s2).ratio()
