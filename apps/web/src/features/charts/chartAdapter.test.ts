import { describe, expect, it } from "vitest";

import {
  buildChartOption,
  collectVisualizationDebugWarnings,
  getVisualizationFallbackReason,
  humanizeWarning,
  summarizePreviewCoverage,
} from "./chartAdapter";
import type { QueryPreview, VisualizationKind, VisualizationSpec } from "./types";

type AxisLabelWithFormatter = {
  formatter?: (value: string | number) => string;
};

const preview = {
  columns: ["day", "gmv"],
  rows: [
    ["2026-03-01", 1000],
    ["2026-03-02", 1400],
    ["2026-03-03", 900],
  ],
};

const spec: VisualizationSpec = {
  version: "v2",
  family: "temporal",
  kind: "line",
  goal: "trend",
  title: "GMV over Day",
  summary: "Peak gmv is $1,400.00 at 2026-03-02.",
  description: "Trend view built from the execution preview.",
  planner_mode: "rule_fallback",
  confidence_score: 0.84,
  chart_safe_on_preview: true,
  x_axis_label: "Day",
  y_axis_label: "GMV",
  value_format: "currency",
  metric_unit: null,
  data_source: {
    type: "execution_preview",
    execution_id: "exec-1",
    row_count: 3,
    is_truncated: false,
  },
  encoding: {
    x: "day",
    y: ["gmv"],
    series: null,
    color: null,
    size: null,
    facet: null,
    label: null,
    tooltip: [],
    stack: null,
    secondary_y: [],
  },
  transform: {
    sort: "asc",
    limit: 50,
    aggregation: "none",
    top_n: null,
    bin_size: null,
    window: null,
    pivot: null,
    normalize: "none",
    cumulative: false,
  },
  annotations: [
    {
      kind: "reference_line",
      axis: "y",
      value: 1100,
      label: "Average $1,100.00",
      style: "neutral",
    },
  ],
  warnings: [],
  rationale: {
    why_kind: "Detected time column with metric.",
    why_fields: ["day as x", "gmv as y"],
  },
};

function chartSpec(
  kind: VisualizationKind,
  encoding: Partial<VisualizationSpec["encoding"]>,
  overrides: Partial<VisualizationSpec> = {},
): VisualizationSpec {
  return {
    ...spec,
    family: overrides.family ?? spec.family,
    kind,
    goal: overrides.goal ?? spec.goal,
    title: `${kind} chart`,
    x_axis_label: encoding.x ?? "x",
    y_axis_label: encoding.y?.[0] ?? "metric",
    encoding: {
      ...spec.encoding,
      ...encoding,
      y: encoding.y ?? spec.encoding.y,
    },
    transform: {
      ...spec.transform,
      ...overrides.transform,
    },
    data_source: {
      ...spec.data_source,
      row_count: overrides.data_source?.row_count ?? 4,
      is_truncated: overrides.data_source?.is_truncated ?? false,
    },
    ...overrides,
  };
}

