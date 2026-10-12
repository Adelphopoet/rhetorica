from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from html import escape
from typing import Any, Protocol, cast

import plotly.graph_objects as go  # type: ignore[import-untyped]
import plotly.io as pio  # type: ignore[import-untyped]
from rhetorica_backend.contracts.execution import QueryPreview
from rhetorica_backend.contracts.visualization import (
    VisualizationAggregation,
    VisualizationAnnotation,
    VisualizationKind,
    VisualizationSort,
    VisualizationSpec,
    VisualizationValueFormat,
)
from rhetorica_backend.infra.logging import get_logger

logger = get_logger(__name__)

type PlotlyFigure = Any
type PlotlyTrace = Any

_PALETTE = (
    "#2563EB",
    "#0F9D7A",
    "#E69F00",
    "#D64F70",
    "#7559D9",
    "#0284C7",
    "#65A30D",
    "#C2410C",
)


@dataclass(frozen=True)
class ChartRenderRequest:
    """Typed input for rendering a governed visualization snapshot."""

    spec: VisualizationSpec
    preview: QueryPreview
    fallback_title: str


class ChartRenderer(Protocol):
    """Renders one governed chart without changing its declared kind."""

    def render(self, request: ChartRenderRequest) -> bytes | None: ...


class StaticImageExporter(Protocol):
    """Converts an already-built figure to a static image."""

    def export(self, figure: PlotlyFigure) -> bytes: ...


class PlotlyFigureBuilder(Protocol):
    """Builds one Plotly figure family from validated rows."""

    def build(self, dataset: ChartDataset, spec: VisualizationSpec) -> PlotlyFigure: ...


class ChartDataError(ValueError):
    """Raised when declared chart encodings cannot be rendered faithfully."""


@dataclass(frozen=True)
class ChartDataset:
    """Immutable column-addressable preview used by figure builders."""

    columns: tuple[str, ...]
    rows: tuple[Mapping[str, object], ...]

    @classmethod
    def from_preview(cls, preview: QueryPreview) -> ChartDataset:
        return cls(
            columns=tuple(preview.columns),
            rows=tuple(
                {
                    column: row[index] if index < len(row) else None
                    for index, column in enumerate(preview.columns)
                }
                for row in preview.rows
            ),
        )

    def require_fields(self, fields: Iterable[str | None]) -> None:
        required = {field for field in fields if field}
        missing = sorted(required.difference(self.columns))
        if missing:
            raise ChartDataError(f"chart_fields_missing:{','.join(missing)}")

    def values(self, field: str) -> list[object]:
        self.require_fields([field])
        return [row[field] for row in self.rows]

    def labels(self, field: str) -> list[str]:
        return [_stringify(value) for value in self.values(field)]

    def numbers(self, field: str) -> list[float | None]:
        values = [_to_number(value) for value in self.values(field)]
        if not any(value is not None for value in values):
            raise ChartDataError(f"chart_metric_not_numeric:{field}")
        return values

    def partition(self, field: str) -> list[tuple[str, ChartDataset]]:
        self.require_fields([field])
        keys: list[str] = []
        rows_by_key: dict[str, list[Mapping[str, object]]] = {}
        for row in self.rows:
            key = _stringify(row[field])
            if key not in rows_by_key:
                keys.append(key)
                rows_by_key[key] = []
            rows_by_key[key].append(row)
        return [
            (key, ChartDataset(columns=self.columns, rows=tuple(rows_by_key[key])))
            for key in keys
        ]


