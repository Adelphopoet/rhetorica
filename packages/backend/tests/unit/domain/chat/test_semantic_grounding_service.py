"""Tests for MetadataSemanticGroundingService."""

from rhetorica_backend.domain.chat.metadata.models import TableContext
from rhetorica_backend.domain.chat.metadata.semantic_grounding_service import (
    MetadataSemanticGroundingService,
)


class TestBuildSemanticGroundingPayload:
    """Tests for build_semantic_grounding_payload method."""

    def _make_table(self, fqn: str, relevance_score: float = 50.0, **kwargs) -> TableContext:
        """Helper to create TableContext for tests."""
        return TableContext(
            source="mcp",
            fqn=fqn,
            columns=["col1", "col2"],
            description="Test table",
            relevance_score=relevance_score,
            **kwargs,
        )

    def test_explicit_chosen_table_fqn_returns_it(self):
        """Explicit chosen_table_fqn is used and explicit_choice is True."""
        tables = [
            self._make_table("svc.db.schema.table1", relevance_score=80.0),
            self._make_table("svc.db.schema.table2", relevance_score=70.0),
        ]

        result = MetadataSemanticGroundingService.build_semantic_grounding_payload(
            tables=tables,
            chosen_table_fqn="svc.db.schema.table1",
        )

        assert result is not None
        assert result["chosen_table_fqn"] == "svc.db.schema.table1"
        assert result["explicit_choice"] is True
        assert len(result["candidates"]) == 2

    def test_single_candidate_returns_it_without_explicit_choice(self):
        """Single candidate without explicit choice returns that candidate with explicit_choice=False."""
        tables = [
            self._make_table("svc.db.schema.solo_table", relevance_score=90.0),
        ]

        result = MetadataSemanticGroundingService.build_semantic_grounding_payload(
            tables=tables,
        )

        assert result is not None
        assert result["chosen_table_fqn"] == "svc.db.schema.solo_table"
        assert result["explicit_choice"] is False
        assert len(result["candidates"]) == 1

    def test_multiple_candidates_no_explicit_returns_none(self):
        """Multiple candidates without explicit choice returns None (ambiguous)."""
        tables = [
            self._make_table("svc.db.schema.table_a", relevance_score=80.0),
            self._make_table("svc.db.schema.table_b", relevance_score=75.0),
        ]

        result = MetadataSemanticGroundingService.build_semantic_grounding_payload(
            tables=tables,
        )

        assert result is not None
        assert result["chosen_table_fqn"] is None
        assert result["explicit_choice"] is False
        assert len(result["candidates"]) == 2

    def test_empty_tables_and_no_chosen_returns_none(self):
        """Empty tables with no chosen_table_fqn returns None."""
        result = MetadataSemanticGroundingService.build_semantic_grounding_payload(
            tables=[],
        )

        assert result is None

    def test_explicit_choice_takes_precedence_over_single_candidate(self):
        """When both explicit and single candidate exist, explicit wins."""
        tables = [
            self._make_table("svc.db.schema.table_a", relevance_score=80.0),
        ]

        result = MetadataSemanticGroundingService.build_semantic_grounding_payload(
            tables=tables,
            chosen_table_fqn="svc.db.schema.explicit_table",
        )

        assert result is not None
        assert result["chosen_table_fqn"] == "svc.db.schema.explicit_table"
        assert result["explicit_choice"] is True

    def test_selection_rationale_none_when_chosen_is_none(self):
        """selection_rationale is None when chosen_table_fqn is None (ambiguous case)."""
        tables = [
            self._make_table("svc.db.schema.table_a", relevance_score=80.0),
            self._make_table("svc.db.schema.table_b", relevance_score=75.0),
        ]

        result = MetadataSemanticGroundingService.build_semantic_grounding_payload(
            tables=tables,
        )

        assert result is not None
        assert result["selection_rationale"] is None
        assert result["explicit_choice"] is False

    def test_lineage_summary_preserved(self):
        """lineage_summary is preserved in the payload."""
        tables = [
            self._make_table("svc.db.schema.table1"),
        ]

        result = MetadataSemanticGroundingService.build_semantic_grounding_payload(
            tables=tables,
            lineage_summary="Upstream: src.table1 -> this.table1",
        )

        assert result is not None
        assert result["lineage_summary"] == "Upstream: src.table1 -> this.table1"

    def test_three_candidates_limited_to_top_three(self):
        """Only top 3 candidates are included in payload."""
        tables = [
            self._make_table(f"svc.db.schema.table{i}", relevance_score=100.0 - i * 10)
            for i in range(5)
        ]

        result = MetadataSemanticGroundingService.build_semantic_grounding_payload(
            tables=tables,
        )

        assert result is not None
        assert len(result["candidates"]) == 3


class TestIsSuspiciousTable:
    """Tests for is_suspicious_table method."""

    def test_suspicious_scope_in_fqn(self):
        """Table with suspicious scope token in fqn is flagged."""
        table = TableContext(
            source="mcp",
            fqn="clickhouse.analytics.sandbox.temp_sales",
            columns=["col1"],
        )
        assert MetadataSemanticGroundingService.is_suspicious_table(table) is True

    def test_clean_table_not_flagged(self):
        """Clean table without suspicious tokens is not flagged."""
        table = TableContext(
            source="mcp",
            fqn="clickhouse.analytics.dm.sales_orders",
            columns=["col1"],
        )
        assert MetadataSemanticGroundingService.is_suspicious_table(table) is False


class TestSemanticReasonCodes:
    """Tests for semantic_reason_codes method."""

    def test_governance_signals_recognized(self):
        """Tables with governance attributes get governance_strength reason code."""
        table = TableContext(
            source="mcp",
            fqn="svc.db.schema.governed_table",
            columns=["col1"],
            certification="Certification.Gold",
            tier="Tier.Tier1",
            owners=["analytics-team"],
            domains=["sales"],
            usage_count=100,
        )

        reason_codes = MetadataSemanticGroundingService.semantic_reason_codes(table)
        assert "governance_strength" in reason_codes

    def test_business_layer_preference_recognized(self):
        """Tables matched on business_schema get business_layer_preference reason code."""
        table = TableContext(
            source="mcp",
            fqn="svc.db.schema.table",
            columns=["col1"],
            matched_on=["name_exact", "business_schema"],
        )

        reason_codes = MetadataSemanticGroundingService.semantic_reason_codes(table)
        assert "business_layer_preference" in reason_codes
