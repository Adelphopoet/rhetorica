from __future__ import annotations

from dataclasses import dataclass, field

import plotly.graph_objects as go
import pytest
from rhetorica_backend.contracts.execution import QueryPreview
from rhetorica_backend.contracts.visualization import (
    VisualizationDataSource,
    VisualizationEncoding,
    VisualizationGoal,
    VisualizationKind,
    VisualizationRationale,
    VisualizationSpec,
    VisualizationTransform,
)
from rhetorica_backend.domain.exports.chart_renderer import (
    ChartRenderRequest,
    PlotlyChartRenderer,
)

_PNG = b"\x89PNG\r\n\x1a\nrendered"


@dataclass
class _CapturingExporter:
    figures: list[go.Figure] = field(default_factory=list)

    def export(self, figure: go.Figure) -> bytes:
        self.figures.append(figure)
        return _PNG


def _preview() -> QueryPreview:
    return QueryPreview(
        columns=["category", "x_value", "value", "other", "series", "size", "heat_y"],
        rows=[
            ["Very long category label 01", 1, 10, 4, "Actual", 16, "North"],
            ["Very long category label 02", 2, 18, 8, "Plan", 28, "South"],
            ["Very long category label 03", 3, 14, 12, "Actual", 22, "West"],
        ],
    )


def _spec(kind: VisualizationKind) -> VisualizationSpec:
    encoding = VisualizationEncoding(x="category", y=["value"])
    if kind in {VisualizationKind.SCATTER, VisualizationKind.BUBBLE}:
        encoding.x = "x_value"
    if kind == VisualizationKind.BUBBLE:
        encoding.size = "size"
        encoding.color = "series"
    if kind == VisualizationKind.HEATMAP:
        encoding.color = "heat_y"
    if kind == VisualizationKind.COMBO_LINE_BAR:
        encoding.secondary_y = ["other"]
    return VisualizationSpec(
        kind=kind,
        goal=VisualizationGoal.COMPARISON,
        title=f"{kind.value} quality snapshot",
        summary="Rendered from the governed execution preview.",
        chart_safe_on_preview=True,
        x_axis_label="Category",
        y_axis_label="Value",
        data_source=VisualizationDataSource(
            execution_id="exec-1",
            row_count=3,
            is_truncated=False,
        ),
        encoding=encoding,
        transform=VisualizationTransform(limit=50),
        rationale=VisualizationRationale(why_kind="Exact declared chart kind."),
    )


@pytest.mark.parametrize(
    ("kind", "trace_types"),
    [
        (VisualizationKind.BAR, ["bar"]),
        (VisualizationKind.BAR_GROUPED, ["bar"]),
        (VisualizationKind.BAR_STACKED, ["bar"]),
        (VisualizationKind.LINE, ["scatter"]),
        (VisualizationKind.AREA, ["scatter"]),
        (VisualizationKind.AREA_STACKED, ["scatter"]),
        (VisualizationKind.COMBO_LINE_BAR, ["bar", "scatter"]),
        (VisualizationKind.SCATTER, ["scatter"]),
        (VisualizationKind.BUBBLE, ["scatter", "scatter"]),
        (VisualizationKind.PIE, ["pie"]),
        (VisualizationKind.DONUT, ["pie"]),
        (VisualizationKind.HEATMAP, ["heatmap"]),
        (VisualizationKind.TREEMAP, ["treemap"]),
        (VisualizationKind.FUNNEL, ["funnel"]),
        (VisualizationKind.HISTOGRAM, ["bar"]),
        (VisualizationKind.WATERFALL, ["waterfall"]),
    ],
)
def test_renderer_compiles_every_declared_png_chart_to_its_native_trace(
    kind: VisualizationKind,
    trace_types: list[str],
) -> None:
    exporter = _CapturingExporter()
    renderer = PlotlyChartRenderer(exporter=exporter)

    content = renderer.render(
        ChartRenderRequest(spec=_spec(kind), preview=_preview(), fallback_title="Fallback")
    )

    assert content == _PNG
    assert len(exporter.figures) == 1
    assert [trace.type for trace in exporter.figures[0].data] == trace_types