class VisualizationDataTransformer:
    """Applies only transforms explicitly declared by VisualizationSpec."""

    def transform(self, preview: QueryPreview, spec: VisualizationSpec) -> ChartDataset:
        dataset = ChartDataset.from_preview(preview)
        rows = list(dataset.rows)
        if spec.transform.aggregation != VisualizationAggregation.NONE:
            rows = self._aggregate(rows, spec)
        rows = self._sort(rows, spec)
        limit = spec.transform.top_n or spec.transform.limit
        return ChartDataset(columns=dataset.columns, rows=tuple(rows[:limit]))

    @staticmethod
    def _aggregate(
        rows: list[Mapping[str, object]],
        spec: VisualizationSpec,
    ) -> list[Mapping[str, object]]:
        group_fields = _unique_fields(
            [spec.encoding.x, spec.encoding.series, spec.encoding.color]
        )
        metric_fields = _unique_fields([*spec.encoding.y, *spec.encoding.secondary_y])
        if not group_fields or not metric_fields:
            return rows

        buckets: dict[tuple[object, ...], dict[str, object]] = {}
        sums: dict[tuple[object, ...], dict[str, float]] = {}
        counts: dict[tuple[object, ...], dict[str, int]] = {}
        for row in rows:
            key = tuple(row.get(field) for field in group_fields)
            if key not in buckets:
                buckets[key] = {
                    field: key[index] for index, field in enumerate(group_fields)
                }
                sums[key] = {}
                counts[key] = {}
            for metric in metric_fields:
                raw_value = row.get(metric)
                if spec.transform.aggregation == VisualizationAggregation.COUNT:
                    if raw_value is not None:
                        counts[key][metric] = counts[key].get(metric, 0) + 1
                    continue
                value = _to_number(raw_value)
                if value is None:
                    continue
                sums[key][metric] = sums[key].get(metric, 0.0) + value
                counts[key][metric] = counts[key].get(metric, 0) + 1

        aggregated: list[Mapping[str, object]] = []
        for key, row in buckets.items():
            result = dict(row)
            for metric in metric_fields:
                count = counts[key].get(metric, 0)
                if spec.transform.aggregation == VisualizationAggregation.COUNT:
                    result[metric] = count
                elif spec.transform.aggregation == VisualizationAggregation.AVG:
                    result[metric] = sums[key].get(metric, 0.0) / count if count else None
                else:
                    result[metric] = sums[key].get(metric) if count else None
            aggregated.append(result)
        return aggregated

    @staticmethod
    def _sort(
        rows: list[Mapping[str, object]],
        spec: VisualizationSpec,
    ) -> list[Mapping[str, object]]:
        direction = spec.transform.sort
        if direction is None:
            return rows
        field = spec.encoding.x
        if spec.kind != VisualizationKind.LINE and spec.encoding.y:
            field = spec.encoding.y[0]
        if field is None:
            return rows
        return sorted(
            rows,
            key=lambda row: _sortable_value(row.get(field)),
            reverse=direction == VisualizationSort.DESC,
        )


class KaleidoPngExporter:
    """Exports high-resolution PNG bytes through Plotly's Kaleido backend."""

    def __init__(self, *, width: int = 1600, height: int = 900, scale: float = 2.0) -> None:
        self._width = width
        self._height = height
        self._scale = scale

    def export(self, figure: PlotlyFigure) -> bytes:
        return cast(
            bytes,
            pio.to_image(
                figure,
                format="png",
                width=self._width,
                height=self._height,
                scale=self._scale,
                validate=True,
            ),
        )


