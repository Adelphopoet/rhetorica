from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.execution import ExecuteSqlResponse, QueryPreview
from rhetorica_backend.contracts.visualization import (
    VisualizationAggregation,
    VisualizationGoal,
    VisualizationKind,
    VisualizationValueFormat,
)
from rhetorica_backend.domain.results.visualization_planner import VisualizationPlanner


def _execution(
    columns: list[str],
    rows: list[list[Any]],
    *,
    row_count: int | None = None,
    applied_row_limit: int | None = None,
) -> ExecuteSqlResponse:
    return ExecuteSqlResponse(
        execution_id="exec-1",
        status="succeeded",
        engine="clickhouse",
        normalized_sql="SELECT 1",
        applied_row_limit=applied_row_limit,
        row_count=row_count if row_count is not None else len(rows),
        warnings=[],
        violations=[],
        preview=QueryPreview(columns=columns, rows=rows),
    )


def test_plan_no_llm_candidate_returns_none() -> None:
    """Without LLM candidate, planner must not emit visualization."""
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["dt", "revenue"],
        rows=[
            ["2026-03-01", 10.0],
            ["2026-03-02", 15.0],
            ["2026-03-03", 18.0],
        ],
    )

    spec = planner.plan(execution=execution)

    assert spec is None


def test_plan_with_llm_candidate_returns_chart() -> None:
    """When LLM candidate is provided, it should be validated and used if valid."""
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["dt", "revenue"],
        rows=[
            ["2026-03-01", 10.0],
            ["2026-03-02", 15.0],
            ["2026-03-03", 18.0],
        ],
    )

    spec = planner.plan(
        execution=execution,
        llm_candidate={
            "version": "v2",
            "kind": "line",
            "title": "Revenue over time",
            "encoding": {
                "x": "dt",
                "y": ["revenue"],
            },
            "transform": {"sort": "asc", "limit": 50, "aggregation": "none"},
            "warnings": [],
            "rationale": {"why_kind": "LLM requested line chart", "why_fields": []},
            "data_source": {
                "type": "execution_preview",
                "execution_id": execution.execution_id,
                "row_count": 3,
                "is_truncated": False,
            },
        },
    )

    assert spec is not None
    assert spec.kind == VisualizationKind.LINE
    assert spec.planner_mode == "llm_validated"
    assert spec.encoding.x == "dt"
    assert spec.encoding.y == ["revenue"]


def test_plan_no_llm_candidate_returns_none_even_for_chartable_data() -> None:
    """Even chartable data yields no visualization without an explicit LLM candidate."""
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["country", "orders"],
        rows=[
            ["RU", 120],
            ["DE", 80],
            ["US", 150],
        ],
    )

    spec = planner.plan(execution=execution)

    assert spec is None


def test_plan_without_llm_returns_none_for_time_metric_data() -> None:
    """Without LLM candidate, planner returns no visualization for time-series data."""
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["dt", "revenue"],
        rows=[
            ["2026-03-01", 10.0],
            ["2026-03-02", 15.0],
            ["2026-03-03", 18.0],
        ],
    )

    spec = planner.plan(execution=execution)

    assert spec is None


def test_plan_llm_candidate_line_for_time_metric() -> None:
    """When LLM provides chart candidate, it should be used if valid."""
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["dt", "revenue"],
        rows=[
            ["2026-03-01", 10.0],
            ["2026-03-02", 15.0],
            ["2026-03-03", 18.0],
        ],
    )

    spec = planner.plan(
        execution=execution,
        llm_candidate={
            "version": "v2",
            "kind": "line",
            "title": "Revenue over time",
            "encoding": {
                "x": "dt",
                "y": ["revenue"],
            },
            "transform": {"sort": "asc", "limit": 50, "aggregation": "none"},
            "warnings": [],
            "rationale": {"why_kind": "LLM requested line chart", "why_fields": []},
            "data_source": {
                "type": "execution_preview",
                "execution_id": execution.execution_id,
                "row_count": 3,
                "is_truncated": False,
            },
        },
    )

    assert spec is not None
    assert spec.kind == VisualizationKind.LINE
    assert spec.goal == VisualizationGoal.INSPECTION
    assert spec.encoding.x == "dt"
    assert spec.encoding.y == ["revenue"]
    assert spec.value_format == VisualizationValueFormat.AUTO
    assert spec.planner_mode == "llm_validated"


def test_plan_without_llm_returns_none_for_dimension_metric_data() -> None:
    """Without LLM candidate, planner returns no visualization for dimension+metric data."""
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["country", "orders"],
        rows=[
            ["RU", 120],
            ["DE", 80],
            ["US", 150],
        ],
    )

    spec = planner.plan(execution=execution)

    assert spec is None