def test_renderer_keeps_all_categories_and_delegates_label_layout_to_plotly() -> None:
    preview = QueryPreview(
        columns=["category", "value"],
        rows=[[f"Extremely long category name {index:02d}", index] for index in range(1, 41)],
    )
    exporter = _CapturingExporter()
    renderer = PlotlyChartRenderer(exporter=exporter)

    content = renderer.render(
        ChartRenderRequest(
            spec=_spec(VisualizationKind.BAR),
            preview=preview,
            fallback_title="Fallback",
        )
    )

    assert content == _PNG
    figure = exporter.figures[0]
    assert len(figure.data[0].x) == 40
    assert figure.layout.xaxis.automargin is True
    assert figure.layout.xaxis.tickmode == "auto"
    assert figure.layout.xaxis.nticks == 14
    assert figure.layout.xaxis.tickangle == -40


def test_renderer_keeps_legend_in_a_right_column_below_title_area() -> None:
    preview = QueryPreview(
        columns=["category", "value", "series"],
        rows=[
            [f"Category {index:02d}", index, f"Series {index:02d}"]
            for index in range(1, 19)
        ],
    )
    spec = _spec(VisualizationKind.BAR)
    spec.encoding.series = "series"
    exporter = _CapturingExporter()
    renderer = PlotlyChartRenderer(exporter=exporter)

    content = renderer.render(
        ChartRenderRequest(spec=spec, preview=preview, fallback_title="Fallback")
    )

    assert content == _PNG
    legend = exporter.figures[0].layout.legend
    assert legend.orientation == "v"
    assert legend.xref == "container"
    assert legend.x == 0.995
    assert legend.xanchor == "right"
    assert legend.yref == "paper"
    assert legend.y == 1.0
    assert legend.yanchor == "top"
    assert legend.itemsizing == "constant"


@pytest.mark.parametrize("kind", [VisualizationKind.PIE, VisualizationKind.DONUT])
def test_renderer_hides_tiny_sector_labels_instead_of_moving_them_over_title(
    kind: VisualizationKind,
) -> None:
    preview = QueryPreview(
        columns=["category", "value"],
        rows=[
            ["DELIVERY_EXPRESS", 677_000],
            ["DELIVERY_SELF", 149_000],
            ["TAKEAWAY", 136_000],
            ["DELIVERY_SLOT", 35_100],
            ["planned", 2_240],
            ["pickup", 298],
            ["dbs", 136],
            ["asap", 44],
        ],
    )
    exporter = _CapturingExporter()
    renderer = PlotlyChartRenderer(exporter=exporter)

    content = renderer.render(
        ChartRenderRequest(
            spec=_spec(kind),
            preview=preview,
            fallback_title="Fallback",
        )
    )

    assert content == _PNG
    figure = exporter.figures[0]
    assert figure.data[0].textposition == "inside"
    assert figure.layout.uniformtext.minsize == 13
    assert figure.layout.uniformtext.mode == "hide"


def test_renderer_renders_zero_and_negative_values_instead_of_dropping_chart() -> None:
    preview = QueryPreview(
        columns=["category", "value"],
        rows=[["zero", 0], ["loss", -25], ["gain", 10]],
    )
    exporter = _CapturingExporter()
    renderer = PlotlyChartRenderer(exporter=exporter)

    content = renderer.render(
        ChartRenderRequest(
            spec=_spec(VisualizationKind.WATERFALL),
            preview=preview,
            fallback_title="Fallback",
        )
    )

    assert content == _PNG
    assert list(exporter.figures[0].data[0].y) == [0.0, -25.0, 10.0]


def test_renderer_rejects_invalid_encoding_without_changing_chart_kind() -> None:
    exporter = _CapturingExporter()
    renderer = PlotlyChartRenderer(exporter=exporter)
    spec = _spec(VisualizationKind.PIE)
    spec.encoding.x = "missing_column"

    content = renderer.render(
        ChartRenderRequest(spec=spec, preview=_preview(), fallback_title="Fallback")
    )

    assert content is None
    assert exporter.figures == []