class CartesianFigureBuilder:
    """Builds bar, line, area, stacked, and dual-axis combo figures."""

    _BAR_KINDS = frozenset(
        {
            VisualizationKind.BAR,
            VisualizationKind.BAR_GROUPED,
            VisualizationKind.BAR_STACKED,
        }
    )

    def build(self, dataset: ChartDataset, spec: VisualizationSpec) -> PlotlyFigure:
        x_field = _required_field(spec.encoding.x, "x")
        metrics = list(spec.encoding.y)
        if not metrics:
            raise ChartDataError("chart_fields_missing:y")
        dataset.require_fields(
            [x_field, spec.encoding.series, *metrics, *spec.encoding.secondary_y]
        )

        figure = go.Figure()
        if spec.kind == VisualizationKind.COMBO_LINE_BAR:
            self._add_combo_traces(figure, dataset, spec, x_field, metrics)
        else:
            self._add_standard_traces(figure, dataset, spec, x_field, metrics)

        if spec.kind == VisualizationKind.BAR_STACKED:
            figure.update_layout(barmode="relative")
        elif spec.kind in self._BAR_KINDS:
            figure.update_layout(barmode="group")
        return figure

    def _add_standard_traces(
        self,
        figure: PlotlyFigure,
        dataset: ChartDataset,
        spec: VisualizationSpec,
        x_field: str,
        metrics: Sequence[str],
    ) -> None:
        if spec.encoding.series:
            metric = metrics[0]
            for index, (name, partition) in enumerate(
                dataset.partition(spec.encoding.series)
            ):
                figure.add_trace(
                    self._trace(
                        partition=partition,
                        spec=spec,
                        x_field=x_field,
                        metric=metric,
                        name=name,
                        color=_PALETTE[index % len(_PALETTE)],
                    )
                )
            return

        for index, metric in enumerate(metrics):
            figure.add_trace(
                self._trace(
                    partition=dataset,
                    spec=spec,
                    x_field=x_field,
                    metric=metric,
                    name=_humanize(metric),
                    color=_PALETTE[index % len(_PALETTE)],
                )
            )

    @staticmethod
    def _trace(
        *,
        partition: ChartDataset,
        spec: VisualizationSpec,
        x_field: str,
        metric: str,
        name: str,
        color: str,
    ) -> PlotlyTrace:
        x_values = partition.labels(x_field)
        y_values = partition.numbers(metric)
        if spec.kind in CartesianFigureBuilder._BAR_KINDS:
            return go.Bar(
                x=x_values,
                y=y_values,
                name=name,
                marker={"color": color, "line": {"color": "#FFFFFF", "width": 0.8}},
                hovertemplate=f"%{{x}}<br>{escape(name)}: %{{y:,.2f}}<extra></extra>",
            )

        stack_group = "total" if spec.kind == VisualizationKind.AREA_STACKED else None
        fill = "tozeroy" if spec.kind == VisualizationKind.AREA else None
        return go.Scatter(
            x=x_values,
            y=y_values,
            name=name,
            mode="lines+markers",
            line={"color": color, "width": 3, "shape": "spline", "smoothing": 0.65},
            marker={"color": color, "size": 8, "line": {"color": "#FFFFFF", "width": 1.5}},
            fill=fill,
            fillcolor=_with_alpha(color, 0.16),
            stackgroup=stack_group,
            connectgaps=False,
            hovertemplate=f"%{{x}}<br>{escape(name)}: %{{y:,.2f}}<extra></extra>",
        )

    def _add_combo_traces(
        self,
        figure: PlotlyFigure,
        dataset: ChartDataset,
        spec: VisualizationSpec,
        x_field: str,
        metrics: Sequence[str],
    ) -> None:
        primary = list(metrics)
        secondary = list(spec.encoding.secondary_y)
        if not secondary and len(primary) > 1:
            primary, secondary = primary[:1], primary[1:]

        for index, metric in enumerate(primary):
            figure.add_trace(
                go.Bar(
                    x=dataset.labels(x_field),
                    y=dataset.numbers(metric),
                    name=_humanize(metric),
                    marker={"color": _PALETTE[index % len(_PALETTE)]},
                    yaxis="y",
                    hovertemplate=f"%{{x}}<br>{escape(_humanize(metric))}: %{{y:,.2f}}<extra></extra>",
                )
            )
        for index, metric in enumerate(secondary, start=len(primary)):
            color = _PALETTE[index % len(_PALETTE)]
            figure.add_trace(
                go.Scatter(
                    x=dataset.labels(x_field),
                    y=dataset.numbers(metric),
                    name=_humanize(metric),
                    mode="lines+markers",
                    line={"color": color, "width": 3},
                    marker={"color": color, "size": 8},
                    yaxis="y2",
                    hovertemplate=f"%{{x}}<br>{escape(_humanize(metric))}: %{{y:,.2f}}<extra></extra>",
                )
            )
        if secondary:
            figure.update_layout(
                yaxis2={
                    "title": _humanize(secondary[0]),
                    "overlaying": "y",
                    "side": "right",
                    "showgrid": False,
                    "automargin": True,
                }
            )


class ScatterFigureBuilder:
    """Builds scatter and bubble figures from explicit encodings."""

    def build(self, dataset: ChartDataset, spec: VisualizationSpec) -> PlotlyFigure:
        x_field = _required_field(spec.encoding.x, "x")
        y_field = _first_metric(spec)
        group_field = spec.encoding.color or spec.encoding.series
        dataset.require_fields([x_field, y_field, group_field, spec.encoding.size])

        figure = go.Figure()
        partitions = dataset.partition(group_field) if group_field else [(_humanize(y_field), dataset)]
        for index, (name, partition) in enumerate(partitions):
            marker: dict[str, object] = {
                "color": _PALETTE[index % len(_PALETTE)],
                "opacity": 0.78,
                "line": {"color": "#FFFFFF", "width": 1.5},
                "size": 14,
            }
            if spec.kind == VisualizationKind.BUBBLE:
                size_field = _required_field(spec.encoding.size, "size")
                sizes = [value or 0.0 for value in partition.numbers(size_field)]
                max_size = max(sizes, default=0.0)
                if max_size <= 0:
                    raise ChartDataError(f"chart_metric_not_positive:{size_field}")
                marker.update(
                    {
                        "size": sizes,
                        "sizemode": "area",
                        "sizeref": 2.0 * max_size / (56.0**2),
                        "sizemin": 8,
                    }
                )
            figure.add_trace(
                go.Scatter(
                    x=partition.numbers(x_field),
                    y=partition.numbers(y_field),
                    name=name,
                    mode="markers",
                    marker=marker,
                    text=partition.labels(spec.encoding.label)
                    if spec.encoding.label
                    else None,
                    hovertemplate=(
                        f"{escape(_humanize(x_field))}: %{{x:,.2f}}<br>"
                        f"{escape(_humanize(y_field))}: %{{y:,.2f}}<extra>{escape(name)}</extra>"
                    ),
                )
            )
        return figure