def test_plan_llm_candidate_bar_for_dimension_metric() -> None:
    """When LLM provides BAR candidate, it should be used if valid."""
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["country", "orders"],
        rows=[
            ["RU", 120],
            ["DE", 80],
            ["US", 150],
        ],
    )

    spec = planner.plan(
        execution=execution,
        llm_candidate={
            "version": "v2",
            "kind": "bar",
            "title": "Orders by country",
            "encoding": {
                "x": "country",
                "y": ["orders"],
            },
            "transform": {"sort": "desc", "limit": 50, "aggregation": "none"},
            "warnings": [],
            "rationale": {"why_kind": "LLM requested bar chart", "why_fields": []},
            "data_source": {
                "type": "execution_preview",
                "execution_id": execution.execution_id,
                "row_count": 3,
                "is_truncated": False,
            },
        },
    )

    assert spec is not None
    assert spec.kind == VisualizationKind.BAR
    assert spec.goal == VisualizationGoal.INSPECTION
    assert spec.encoding.x == "country"
    assert spec.encoding.y == ["orders"]
    assert spec.value_format == VisualizationValueFormat.AUTO
    assert spec.planner_mode == "llm_validated"


def test_plan_without_llm_returns_none_for_two_metrics_data() -> None:
    """Two metrics without a clear dimension still return no fallback."""
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["avg_check", "retention_rate"],
        rows=[
            [500.0, 0.31],
            [620.0, 0.35],
            [580.0, 0.33],
        ],
    )

    spec = planner.plan(execution=execution)

    assert spec is None


def test_plan_llm_candidate_scatter_for_two_metrics() -> None:
    """When LLM provides SCATTER candidate, it should be used if valid."""
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["avg_check", "retention_rate"],
        rows=[
            [500.0, 0.31],
            [620.0, 0.35],
            [580.0, 0.33],
        ],
    )

    spec = planner.plan(
        execution=execution,
        llm_candidate={
            "version": "v2",
            "kind": "scatter",
            "title": "Check vs Retention",
            "encoding": {
                "x": "avg_check",
                "y": ["retention_rate"],
            },
            "transform": {"sort": None, "limit": 50, "aggregation": "none"},
            "warnings": [],
            "rationale": {"why_kind": "LLM requested scatter plot", "why_fields": []},
            "data_source": {
                "type": "execution_preview",
                "execution_id": execution.execution_id,
                "row_count": 3,
                "is_truncated": False,
            },
        },
    )

    assert spec is not None
    assert spec.kind == VisualizationKind.SCATTER
    assert spec.goal == VisualizationGoal.INSPECTION
    assert spec.encoding.x == "avg_check"
    assert spec.encoding.y == ["retention_rate"]
    assert spec.value_format == VisualizationValueFormat.AUTO
    assert spec.planner_mode == "llm_validated"


def test_planner_returns_none_on_invalid_llm_spec() -> None:
    """Invalid LLM candidate should be dropped instead of fallback visualization."""
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["country", "orders"],
        rows=[
            ["RU", 120],
            ["DE", 80],
        ],
    )

    spec = planner.plan(
        execution=execution,
        llm_candidate={
            "version": "v1",
            "kind": "pie",
            "title": "Broken pie",
            "planner_mode": "llm_validated",
            "data_source": {
                "type": "execution_preview",
                "execution_id": "wrong",
                "row_count": 2,
                "is_truncated": False,
            },
            "encoding": {
                "x": "country",
                "y": ["orders", "extra_metric"],
                "series": None,
            },
            "transform": {"sort": "desc", "limit": 50, "aggregation": "none"},
            "warnings": [],
            "rationale": {"why_kind": "broken", "why_fields": []},
        },
    )

    assert spec is None


def test_planner_returns_none_on_llm_parse_failure() -> None:
    """Malformed LLM candidate should not produce visualization."""
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["dt", "revenue"],
        rows=[
            ["2026-03-01", 10.0],
            ["2026-03-02", 15.0],
        ],
    )

    # Provide a malformed candidate with invalid kind enum value
    # that causes ValidationError in model_validate
    spec = planner.plan(
        execution=execution,
        llm_candidate={
            "version": "v2",
            "kind": "invalid_chart_kind",  # Invalid enum value causes parse failure
            "title": "Broken spec",
            "encoding": {"x": "dt", "y": ["revenue"]},
            "transform": {"sort": None, "limit": 50, "aggregation": "none"},
            "warnings": [],
            "rationale": {"why_kind": "test", "why_fields": []},
            "data_source": {
                "type": "execution_preview",
                "execution_id": execution.execution_id,
                "row_count": 2,
                "is_truncated": False,
            },
        },
    )

    assert spec is None


def test_plan_without_llm_returns_none_on_truncated_preview() -> None:
    """Without LLM candidate, truncated preview still returns no visualization."""
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["country", "orders"],
        rows=[
            ["RU", 120],
            ["DE", 80],
            ["US", 70],
        ],
        row_count=5000,
        applied_row_limit=5000,
    )

    spec = planner.plan(execution=execution)

    assert spec is None


def test_plan_without_llm_returns_none_for_high_cardinality_dimension() -> None:
    """Without LLM candidate, high cardinality data still yields no visualization."""
    planner = VisualizationPlanner()
    rows = [[f"item_{index}", index] for index in range(100)]
    execution = _execution(
        columns=["item_name", "orders"],
        rows=rows,
    )

    spec = planner.plan(execution=execution)

    assert spec is None


