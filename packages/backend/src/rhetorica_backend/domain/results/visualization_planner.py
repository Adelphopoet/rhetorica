"""Governed planner for VisualizationSpec v2."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.execution import ExecuteSqlResponse
from rhetorica_backend.contracts.visualization import (
    VisualizationAggregation,
    VisualizationAnnotation,
    VisualizationDataSource,
    VisualizationEncoding,
    VisualizationFamily,
    VisualizationGoal,
    VisualizationKind,
    VisualizationNormalize,
    VisualizationSpec,
    VisualizationTransform,
    VisualizationValueFormat,
)


class VisualizationPlanner:
    """Builds safe visualization spec from execution preview."""

    _DEFAULT_LIMIT = 50
    _MAX_LIMIT = 200
    _MAX_DIMENSION_CARDINALITY = 50
    _MAX_SERIES_CARDINALITY = 12
    _PREFERRED_METRIC_TOKENS = (
        "avg",
        "average",
        "mean",
        "median",
        "rate",
        "ratio",
        "pct",
        "percent",
        "per_",
        "_per",
    )
    _PERCENT_TOKENS = ("pct", "percent", "rate", "ratio", "share")
    _CURRENCY_TOKENS = ("gmv", "revenue", "sales", "amount", "price", "cost")

    def plan(
        self,
        execution: ExecuteSqlResponse | None,
        llm_candidate: dict[str, Any] | None = None,
    ) -> VisualizationSpec | None:
        if execution is None:
            return None
        if not execution.preview.columns:
            return None

        if llm_candidate is None:
            # Visualization is now explicit LLM opt-in.
            return None

        try:
            llm_spec = VisualizationSpec.model_validate(llm_candidate)
        except Exception:
            return None

        llm_spec.planner_mode = "llm_validated"
        try:
            return self._normalize_and_validate(llm_spec, execution)
        except ValueError:
            return None

    def _normalize_and_validate(
        self,
        spec: VisualizationSpec,
        execution: ExecuteSqlResponse,
    ) -> VisualizationSpec:
        columns = execution.preview.columns
        rows = execution.preview.rows
        column_set = set(columns)

        row_count = execution.row_count if execution.row_count is not None else len(rows)
        is_truncated = self._is_truncated(execution)

        warnings: list[str] = []
        for warning in spec.warnings:
            if warning not in warnings:
                warnings.append(warning)
        if is_truncated and "preview_truncated" not in warnings:
            warnings.append("preview_truncated")

        normalized = spec.model_copy(deep=True)
        normalized.version = "v2"
        normalized.data_source = VisualizationDataSource(
            execution_id=execution.execution_id,
            row_count=max(0, row_count),
            is_truncated=is_truncated,
        )
        normalized.transform.limit = max(1, min(normalized.transform.limit, self._MAX_LIMIT))
        if normalized.transform.top_n is not None:
            normalized.transform.top_n = max(1, min(normalized.transform.top_n, self._MAX_LIMIT))
        normalized.confidence_score = max(0.0, min(normalized.confidence_score, 1.0))
        normalized.warnings = warnings
        normalized.chart_safe_on_preview = bool(rows) and not is_truncated
        normalized.annotations = [
            annotation
            for annotation in normalized.annotations
            if annotation.kind == "reference_line" and annotation.axis in {"x", "y"}
        ]

        if normalized.kind == VisualizationKind.TABLE:
            normalized.goal = VisualizationGoal.INSPECTION
            normalized.encoding = VisualizationEncoding()
            normalized.transform = VisualizationTransform(
                sort=None,
                limit=normalized.transform.limit,
                aggregation=VisualizationAggregation.NONE,
                normalize=VisualizationNormalize.NONE,
            )
            normalized.x_axis_label = None
            normalized.y_axis_label = None
            normalized.value_format = VisualizationValueFormat.AUTO
            normalized.chart_safe_on_preview = False
            return normalized

        x = normalized.encoding.x
        y = normalized.encoding.y
        series = normalized.encoding.series
        color = normalized.encoding.color
        size = normalized.encoding.size
        secondary_y = normalized.encoding.secondary_y

        if not x:
            raise ValueError
        if x not in column_set:
            raise ValueError
        if not y:
            raise ValueError
        if any(metric not in column_set for metric in y):
            raise ValueError
        if any(metric not in column_set for metric in secondary_y):
            raise ValueError
        if series and series not in column_set:
            raise ValueError
        if color and color not in column_set:
            raise ValueError
        if size and size not in column_set:
            raise ValueError

        if (
            normalized.kind
            in {
                VisualizationKind.PIE,
                VisualizationKind.DONUT,
                VisualizationKind.TREEMAP,
                VisualizationKind.FUNNEL,
                VisualizationKind.HISTOGRAM,
                VisualizationKind.WATERFALL,
                VisualizationKind.SCATTER,
                VisualizationKind.BUBBLE,
                VisualizationKind.HEATMAP,
            }
            and len(y) != 1
        ):
            raise ValueError
        if normalized.kind == VisualizationKind.HEATMAP and not (color or series):
            raise ValueError
        if (
            normalized.kind
            in {
                VisualizationKind.PIE,
                VisualizationKind.DONUT,
            }
            and series is not None
        ):
            raise ValueError

        if rows:
            x_cardinality = self._column_cardinality(rows, columns, x)
            if (
                normalized.kind
                in {
                    VisualizationKind.BAR,
                    VisualizationKind.BAR_GROUPED,
                    VisualizationKind.BAR_STACKED,
                    VisualizationKind.PIE,
                    VisualizationKind.DONUT,
                    VisualizationKind.TREEMAP,
                    VisualizationKind.FUNNEL,
                    VisualizationKind.WATERFALL,
                }
                and x_cardinality > self._MAX_DIMENSION_CARDINALITY
            ):
                raise ValueError
            if series:
                series_cardinality = self._column_cardinality(rows, columns, series)
                if series_cardinality > self._MAX_SERIES_CARDINALITY:
                    raise ValueError

        if normalized.family is None:
            normalized.family = self._family_for_kind(normalized.kind)

        return normalized

    def _build_chart_title(
        self,
        *,
        kind: VisualizationKind,
        x: str,
        metric: str,
    ) -> str:
        metric_label = self._humanize_label(metric)
        x_label = self._humanize_label(x)
        if kind in {
            VisualizationKind.LINE,
            VisualizationKind.AREA,
            VisualizationKind.AREA_STACKED,
            VisualizationKind.COMBO_LINE_BAR,
        }:
            return f"{metric_label} over {x_label}"
        if kind in {
            VisualizationKind.BAR,
            VisualizationKind.BAR_GROUPED,
            VisualizationKind.BAR_STACKED,
        }:
            return f"{metric_label} by {x_label}"
        if kind == VisualizationKind.SCATTER:
            return f"{self._humanize_label(x)} vs {metric_label}"
        if kind in {VisualizationKind.PIE, VisualizationKind.DONUT, VisualizationKind.TREEMAP}:
            return f"{metric_label} share by {x_label}"
        if kind == VisualizationKind.HEATMAP:
            return f"{metric_label} intensity across {x_label}"
        if kind == VisualizationKind.FUNNEL:
            return f"{metric_label} funnel by {x_label}"
        return "Execution Preview"

    def _build_chart_summary(
        self,
        *,
        kind: VisualizationKind,
        rows: list[list[Any]],
        columns: list[str],
        x: str,
        metric: str,
    ) -> str:
        metric_values = self._metric_values(rows, columns, metric)
        if not metric_values:
            return "Preview is ready for inspection."
        x_index = columns.index(x)
        metric_index = columns.index(metric)

        if kind in {
            VisualizationKind.LINE,
            VisualizationKind.AREA,
            VisualizationKind.AREA_STACKED,
            VisualizationKind.BAR,
            VisualizationKind.BAR_GROUPED,
            VisualizationKind.BAR_STACKED,
            VisualizationKind.COMBO_LINE_BAR,
            VisualizationKind.PIE,
            VisualizationKind.DONUT,
            VisualizationKind.TREEMAP,
            VisualizationKind.FUNNEL,
        }:
            ranked_rows = [
                row
                for row in rows
                if len(row) > max(x_index, metric_index)
                and self._safe_float(row[metric_index]) is not None
            ]
            if not ranked_rows:
                return "Preview is ready for inspection."
            top_row = max(
                ranked_rows,
                key=lambda row: self._safe_float(row[metric_index]) or 0.0,
            )
            top_value = self._safe_float(top_row[metric_index])
            top_dimension = top_row[x_index]
            return (
                f"Peak {self._humanize_label(metric).lower()} is "
                f"{self._format_preview_value(metric, top_value, rows, columns)} "
                f"at {top_dimension}."
            )

        if kind == VisualizationKind.SCATTER:
            return (
                f"{len(metric_values)} points available to inspect the relationship between "
                f"{self._humanize_label(x).lower()} and {self._humanize_label(metric).lower()}."
            )
        if kind == VisualizationKind.HEATMAP:
            return (
                f"{len(metric_values)} cells available to inspect variation in "
                f"{self._humanize_label(metric).lower()}."
            )

        return "Preview is ready for inspection."

    def _build_reference_line_annotations(
        self,
        *,
        metric_name: str,
        rows: list[list[Any]],
        columns: list[str],
        value_format: VisualizationValueFormat,
    ) -> list[VisualizationAnnotation]:
        metric_values = self._metric_values(rows, columns, metric_name)
        if len(metric_values) < 2:
            return []
        average = sum(metric_values) / len(metric_values)
        return [
            VisualizationAnnotation(
                axis="y",
                value=round(average, 4),
                label=(f"Average {self._format_numeric_value(average, value_format)}"),
                style="neutral",
            )
        ]

    def _infer_value_format(
        self,
        metric_name: str,
        rows: list[list[Any]],
        columns: list[str],
    ) -> VisualizationValueFormat:
        normalized = metric_name.strip().lower()
        values = self._metric_values(rows, columns, metric_name)
        if any(token in normalized for token in self._PERCENT_TOKENS):
            return VisualizationValueFormat.PERCENT
        if any(token in normalized for token in self._CURRENCY_TOKENS):
            return VisualizationValueFormat.CURRENCY
        if values and all(float(value).is_integer() for value in values):
            return VisualizationValueFormat.INTEGER
        if values and max(values) <= 1 and min(values) >= 0:
            return VisualizationValueFormat.PERCENT
        if values:
            return VisualizationValueFormat.DECIMAL
        return VisualizationValueFormat.AUTO

    @staticmethod
    def _goal_for_kind(kind: VisualizationKind) -> VisualizationGoal:
        if kind in {
            VisualizationKind.LINE,
            VisualizationKind.AREA,
            VisualizationKind.AREA_STACKED,
            VisualizationKind.COMBO_LINE_BAR,
        }:
            return VisualizationGoal.TREND
        if kind in {
            VisualizationKind.BAR,
            VisualizationKind.BAR_GROUPED,
            VisualizationKind.BAR_STACKED,
        }:
            return VisualizationGoal.COMPARISON
        if kind in {
            VisualizationKind.PIE,
            VisualizationKind.DONUT,
            VisualizationKind.TREEMAP,
            VisualizationKind.FUNNEL,
        }:
            return VisualizationGoal.COMPOSITION
        if kind in {VisualizationKind.SCATTER, VisualizationKind.BUBBLE}:
            return VisualizationGoal.CORRELATION
        if kind in {VisualizationKind.HEATMAP, VisualizationKind.HISTOGRAM}:
            return VisualizationGoal.DISTRIBUTION
        return VisualizationGoal.INSPECTION

    @staticmethod
    def _family_for_kind(kind: VisualizationKind) -> VisualizationFamily:
        if kind in {
            VisualizationKind.BAR,
            VisualizationKind.BAR_GROUPED,
            VisualizationKind.BAR_STACKED,
        }:
            return VisualizationFamily.CATEGORICAL
        if kind in {
            VisualizationKind.LINE,
            VisualizationKind.AREA,
            VisualizationKind.AREA_STACKED,
            VisualizationKind.COMBO_LINE_BAR,
        }:
            return VisualizationFamily.TEMPORAL
        if kind in {VisualizationKind.HISTOGRAM}:
            return VisualizationFamily.DISTRIBUTION
        if kind in {
            VisualizationKind.SCATTER,
            VisualizationKind.BUBBLE,
            VisualizationKind.HEATMAP,
        }:
            return VisualizationFamily.RELATIONSHIP
        if kind in {
            VisualizationKind.PIE,
            VisualizationKind.DONUT,
            VisualizationKind.TREEMAP,
            VisualizationKind.FUNNEL,
            VisualizationKind.WATERFALL,
        }:
            return VisualizationFamily.COMPOSITION
        return VisualizationFamily.FALLBACK

    @staticmethod
    def _humanize_label(value: str) -> str:
        return value.replace("_", " ").strip().title()

    def _metric_values(
        self,
        rows: list[list[Any]],
        columns: list[str],
        metric_name: str,
    ) -> list[float]:
        index = columns.index(metric_name)
        values: list[float] = []
        for row in rows:
            if len(row) <= index:
                continue
            value = self._safe_float(row[index])
            if value is None:
                continue
            values.append(value)
        return values

    def _format_preview_value(
        self,
        metric_name: str,
        value: float | None,
        rows: list[list[Any]],
        columns: list[str],
    ) -> str:
        if value is None:
            return "n/a"
        return self._format_numeric_value(
            value,
            self._infer_value_format(metric_name, rows, columns),
        )

    @staticmethod
    def _format_numeric_value(
        value: float,
        value_format: VisualizationValueFormat,
    ) -> str:
        if value_format == VisualizationValueFormat.INTEGER:
            return f"{round(value):,}"
        if value_format == VisualizationValueFormat.PERCENT:
            scaled = value * 100 if abs(value) <= 1 else value
            return f"{scaled:.1f}%"
        if value_format == VisualizationValueFormat.CURRENCY:
            return f"${value:,.2f}"
        if value_format == VisualizationValueFormat.DECIMAL:
            return f"{value:,.2f}"
        return f"{value:,.4g}"

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if parsed != parsed:
            return None
        return parsed

    @staticmethod
    def _value_key(value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.12g}"
        return str(value)

    @staticmethod
    def _column_cardinality(rows: list[list[Any]], columns: list[str], column_name: str) -> int:
        index = columns.index(column_name)
        values = [row[index] for row in rows if len(row) > index and row[index] is not None]
        return len({VisualizationPlanner._value_key(value) for value in values})

    @staticmethod
    def _is_truncated(execution: ExecuteSqlResponse) -> bool:
        if execution.row_count is None or execution.applied_row_limit is None:
            return False
        return execution.row_count >= execution.applied_row_limit