class PieFigureBuilder:
    """Builds pie and donut figures without cartesian fallback."""

    def build(self, dataset: ChartDataset, spec: VisualizationSpec) -> PlotlyFigure:
        label_field = _required_field(spec.encoding.x, "x")
        metric = _first_metric(spec)
        dataset.require_fields([label_field, metric])
        return go.Figure(
            go.Pie(
                labels=dataset.labels(label_field),
                values=dataset.numbers(metric),
                hole=0.5 if spec.kind == VisualizationKind.DONUT else 0.0,
                sort=False,
                direction="clockwise",
                marker={"colors": _PALETTE, "line": {"color": "#FFFFFF", "width": 2}},
                textinfo="label+percent",
                textposition="inside",
                insidetextorientation="horizontal",
                hovertemplate=(
                    f"%{{label}}<br>{escape(_humanize(metric))}: %{{value:,.2f}}"
                    "<br>%{percent}<extra></extra>"
                ),
            )
        )


class HeatmapFigureBuilder:
    """Builds a rectangular heatmap and rejects ambiguous duplicate cells."""

    def build(self, dataset: ChartDataset, spec: VisualizationSpec) -> PlotlyFigure:
        x_field = _required_field(spec.encoding.x, "x")
        y_field = _required_field(spec.encoding.color or spec.encoding.series, "color_or_series")
        metric = _first_metric(spec)
        dataset.require_fields([x_field, y_field, metric])

        x_labels = _ordered_unique(dataset.labels(x_field))
        y_labels = _ordered_unique(dataset.labels(y_field))
        cells: dict[tuple[str, str], float | None] = {}
        for row in dataset.rows:
            key = (_stringify(row[x_field]), _stringify(row[y_field]))
            if key in cells:
                raise ChartDataError("heatmap_duplicate_cell")
            cells[key] = _to_number(row[metric])
        z_values = [[cells.get((x_value, y_value)) for x_value in x_labels] for y_value in y_labels]
        return go.Figure(
            go.Heatmap(
                x=x_labels,
                y=y_labels,
                z=z_values,
                colorscale=[
                    [0.0, "#E8F1FF"],
                    [0.5, "#60A5FA"],
                    [1.0, "#1D4ED8"],
                ],
                colorbar={"title": _humanize(metric), "thickness": 18},
                xgap=2,
                ygap=2,
                hovertemplate=(
                    f"{escape(_humanize(x_field))}: %{{x}}<br>"
                    f"{escape(_humanize(y_field))}: %{{y}}<br>"
                    f"{escape(_humanize(metric))}: %{{z:,.2f}}<extra></extra>"
                ),
            )
        )


class TreemapFigureBuilder:
    """Builds a flat treemap from explicit label and value encodings."""

    def build(self, dataset: ChartDataset, spec: VisualizationSpec) -> PlotlyFigure:
        label_field = _required_field(spec.encoding.x, "x")
        metric = _first_metric(spec)
        dataset.require_fields([label_field, metric])
        return go.Figure(
            go.Treemap(
                labels=dataset.labels(label_field),
                parents=[""] * len(dataset.rows),
                values=dataset.numbers(metric),
                sort=False,
                marker={"colors": [_PALETTE[index % len(_PALETTE)] for index in range(len(dataset.rows))]},
                textinfo="label+value+percent entry",
                hovertemplate=(
                    f"%{{label}}<br>{escape(_humanize(metric))}: %{{value:,.2f}}"
                    "<br>%{percentEntry}<extra></extra>"
                ),
            )
        )


