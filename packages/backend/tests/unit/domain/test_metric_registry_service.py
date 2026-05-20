"""Tests for MetricRegistryService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rhetorica_backend.contracts.metrics import MetricDefinition
from rhetorica_backend.domain.metadata.metric_registry_service import (
    MetricRegistryService,
    _SimpleTtlCache,
)


class _FakeMetricRepository:
    """Fake repository for testing MetricRegistryService."""

    def __init__(self) -> None:
        self._metrics: dict[str, MetricDefinition] = {}
        self._by_name: dict[tuple[str, str], MetricDefinition] = {}
        self._list_all_calls: list[str] = []

    def add_metric(self, metric: MetricDefinition) -> None:
        self._metrics[(metric.workspace_id, metric.metric_id)] = metric
        key = (metric.workspace_id, metric.name.lower())
        self._by_name[key] = metric

    def get_by_id(
        self,
        workspace_id: str,
        metric_id: str,
    ) -> MetricDefinition | None:
        return self._metrics.get((workspace_id, metric_id))

    def get_by_name(
        self,
        workspace_id: str,
        name: str,
    ) -> MetricDefinition | None:
        return self._by_name.get((workspace_id, name.lower()))

    def list_all(self, workspace_id: str) -> list[MetricDefinition]:
        self._list_all_calls.append(workspace_id)
        return [
            m
            for m in self._metrics.values()
            if m.workspace_id == workspace_id
        ]

    def upsert(self, metric: MetricDefinition) -> MetricDefinition:
        self.add_metric(metric)
        return metric

    def delete(self, workspace_id: str, metric_id: str) -> bool:
        key = (workspace_id, metric_id)
        if key in self._metrics:
            metric = self._metrics[key]
            del self._metrics[key]
            name_key = (workspace_id, metric.name.lower())
            self._by_name.pop(name_key, None)
            return True
        return False


def _make_metric(
    workspace_id: str = "default",
    metric_id: str = "revenue",
    name: str = "Revenue",
    description: str = "Total revenue",
    formula: str = "SELECT SUM(amount) FROM orders",
    grain: str = "daily",
    allowed_dimensions: list[str] | None = None,
    default_filters: dict[str, str] | None = None,
    owner: str | None = "finance",
    certified: bool = True,
    source_tables: list[str] | None = None,
    join_policy: str | None = None,
    freshness_hours: float | None = 24.0,
    updated_at: datetime | None = None,
) -> MetricDefinition:
    now = datetime.now(tz=UTC)
    return MetricDefinition(
        workspace_id=workspace_id,
        metric_id=metric_id,
        name=name,
        description=description,
        formula=formula,
        grain=grain,
        allowed_dimensions=allowed_dimensions or ["date", "region", "product_category"],
        default_filters=default_filters or {},
        owner=owner,
        certified=certified,
        source_tables=source_tables or ["db.warehouse.orders"],
        join_policy=join_policy,
        freshness_hours=freshness_hours,
        created_at=now,
        updated_at=updated_at or now,
    )


class TestGetById:
    def test_get_by_id_returns_definition(self) -> None:
        repository = _FakeMetricRepository()
        metric = _make_metric(metric_id="rev", name="Revenue")
        repository.add_metric(metric)
        service = MetricRegistryService(repository)

        result = service.get_by_id(workspace_id="default", metric_id="rev")

        assert result is not None
        assert result.metric_id == "rev"
        assert result.name == "Revenue"

    def test_get_by_id_not_found_returns_none(self) -> None:
        repository = _FakeMetricRepository()
        service = MetricRegistryService(repository)

        result = service.get_by_id(workspace_id="default", metric_id="nonexistent")

        assert result is None


class TestGetByName:
    def test_get_by_name_case_insensitive(self) -> None:
        repository = _FakeMetricRepository()
        metric = _make_metric(metric_id="rev", name="Revenue")
        repository.add_metric(metric)
        service = MetricRegistryService(repository)

        result = service.get_by_name(workspace_id="default", name="revenue")

        assert result is not None
        assert result.metric_id == "rev"


class TestFindByBusinessTerm:
    def test_find_by_business_term_discovery(self) -> None:
        repository = _FakeMetricRepository()
        metric = _make_metric(
            metric_id="rev",
            name="Revenue",
            description="Total money made from sales",
        )
        repository.add_metric(metric)
        service = MetricRegistryService(repository)

        results = service.find_by_business_term(term="how much money did we make", workspace_id="default")

        assert len(results) == 1
        assert results[0].metric_id == "rev"

    def test_find_by_business_term_multiple_matches_ranked(self) -> None:
        repository = _FakeMetricRepository()
        revenue = _make_metric(metric_id="rev", name="Revenue", description="Total revenue")
        gmv = _make_metric(metric_id="gmv", name="GMV", description="Gross merchandise value")
        churn = _make_metric(metric_id="churn", name="Churn Rate", description="Customer churn")
        repository.add_metric(revenue)
        repository.add_metric(gmv)
        repository.add_metric(churn)
        service = MetricRegistryService(repository)

        results = service.find_by_business_term(term="money revenue", workspace_id="default")

        assert len(results) >= 1
        assert results[0].name in ["Revenue", "GMV"]

    def test_find_by_business_term_no_match_returns_empty(self) -> None:
        repository = _FakeMetricRepository()
        metric = _make_metric(metric_id="rev", name="Revenue")
        repository.add_metric(metric)
        service = MetricRegistryService(repository)

        results = service.find_by_business_term(term="xyznonexistent123", workspace_id="default")

        assert results == []


class TestCompactCard:
    def test_compact_card_strips_formula_to_200_chars(self) -> None:
        repository = _FakeMetricRepository()
        long_formula = "SELECT " + "x" * 300
        metric = _make_metric(formula=long_formula)
        repository.add_metric(metric)
        service = MetricRegistryService(repository)

        card = service.get_compact_card(metric)

        assert len(card["formula_summary"]) <= 203  # 200 + "..."
        assert "..." in card["formula_summary"]

    def test_compact_card_preserves_certification_status(self) -> None:
        metric = _make_metric(certified=True)
        repository = _FakeMetricRepository()
        repository.add_metric(metric)
        service = MetricRegistryService(repository)

        card = service.get_compact_card(metric)

        assert card["certified"] is True

    def test_compact_card_minimal_for_new_metric(self) -> None:
        metric = _make_metric()
        repository = _FakeMetricRepository()
        repository.add_metric(metric)
        service = MetricRegistryService(repository)

        card = service.get_compact_card(metric)

        assert "metric_id" in card
        assert "name" in card
        assert "formula_summary" in card
        assert "grain" in card
        assert "allowed_dimensions" in card
        assert "source_refs" in card
        assert "certified" in card


class TestSearch:
    def test_search_case_insensitive_partial_match(self) -> None:
        repository = _FakeMetricRepository()
        metric = _make_metric(metric_id="rev", name="Revenue")
        repository.add_metric(metric)
        service = MetricRegistryService(repository)

        results = service.search(name_query="rev", workspace_id="default")

        assert len(results) == 1
        assert results[0].metric_id == "rev"


class TestAllowedDimensions:
    def test_get_allowed_dimensions_enforces_schema(self) -> None:
        metric = _make_metric(allowed_dimensions=["date", "region", "product_category"])
        repository = _FakeMetricRepository()
        repository.add_metric(metric)
        service = MetricRegistryService(repository)

        dimensions = service.get_allowed_dimensions(metric)

        assert set(dimensions) == {"date", "region", "product_category"}


class TestCertification:
    def test_is_certified_returns_false_when_not_certified(self) -> None:
        metric = _make_metric(certified=False)
        repository = _FakeMetricRepository()
        repository.add_metric(metric)
        service = MetricRegistryService(repository)

        assert service.is_certified(metric) is False


class TestFreshnessWarning:
    def test_freshness_warning_when_stale(self) -> None:
        metric = _make_metric(freshness_hours=24.0)
        stale_time = datetime.now(tz=UTC) - timedelta(hours=48)
        repository = _FakeMetricRepository()
        repository.add_metric(metric)
        service = MetricRegistryService(repository)

        warning = service.get_freshness_warning(metric, last_updated=stale_time)

        assert warning is not None
        assert "stale" in warning.lower()

    def test_freshness_warning_when_fresh(self) -> None:
        metric = _make_metric(freshness_hours=24.0)
        fresh_time = datetime.now(tz=UTC) - timedelta(hours=1)
        repository = _FakeMetricRepository()
        repository.add_metric(metric)
        service = MetricRegistryService(repository)

        warning = service.get_freshness_warning(metric, last_updated=fresh_time)

        assert warning is None


class TestUpsert:
    def test_upsert_returns_updated_metric(self) -> None:
        repository = _FakeMetricRepository()
        service = MetricRegistryService(repository)
        metric = _make_metric()

        result = service.upsert(metric)

        assert result.metric_id == metric.metric_id
        assert result.workspace_id == metric.workspace_id


class TestMetricConsistencyCheck:
    def test_preflight_checks_allowed_dimensions(self) -> None:
        metric = _make_metric(
            allowed_dimensions=["date", "region"],
            source_tables=["orders"],
        )
        repository = _FakeMetricRepository()
        repository.add_metric(metric)
        service = MetricRegistryService(repository)
        # Test with table-qualified disallowed column
        sql = "SELECT orders.unauthorized_column FROM orders"
        context_tables = ["orders"]
        metadata_columns: dict[str, set[str]] = {"orders": {"authorized_column"}}

        result = service.check_metric_consistency(
            sql=sql,
            metric=metric,
            context_tables=context_tables,
            metadata_table_columns=metadata_columns,
        )

        assert result.status == "fail"
        assert len(result.errors) >= 1

    def test_preflight_allows_valid_dimension(self) -> None:
        metric = _make_metric(
            allowed_dimensions=["date", "region"],
            source_tables=["orders"],
        )
        repository = _FakeMetricRepository()
        repository.add_metric(metric)
        service = MetricRegistryService(repository)
        # Use table-qualified columns for proper validation
        sql = "SELECT orders.date, orders.region FROM orders WHERE orders.date = '2024-01-01'"
        context_tables = ["orders"]
        metadata_columns: dict[str, set[str]] = {
            "orders": {"date", "region", "amount"}
        }

        result = service.check_metric_consistency(
            sql=sql,
            metric=metric,
            context_tables=context_tables,
            metadata_table_columns=metadata_columns,
        )

        assert result.status == "pass"

    def test_preflight_outside_source_tables(self) -> None:
        metric = _make_metric(
            allowed_dimensions=["date"],
            source_tables=["orders"],
        )
        repository = _FakeMetricRepository()
        repository.add_metric(metric)
        service = MetricRegistryService(repository)
        sql = "SELECT unauthorized.date FROM unauthorized_table"
        context_tables = ["orders", "unauthorized_table"]
        metadata_columns: dict[str, set[str]] = {
            "orders": {"date", "amount"},
            "unauthorized_table": {"date"},
        }

        result = service.check_metric_consistency(
            sql=sql,
            metric=metric,
            context_tables=context_tables,
            metadata_table_columns=metadata_columns,
        )

        assert result.status == "fail"
        assert any(
            "metric_source_table_violation" in err.code
            for err in result.errors
        )


class TestSimpleTtlCache:
    """Tests for the _SimpleTtlCache implementation."""

    def test_cache_stores_and_retrieves_value(self) -> None:
        cache = _SimpleTtlCache(ttl_seconds=60)
        cache.set("key1", {"data": "value1"})
        result = cache.get("key1")
        assert result == {"data": "value1"}

    def test_cache_returns_none_for_missing_key(self) -> None:
        cache = _SimpleTtlCache(ttl_seconds=60)
        result = cache.get("nonexistent")
        assert result is None

    def test_cache_ttl_expires(self) -> None:
        """Test cache expiration with TTL=0 (immediate expiration)."""
        import time as time_module
        # Create cache with 0 TTL - expires immediately
        cache = _SimpleTtlCache(ttl_seconds=0)
        cache.set("short_lived", "value")

        # Wait a tiny bit to ensure expiration
        time_module.sleep(0.01)

        # Should be expired immediately since TTL=0
        result = cache.get("short_lived")
        assert result is None

    def test_cache_invalidate_removes_entry(self) -> None:
        cache = _SimpleTtlCache(ttl_seconds=60)
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        cache.invalidate("key1")

        assert cache.get("key1") is None
        assert cache.get("key2") == "value2"

    def test_cache_clear_removes_all(self) -> None:
        cache = _SimpleTtlCache(ttl_seconds=60)
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        cache.clear()

        assert cache.get("key1") is None
        assert cache.get("key2") is None


class TestCacheIntegration:
    """Integration tests for caching behavior in MetricRegistryService."""

    def test_list_all_uses_cache(self) -> None:
        """Verify that repeated list_all calls use the cache."""
        repository = _FakeMetricRepository()
        metric = _make_metric(workspace_id="ws1")
        repository.add_metric(metric)
        service = MetricRegistryService(repository, cache_ttl_seconds=300)

        # First call should hit repository
        result1 = service.list_all("ws1")
        assert len(result1) == 1

        # Second call should use cache (no additional repository call)
        result2 = service.list_all("ws1")
        assert len(result2) == 1
        # The fake repository tracks list_all calls
        assert len(repository._list_all_calls) == 1  # Only one actual call

    def test_cache_ttl_invalidation_on_upsert(self) -> None:
        """Verify cache is invalidated after upsert."""
        repository = _FakeMetricRepository()
        metric = _make_metric(workspace_id="ws1")
        repository.add_metric(metric)
        service = MetricRegistryService(repository, cache_ttl_seconds=300)

        # Populate cache
        service.list_all("ws1")
        initial_calls = len(repository._list_all_calls)

        # Upsert should invalidate cache
        service.upsert(metric)
        service.list_all("ws1")

        # Should have made another call after invalidation
        assert len(repository._list_all_calls) == initial_calls + 1


class TestCrossWorkspaceIsolation:
    """Tests for workspace isolation in metric registry."""

    def test_workspace_a_metric_not_visible_to_workspace_b(self) -> None:
        """Verify metrics in workspace A are not visible to workspace B."""
        repository = _FakeMetricRepository()

        # Create metric in workspace A
        metric_a = _make_metric(
            workspace_id="workspace_a",
            metric_id="metric-a",
            name="Workspace A Revenue",
        )
        repository.add_metric(metric_a)

        # Create metric in workspace B
        metric_b = _make_metric(
            workspace_id="workspace_b",
            metric_id="metric-b",
            name="Workspace B Revenue",
        )
        repository.add_metric(metric_b)

        service = MetricRegistryService(repository)

        # List all for workspace A should only return A's metric
        metrics_a = service.list_all("workspace_a")
        assert len(metrics_a) == 1
        assert metrics_a[0].metric_id == "metric-a"
        assert metrics_a[0].workspace_id == "workspace_a"

        # List all for workspace B should only return B's metric
        metrics_b = service.list_all("workspace_b")
        assert len(metrics_b) == 1
        assert metrics_b[0].metric_id == "metric-b"
        assert metrics_b[0].workspace_id == "workspace_b"

    def test_get_by_id_workspace_isolation(self) -> None:
        """Verify get_by_id is workspace-scoped."""
        repository = _FakeMetricRepository()

        metric_a = _make_metric(
            workspace_id="workspace_a",
            metric_id="shared-id",
            name="Workspace A Metric",
        )
        repository.add_metric(metric_a)

        metric_b = _make_metric(
            workspace_id="workspace_b",
            metric_id="shared-id",
            name="Workspace B Metric",
        )
        repository.add_metric(metric_b)

        service = MetricRegistryService(repository)

        # Should find A's metric in workspace A
        found_a = service.get_by_id("workspace_a", "shared-id")
        assert found_a is not None
        assert found_a.name == "Workspace A Metric"

        # Should find B's metric in workspace B
        found_b = service.get_by_id("workspace_b", "shared-id")
        assert found_b is not None
        assert found_b.name == "Workspace B Metric"

        # Should not find in wrong workspace
        not_found = service.get_by_id("workspace_c", "shared-id")
        assert not_found is None

    def test_find_by_business_term_workspace_isolation(self) -> None:
        """Verify business term search is workspace-scoped."""
        repository = _FakeMetricRepository()

        metric_a = _make_metric(
            workspace_id="workspace_a",
            metric_id="rev-a",
            name="Revenue",
            description="Total revenue for A",
        )
        repository.add_metric(metric_a)

        metric_b = _make_metric(
            workspace_id="workspace_b",
            metric_id="rev-b",
            name="Revenue",
            description="Total revenue for B",
        )
        repository.add_metric(metric_b)

        service = MetricRegistryService(repository)

        # Should only find A's revenue in workspace A
        results_a = service.find_by_business_term("revenue", "workspace_a")
        assert len(results_a) == 1
        assert results_a[0].metric_id == "rev-a"
        assert results_a[0].workspace_id == "workspace_a"

        # Should only find B's revenue in workspace B
        results_b = service.find_by_business_term("revenue", "workspace_b")
        assert len(results_b) == 1
        assert results_b[0].metric_id == "rev-b"
        assert results_b[0].workspace_id == "workspace_b"

    def test_cache_per_workspace(self) -> None:
        """Verify caches are workspace-specific."""
        repository = _FakeMetricRepository()
        service = MetricRegistryService(repository, cache_ttl_seconds=300)

        # Add different metrics to different workspaces
        metric_a = _make_metric(workspace_id="ws_a", metric_id="m-a")
        metric_b = _make_metric(workspace_id="ws_b", metric_id="m-b")
        repository.add_metric(metric_a)
        repository.add_metric(metric_b)

        # Populate caches for both workspaces
        service.list_all("ws_a")
        service.list_all("ws_b")

        # Both caches populated with single call each
        assert repository._list_all_calls == ["ws_a", "ws_b"]

        # Subsequent calls use cache
        service.list_all("ws_a")
        service.list_all("ws_b")
        # No new calls
        assert len(repository._list_all_calls) == 2

