from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class VisualizationFamily(str, Enum):
    """High-level chart family used by richer v2 grammar."""

    CATEGORICAL = "categorical"
    TEMPORAL = "temporal"
    DISTRIBUTION = "distribution"
    RELATIONSHIP = "relationship"
    COMPOSITION = "composition"
    SUMMARY = "summary"
    FALLBACK = "fallback"


class VisualizationKind(str, Enum):
    """Supported visualization kinds for governed v2 contract."""

    TABLE = "table"
    BAR = "bar"
    BAR_GROUPED = "bar_grouped"
    BAR_STACKED = "bar_stacked"
    LINE = "line"
    AREA = "area"
    AREA_STACKED = "area_stacked"
    COMBO_LINE_BAR = "combo_line_bar"
    SCATTER = "scatter"
    BUBBLE = "bubble"
    PIE = "pie"
    DONUT = "donut"
    HEATMAP = "heatmap"
    TREEMAP = "treemap"
    FUNNEL = "funnel"
    HISTOGRAM = "histogram"
    WATERFALL = "waterfall"


class VisualizationGoal(str, Enum):
    """Analytical intent communicated to the UI."""

    INSPECTION = "inspection"
    TREND = "trend"
    COMPARISON = "comparison"
    COMPOSITION = "composition"
    CORRELATION = "correlation"
    DISTRIBUTION = "distribution"
    RANKING = "ranking"
    DELTA = "delta"


class VisualizationAggregation(str, Enum):
    """Supported aggregation modes for chart transforms."""

    NONE = "none"
    SUM = "sum"
    AVG = "avg"
    COUNT = "count"


class VisualizationSort(str, Enum):
    """Supported ordering in transform rules."""

    ASC = "asc"
    DESC = "desc"


class VisualizationNormalize(str, Enum):
    """Supported normalization policies."""

    NONE = "none"
    PERCENT_OF_TOTAL = "percent_of_total"
    PERCENT_OF_SERIES = "percent_of_series"


class VisualizationValueFormat(str, Enum):
    """Display formatting hint for axis labels and tooltips."""

    AUTO = "auto"
    INTEGER = "integer"
    DECIMAL = "decimal"
    PERCENT = "percent"
    CURRENCY = "currency"


class VisualizationDataSource(BaseModel):
    """Data source metadata for visualization payload."""

    type: Literal["execution_preview"] = "execution_preview"
    execution_id: str
    row_count: int = Field(default=0, ge=0)
    is_truncated: bool = False


class VisualizationEncoding(BaseModel):
    """Column-to-channel mapping for richer chart rendering."""

    x: str | None = None
    y: list[str] = Field(default_factory=list)
    series: str | None = None
    color: str | None = None
    size: str | None = None
    facet: str | None = None
    label: str | None = None
    tooltip: list[str] = Field(default_factory=list)
    stack: str | None = None
    secondary_y: list[str] = Field(default_factory=list)


class VisualizationTransform(BaseModel):
    """Deterministic transform controls enforced by backend policy."""

    sort: VisualizationSort | None = None
    limit: int = Field(default=50, ge=1, le=200)
    aggregation: VisualizationAggregation = VisualizationAggregation.NONE
    top_n: int | None = Field(default=None, ge=1, le=200)
    bin_size: int | None = Field(default=None, ge=1, le=5000)
    window: str | None = None
    pivot: str | None = None
    normalize: VisualizationNormalize = VisualizationNormalize.NONE
    cumulative: bool = False


class VisualizationRationale(BaseModel):
    """Explainability section for planner decisions."""

    why_kind: str
    why_fields: list[str] = Field(default_factory=list)


class VisualizationAnnotation(BaseModel):
    """Declarative annotation supported by the chart adapter."""

    kind: Literal["reference_line"] = "reference_line"
    axis: Literal["x", "y"] = "y"
    value: float | str
    label: str
    style: Literal["neutral", "success", "warn"] = "neutral"


class VisualizationSpec(BaseModel):
    """Safe declarative chart contract (no executable JS)."""

    version: Literal["v1", "v2"] = "v2"
    family: VisualizationFamily | None = None
    kind: VisualizationKind
    goal: VisualizationGoal = VisualizationGoal.INSPECTION
    title: str | None = None
    summary: str | None = None
    description: str | None = None
    planner_mode: Literal["llm_validated", "rule_fallback", "table_fallback"] = "rule_fallback"
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    chart_safe_on_preview: bool = False
    x_axis_label: str | None = None
    y_axis_label: str | None = None
    value_format: VisualizationValueFormat = VisualizationValueFormat.AUTO
    metric_unit: str | None = Field(default=None, max_length=24)
    data_source: VisualizationDataSource
    encoding: VisualizationEncoding = Field(default_factory=VisualizationEncoding)
    transform: VisualizationTransform = Field(default_factory=VisualizationTransform)
    annotations: list[VisualizationAnnotation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    rationale: VisualizationRationale

    @model_validator(mode="after")
    def _normalize_contract(self) -> VisualizationSpec:
        if self.family is None:
            self.family = _family_for_kind(self.kind)
        if self.version not in {"v1", "v2"}:
            self.version = "v2"
        if self.version == "v1":
            self.version = "v2"
        self.encoding.tooltip = _dedupe(self.encoding.tooltip)
        self.encoding.secondary_y = _dedupe(self.encoding.secondary_y)
        self.encoding.y = _dedupe(self.encoding.y)
        self.warnings = _dedupe(self.warnings)
        return self


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
    if kind == VisualizationKind.TABLE:
        return VisualizationFamily.FALLBACK
    return VisualizationFamily.SUMMARY


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