class FunnelFigureBuilder:
    """Builds a funnel from ordered stages."""

    def build(self, dataset: ChartDataset, spec: VisualizationSpec) -> PlotlyFigure:
        stage_field = _required_field(spec.encoding.x, "x")
        metric = _first_metric(spec)
        dataset.require_fields([stage_field, metric])
        return go.Figure(
            go.Funnel(
                y=dataset.labels(stage_field),
                x=dataset.numbers(metric),
                marker={"color": [_PALETTE[index % len(_PALETTE)] for index in range(len(dataset.rows))]},
                textinfo="value+percent initial",
                connector={"line": {"color": "#CBD5E1", "width": 1}},
                hovertemplate=(
                    f"%{{y}}<br>{escape(_humanize(metric))}: %{{x:,.2f}}"
                    "<br>%{percentInitial}<extra></extra>"
                ),
            )
        )


class HistogramFigureBuilder:
    """Builds the pre-binned histogram declared by the governed contract."""

    def build(self, dataset: ChartDataset, spec: VisualizationSpec) -> PlotlyFigure:
        bin_field = _required_field(spec.encoding.x, "x")
        metric = _first_metric(spec)
        dataset.require_fields([bin_field, metric])
        return go.Figure(
            go.Bar(
                x=dataset.labels(bin_field),
                y=dataset.numbers(metric),
                marker={"color": _PALETTE[0], "line": {"color": "#FFFFFF", "width": 0.8}},
                name=_humanize(metric),
                hovertemplate=f"%{{x}}<br>{escape(_humanize(metric))}: %{{y:,.2f}}<extra></extra>",
            )
        )


class WaterfallFigureBuilder:
    """Builds true cumulative waterfall geometry from signed deltas."""

    def build(self, dataset: ChartDataset, spec: VisualizationSpec) -> PlotlyFigure:
        x_field = _required_field(spec.encoding.x, "x")
        metric = _first_metric(spec)
        dataset.require_fields([x_field, metric])
        return go.Figure(
            go.Waterfall(
                x=dataset.labels(x_field),
                y=dataset.numbers(metric),
                measure=["relative"] * len(dataset.rows),
                increasing={"marker": {"color": "#0F9D7A"}},
                decreasing={"marker": {"color": "#D64F70"}},
                totals={"marker": {"color": "#2563EB"}},
                connector={"line": {"color": "#94A3B8", "width": 1}},
                hovertemplate=(
                    f"%{{x}}<br>{escape(_humanize(metric))}: %{{y:,.2f}}"
                    "<br>Cumulative: %{final:,.2f}<extra></extra>"
                ),
            )
        )