def test_plan_without_llm_returns_none_for_percent_data() -> None:
    """Without LLM candidate, percent data still yields no visualization."""
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["week_start", "conversion_rate"],
        rows=[
            ["2026-03-01", 0.121],
            ["2026-03-08", 0.135],
            ["2026-03-15", 0.118],
        ],
    )

    spec = planner.plan(execution=execution)

    assert spec is None


def test_plan_llm_candidate_line_with_percent_formatting() -> None:
    """When LLM provides LINE candidate with percent data, formatting should be applied."""
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["week_start", "conversion_rate"],
        rows=[
            ["2026-03-01", 0.121],
            ["2026-03-08", 0.135],
            ["2026-03-15", 0.118],
        ],
    )

    spec = planner.plan(
        execution=execution,
        llm_candidate={
            "version": "v2",
            "kind": "line",
            "title": "Conversion Rate Over Time",
            "encoding": {
                "x": "week_start",
                "y": ["conversion_rate"],
            },
            "transform": {"sort": "asc", "limit": 50, "aggregation": "none"},
            "warnings": [],
            "rationale": {"why_kind": "LLM requested line chart for trends", "why_fields": []},
            "data_source": {
                "type": "execution_preview",
                "execution_id": execution.execution_id,
                "row_count": 3,
                "is_truncated": False,
            },
        },
    )

    assert spec is not None
    assert spec.kind == VisualizationKind.LINE
    assert spec.value_format == VisualizationValueFormat.AUTO
    assert spec.annotations == []


def test_planner_accepts_every_advertised_rendered_kind() -> None:
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["x", "series", "metric", "secondary", "size"],
        rows=[
            ["A", "new", 10.0, 8.0, 12.0],
            ["B", "new", 16.0, 12.0, 18.0],
            ["A", "returning", 7.0, 9.0, 10.0],
        ],
    )

    candidates: dict[VisualizationKind, dict[str, Any]] = {
        VisualizationKind.BAR: {"x": "x", "y": ["metric"]},
        VisualizationKind.BAR_GROUPED: {"x": "x", "y": ["metric"], "series": "series"},
        VisualizationKind.BAR_STACKED: {"x": "x", "y": ["metric"], "series": "series"},
        VisualizationKind.LINE: {"x": "x", "y": ["metric"]},
        VisualizationKind.AREA: {"x": "x", "y": ["metric"]},
        VisualizationKind.AREA_STACKED: {"x": "x", "y": ["metric"], "series": "series"},
        VisualizationKind.COMBO_LINE_BAR: {
            "x": "x",
            "y": ["metric"],
            "secondary_y": ["secondary"],
        },
        VisualizationKind.SCATTER: {"x": "secondary", "y": ["metric"]},
        VisualizationKind.BUBBLE: {"x": "secondary", "y": ["metric"], "size": "size"},
        VisualizationKind.PIE: {"x": "x", "y": ["metric"]},
        VisualizationKind.DONUT: {"x": "x", "y": ["metric"]},
        VisualizationKind.HEATMAP: {"x": "x", "y": ["metric"], "color": "series"},
        VisualizationKind.TREEMAP: {"x": "x", "y": ["metric"]},
        VisualizationKind.FUNNEL: {"x": "x", "y": ["metric"]},
        VisualizationKind.HISTOGRAM: {"x": "x", "y": ["metric"]},
        VisualizationKind.WATERFALL: {"x": "x", "y": ["metric"]},
    }

    for kind, encoding in candidates.items():
        spec = planner.plan(
            execution=execution,
            llm_candidate={
                "version": "v2",
                "kind": kind.value,
                "title": f"{kind.value} chart",
                "encoding": encoding,
                "transform": {"sort": None, "limit": 50, "aggregation": "none"},
                "warnings": [],
                "rationale": {"why_kind": f"LLM requested {kind.value}", "why_fields": []},
                "data_source": {
                    "type": "execution_preview",
                    "execution_id": execution.execution_id,
                    "row_count": 3,
                    "is_truncated": False,
                },
            },
        )

        assert spec is not None, kind.value
        assert spec.kind == kind


def test_planner_allows_explicit_aggregation_transform() -> None:
    planner = VisualizationPlanner()
    execution = _execution(
        columns=["country", "orders"],
        rows=[
            ["RU", 120],
            ["RU", 80],
            ["DE", 70],
        ],
    )

    spec = planner.plan(
        execution=execution,
        llm_candidate={
            "version": "v2",
            "kind": "bar",
            "title": "Orders by country",
            "encoding": {
                "x": "country",
                "y": ["orders"],
            },
            "transform": {"sort": "desc", "limit": 50, "aggregation": "sum"},
            "warnings": [],
            "rationale": {"why_kind": "LLM requested grouped preview aggregation", "why_fields": []},
            "data_source": {
                "type": "execution_preview",
                "execution_id": execution.execution_id,
                "row_count": 3,
                "is_truncated": False,
            },
        },
    )

    assert spec is not None
    assert spec.transform.aggregation == VisualizationAggregation.SUM