describe("buildChartOption", () => {
  it("builds a styled line chart with annotations and formatted axes", () => {
    const option = buildChartOption(spec, preview);

    expect(option).not.toBeNull();
    expect(option?.series).toHaveLength(1);
    const yAxis = Array.isArray(option?.yAxis) ? option?.yAxis[0] : option?.yAxis;
    expect(yAxis).toMatchObject({
      name: "GMV",
    });

    const series = Array.isArray(option?.series) ? option?.series[0] : null;
    expect(series).toMatchObject({
      type: "line",
    });
    expect(series?.markLine).toMatchObject({
      data: [{ yAxis: 1100 }],
    });
    const formatter = (yAxis?.axisLabel as AxisLabelWithFormatter | undefined)?.formatter;
    expect(formatter?.(1400)).toContain("₽");
    expect(formatter?.(1400)).toContain("1");
  });

  it("summarizes preview coverage and warnings for trust UX", () => {
    expect(summarizePreviewCoverage(spec)).toBe("3 rows visualized from 3 available.");
    expect(humanizeWarning("preview_truncated")).toContain("truncated");
  });

  it.each([
    ["bar", { columns: ["label", "value"], rows: [["A", 10], ["B", 20]] }, { x: "label", y: ["value"] }, "bar"],
    ["bar_grouped", { columns: ["day", "segment", "value"], rows: [["Mon", "new", 10], ["Mon", "returning", 8]] }, { x: "day", y: ["value"], series: "segment" }, "bar"],
    ["bar_stacked", { columns: ["day", "segment", "value"], rows: [["Mon", "new", 10], ["Mon", "returning", 8]] }, { x: "day", y: ["value"], series: "segment", stack: "total" }, "bar"],
    ["line", preview, { x: "day", y: ["gmv"] }, "line"],
    ["area", preview, { x: "day", y: ["gmv"] }, "line"],
    ["area_stacked", { columns: ["day", "segment", "gmv"], rows: [["2026-03-01", "A", 100], ["2026-03-01", "B", 80]] }, { x: "day", y: ["gmv"], series: "segment", stack: "total" }, "line"],
    ["combo_line_bar", { columns: ["day", "gmv", "plan"], rows: [["2026-03-01", 100, 90], ["2026-03-02", 120, 110]] }, { x: "day", y: ["gmv", "plan"] }, "bar"],
    ["scatter", { columns: ["avg_check", "retention"], rows: [[500, 0.31], [620, 0.35]] }, { x: "avg_check", y: ["retention"] }, "scatter"],
    ["bubble", { columns: ["avg_check", "retention", "orders"], rows: [[500, 0.31, 12], [620, 0.35, 18]] }, { x: "avg_check", y: ["retention"], size: "orders" }, "scatter"],
    ["pie", { columns: ["channel", "orders"], rows: [["Web", 100], ["App", 220]] }, { x: "channel", y: ["orders"] }, "pie"],
    ["donut", { columns: ["channel", "orders"], rows: [["Web", 100], ["App", 220]] }, { x: "channel", y: ["orders"] }, "pie"],
    ["heatmap", { columns: ["day", "segment", "orders"], rows: [["Mon", "A", 10], ["Tue", "B", 14]] }, { x: "day", y: ["orders"], color: "segment" }, "heatmap"],
    ["treemap", { columns: ["category", "gmv"], rows: [["A", 100], ["B", 70]] }, { x: "category", y: ["gmv"] }, "treemap"],
    ["funnel", { columns: ["stage", "users"], rows: [["Visit", 1000], ["Signup", 300]] }, { x: "stage", y: ["users"] }, "funnel"],
    ["histogram", { columns: ["bucket", "frequency"], rows: [["0-10", 4], ["10-20", 9]] }, { x: "bucket", y: ["frequency"] }, "bar"],
    ["waterfall", { columns: ["step", "delta"], rows: [["Start", 100], ["Refunds", -20], ["Upsell", 35]] }, { x: "step", y: ["delta"] }, "bar"],
  ] satisfies Array<[VisualizationKind, QueryPreview, Partial<VisualizationSpec["encoding"]>, string]>)(
    "renders %s payloads",
    (kind, chartPreview, encoding, expectedSeriesType) => {
      const option = buildChartOption(chartSpec(kind, encoding), chartPreview);

      expect(option).not.toBeNull();
      const series = Array.isArray(option?.series) ? option.series : [option?.series];
      expect(series[0]).toMatchObject({ type: expectedSeriesType });
    },
  );

  it("applies explicit aggregation before rendering", () => {
    const option = buildChartOption(
      chartSpec(
        "bar",
        { x: "country", y: ["orders"] },
        { transform: { ...spec.transform, sort: null, aggregation: "sum" } },
      ),
      {
        columns: ["country", "orders"],
        rows: [["RU", 10], ["RU", 15], ["DE", 7]],
      },
    );

    expect(option).not.toBeNull();
    expect(option?.dataset).toMatchObject([{ source: [{ country: "RU", orders: 25 }, { country: "DE", orders: 7 }] }]);
  });

  it("reports deterministic fallback reasons for debug telemetry", () => {
    const unsafe = chartSpec("line", { x: "missing", y: ["gmv"] });

    expect(getVisualizationFallbackReason(unsafe, preview)).toBe("unsafe_encoding");
    expect(
      collectVisualizationDebugWarnings(
        chartSpec("line", { x: "day", y: ["gmv"] }, { data_source: { ...spec.data_source, is_truncated: true } }),
        preview,
      ),
    ).toContain("truncated_preview");
  });
});