class PlotlyChartStyler:
    """Applies one deterministic, transport-safe visual system to every figure."""

    def apply(self, figure: PlotlyFigure, request: ChartRenderRequest) -> PlotlyFigure:
        spec = request.spec
        title = escape(spec.title or request.fallback_title)
        subtitle = spec.summary or spec.description
        title_text = title
        if subtitle:
            title_text += (
                f"<br><span style='font-size:17px;color:#64748B'>{escape(subtitle)}</span>"
            )

        figure.update_layout(
            template="plotly_white",
            title={
                "text": title_text,
                "x": 0.045,
                "xanchor": "left",
                "y": 0.955,
                "yanchor": "top",
                "font": {"family": "DejaVu Sans, Arial, sans-serif", "size": 30, "color": "#0F172A"},
                "automargin": True,
            },
            paper_bgcolor="#F8FAFC",
            plot_bgcolor="#FFFFFF",
            colorway=list(_PALETTE),
            font={"family": "DejaVu Sans, Arial, sans-serif", "size": 16, "color": "#334155"},
            margin={"l": 88, "r": 72, "t": 150 if subtitle else 112, "b": 128, "pad": 8},
            legend={
                "orientation": "v",
                "yanchor": "top",
                "y": 1.0,
                "yref": "paper",
                "xanchor": "right",
                "x": 0.995,
                "xref": "container",
                "font": {"size": 15, "color": "#475569"},
                "bgcolor": "rgba(255,255,255,0)",
                "itemsizing": "constant",
                "itemwidth": 30,
            },
            hoverlabel={"font": {"family": "DejaVu Sans, Arial, sans-serif", "size": 15}},
            minreducedwidth=800,
            minreducedheight=480,
            uniformtext={"minsize": 13, "mode": "hide"},
            bargap=0.18,
            bargroupgap=0.08,
        )
        self._style_cartesian_axes(figure, spec)
        self._add_reference_lines(figure, spec.annotations)
        return figure

    @staticmethod
    def _style_cartesian_axes(figure: PlotlyFigure, spec: VisualizationSpec) -> None:
        if not _has_cartesian_trace(figure):
            return
        if spec.kind == VisualizationKind.HEATMAP:
            figure.update_xaxes(
                title={"text": spec.x_axis_label, "standoff": 24},
                type="category",
                tickmode="auto",
                nticks=14,
                tickangle=-40,
                automargin=True,
                ticklabeloverflow="allow",
                tickfont={"size": 14, "color": "#475569"},
                showgrid=False,
                fixedrange=True,
            )
            figure.update_yaxes(
                title={
                    "text": _humanize(
                        spec.encoding.color or spec.encoding.series or "Series"
                    ),
                    "standoff": 20,
                },
                type="category",
                automargin=True,
                tickfont={"size": 14, "color": "#475569"},
                showgrid=False,
                fixedrange=True,
            )
            return

        tick_format, tick_prefix, tick_suffix = _value_axis_format(spec)
        numeric_x = spec.kind in {VisualizationKind.SCATTER, VisualizationKind.BUBBLE}
        figure.update_xaxes(
            title={"text": spec.x_axis_label, "standoff": 24},
            type="linear" if numeric_x else "category",
            tickmode="auto",
            nticks=14,
            tickangle=0 if numeric_x else -40,
            automargin=True,
            ticklabeloverflow="allow",
            tickfont={"size": 14, "color": "#475569"},
            showgrid=False,
            showline=True,
            linecolor="#CBD5E1",
            linewidth=1,
            ticks="outside",
            tickcolor="#CBD5E1",
            fixedrange=True,
        )
        figure.update_yaxes(
            title={"text": spec.y_axis_label, "standoff": 20},
            automargin=True,
            tickfont={"size": 14, "color": "#475569"},
            tickformat=tick_format,
            tickprefix=tick_prefix,
            ticksuffix=tick_suffix,
            separatethousands=True,
            showgrid=True,
            gridcolor="#E2E8F0",
            gridwidth=1,
            zeroline=True,
            zerolinecolor="#94A3B8",
            zerolinewidth=1,
            showline=False,
            fixedrange=True,
        )
        secondary_metrics = list(spec.encoding.secondary_y)
        if (
            spec.kind == VisualizationKind.COMBO_LINE_BAR
            and not secondary_metrics
            and len(spec.encoding.y) > 1
        ):
            secondary_metrics = list(spec.encoding.y[1:])
        if spec.kind == VisualizationKind.COMBO_LINE_BAR and secondary_metrics:
            figure.update_layout(
                yaxis2={
                    "title": _humanize(secondary_metrics[0]),
                    "overlaying": "y",
                    "side": "right",
                    "showgrid": False,
                    "automargin": True,
                    "tickformat": tick_format,
                    "tickprefix": tick_prefix,
                    "ticksuffix": tick_suffix,
                }
            )

    @staticmethod
    def _add_reference_lines(
        figure: PlotlyFigure,
        annotations: Sequence[VisualizationAnnotation],
    ) -> None:
        if not _has_cartesian_trace(figure):
            return
        colors = {"neutral": "#64748B", "success": "#0F9D7A", "warn": "#E69F00"}
        for annotation in annotations:
            color = colors[annotation.style]
            if annotation.axis == "y":
                figure.add_hline(
                    y=annotation.value,
                    line_dash="dash",
                    line_color=color,
                    line_width=2,
                    annotation_text=annotation.label,
                    annotation_font_color=color,
                )
            else:
                figure.add_vline(
                    x=annotation.value,
                    line_dash="dash",
                    line_color=color,
                    line_width=2,
                    annotation_text=annotation.label,
                    annotation_font_color=color,
                )


