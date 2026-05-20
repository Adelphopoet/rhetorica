export type VisualizationFamily =
  | "categorical"
  | "temporal"
  | "distribution"
  | "relationship"
  | "composition"
  | "summary"
  | "fallback";

export type VisualizationKind =
  | "table"
  | "bar"
  | "bar_grouped"
  | "bar_stacked"
  | "line"
  | "area"
  | "area_stacked"
  | "combo_line_bar"
  | "scatter"
  | "bubble"
  | "pie"
  | "donut"
  | "heatmap"
  | "treemap"
  | "funnel"
  | "histogram"
  | "waterfall";
export type VisualizationGoal =
  | "inspection"
  | "trend"
  | "comparison"
  | "composition"
  | "correlation"
  | "distribution"
  | "ranking"
  | "delta";

export type VisualizationSort = "asc" | "desc";

export type VisualizationAggregation = "none" | "sum" | "avg" | "count";
export type VisualizationNormalize = "none" | "percent_of_total" | "percent_of_series";
export type VisualizationValueFormat = "auto" | "integer" | "decimal" | "percent" | "currency";

export interface VisualizationDataSource {
  type: "execution_preview";
  execution_id: string;
  row_count: number;
  is_truncated: boolean;
}

export interface VisualizationEncoding {
  x: string | null;
  y: string[];
  series: string | null;
  color: string | null;
  size: string | null;
  facet: string | null;
  label: string | null;
  tooltip: string[];
  stack: string | null;
  secondary_y: string[];
}

export interface VisualizationTransform {
  sort: VisualizationSort | null;
  limit: number;
  aggregation: VisualizationAggregation;
  top_n: number | null;
  bin_size: number | null;
  window: string | null;
  pivot: string | null;
  normalize: VisualizationNormalize;
  cumulative: boolean;
}

export interface VisualizationRationale {
  why_kind: string;
  why_fields: string[];
}

export interface VisualizationAnnotation {
  kind: "reference_line";
  axis: "x" | "y";
  value: number | string;
  label: string;
  style: "neutral" | "success" | "warn";
}

export interface VisualizationSpec {
  version: "v1" | "v2";
  family: VisualizationFamily | null;
  kind: VisualizationKind;
  goal: VisualizationGoal;
  title: string | null;
  summary: string | null;
  description: string | null;
  planner_mode: "llm_validated" | "rule_fallback" | "table_fallback";
  confidence_score: number;
  chart_safe_on_preview: boolean;
  x_axis_label: string | null;
  y_axis_label: string | null;
  value_format: VisualizationValueFormat;
  metric_unit: string | null;
  data_source: VisualizationDataSource;
  encoding: VisualizationEncoding;
  transform: VisualizationTransform;
  annotations: VisualizationAnnotation[];
  warnings: string[];
  rationale: VisualizationRationale;
}

export interface QueryPreview {
  columns: string[];
  rows: Array<Array<string | number | boolean | null>>;
}