class PlotlyChartRenderer:
    """Selects the exact declared builder, styles the figure, and exports PNG."""

    def __init__(
        self,
        *,
        exporter: StaticImageExporter | None = None,
        transformer: VisualizationDataTransformer | None = None,
        styler: PlotlyChartStyler | None = None,
        builders: Mapping[VisualizationKind, PlotlyFigureBuilder] | None = None,
    ) -> None:
        self._exporter = exporter or KaleidoPngExporter()
        self._transformer = transformer or VisualizationDataTransformer()
        self._styler = styler or PlotlyChartStyler()
        self._builders = dict(builders or self._default_builders())

    def render(self, request: ChartRenderRequest) -> bytes | None:
        if request.spec.kind == VisualizationKind.TABLE:
            return None
        builder = self._builders.get(request.spec.kind)
        if builder is None:
            logger.warning("chart.render.unsupported_kind", kind=request.spec.kind.value)
            return None
        if not request.preview.columns or not request.preview.rows:
            logger.info("chart.render.empty_preview", kind=request.spec.kind.value)
            return None
        try:
            dataset = self._transformer.transform(request.preview, request.spec)
            if not dataset.rows:
                return None
            figure = builder.build(dataset, request.spec)
            self._styler.apply(figure, request)
            content = self._exporter.export(figure)
        except ChartDataError as exc:
            logger.warning(
                "chart.render.invalid_data",
                kind=request.spec.kind.value,
                reason=str(exc),
            )
            return None
        except Exception:
            logger.exception("chart.render.failed", kind=request.spec.kind.value)
            return None
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            logger.error("chart.render.invalid_png", kind=request.spec.kind.value)
            return None
        return content

    @staticmethod
    def _default_builders() -> Mapping[VisualizationKind, PlotlyFigureBuilder]:
        cartesian = CartesianFigureBuilder()
        scatter = ScatterFigureBuilder()
        pie = PieFigureBuilder()
        return {
            VisualizationKind.BAR: cartesian,
            VisualizationKind.BAR_GROUPED: cartesian,
            VisualizationKind.BAR_STACKED: cartesian,
            VisualizationKind.LINE: cartesian,
            VisualizationKind.AREA: cartesian,
            VisualizationKind.AREA_STACKED: cartesian,
            VisualizationKind.COMBO_LINE_BAR: cartesian,
            VisualizationKind.SCATTER: scatter,
            VisualizationKind.BUBBLE: scatter,
            VisualizationKind.PIE: pie,
            VisualizationKind.DONUT: pie,
            VisualizationKind.HEATMAP: HeatmapFigureBuilder(),
            VisualizationKind.TREEMAP: TreemapFigureBuilder(),
            VisualizationKind.FUNNEL: FunnelFigureBuilder(),
            VisualizationKind.HISTOGRAM: HistogramFigureBuilder(),
            VisualizationKind.WATERFALL: WaterfallFigureBuilder(),
        }


def _required_field(value: str | None, channel: str) -> str:
    if value is None:
        raise ChartDataError(f"chart_fields_missing:{channel}")
    return value


def _first_metric(spec: VisualizationSpec) -> str:
    if not spec.encoding.y:
        raise ChartDataError("chart_fields_missing:y")
    return spec.encoding.y[0]


def _to_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _stringify(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _sortable_value(value: object) -> tuple[int, float | str]:
    if value is None:
        return (2, "")
    number = _to_number(value)
    if number is not None:
        return (0, number)
    return (1, str(value))


def _unique_fields(fields: Iterable[str | None]) -> list[str]:
    return list(dict.fromkeys(field for field in fields if field))


def _ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _humanize(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _with_alpha(color: str, alpha: float) -> str:
    normalized = color.lstrip("#")
    red = int(normalized[0:2], 16)
    green = int(normalized[2:4], 16)
    blue = int(normalized[4:6], 16)
    return f"rgba({red},{green},{blue},{alpha})"


def _has_cartesian_trace(figure: PlotlyFigure) -> bool:
    return any(trace.type in {"bar", "scatter", "heatmap", "waterfall"} for trace in figure.data)


def _value_axis_format(spec: VisualizationSpec) -> tuple[str | None, str | None, str | None]:
    if spec.value_format == VisualizationValueFormat.INTEGER:
        return ",.0f", None, _metric_suffix(spec)
    if spec.value_format == VisualizationValueFormat.DECIMAL:
        return ",.2f", None, _metric_suffix(spec)
    if spec.value_format == VisualizationValueFormat.PERCENT:
        return ".1%", None, None
    if spec.value_format == VisualizationValueFormat.CURRENCY:
        currency = spec.metric_unit or "RUB"
        return ",.2f", f"{currency} ", None
    return "~s", None, _metric_suffix(spec)


def _metric_suffix(spec: VisualizationSpec) -> str | None:
    return f" {spec.metric_unit}" if spec.metric_unit else None
