import { describe, expect, it } from "vitest";
import { init, setPlatformAPI } from "echarts";
import { getChartColors } from "./chartTheme";

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
type TooltipWithFormatter = {
  formatter?: (params: unknown) => string;
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
  summary: "Peak gmv is $1,400.00 at 2026-03-02",
  description: "Trend view built from the execution preview",
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
    why_kind: "Detected time column with metric",
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
    expect(summarizePreviewCoverage(spec)).toBe("Показано строк: 3 из 3 доступных");
    expect(humanizeWarning("preview_truncated")).toContain("Предпросмотр усечён");
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
      const originalPreview = structuredClone(chartPreview);
      const chart = chartSpec(kind, encoding);
      const dark = buildChartOption(chart, chartPreview, "dark");
      const light = buildChartOption(chart, chartPreview, "light");
      for (const option of [dark, light]) {
        expect(option).not.toBeNull();
        const series = Array.isArray(option?.series) ? option.series : [option?.series];
        expect(series[0]).toMatchObject({ type: expectedSeriesType });
      }
      expect(light).toMatchObject({ backgroundColor: "#ffffff", darkMode: false, textStyle: { color: "#17243b" }, tooltip: { backgroundColor: "rgba(255, 255, 255, 0.97)", textStyle: { color: "#17243b" } } });
      expect(dark).toMatchObject({ backgroundColor: "#0c1220", darkMode: true, textStyle: { color: "#edf3ff" } });
      expect(JSON.stringify(light?.dataset)).toEqual(JSON.stringify(dark?.dataset));
      const lightSeries = Array.isArray(light?.series) ? light.series : [light?.series];
      const darkSeries = Array.isArray(dark?.series) ? dark.series : [dark?.series];
      expect(lightSeries.map(series => series && "encode" in series ? series.encode : undefined)).toEqual(darkSeries.map(series => series && "encode" in series ? series.encode : undefined));
      if (kind === "treemap") {
        expect(lightSeries[0]).toMatchObject({ label: { color: getChartColors("light").insideLabel } });
        expect(darkSeries[0]).toMatchObject({ label: { color: getChartColors("dark").insideLabel } });
      }
      expect(chartPreview).toEqual(originalPreview);
      // Prevent a dark palette leaking into any of the supported light renderers.
      expect(JSON.stringify(light)).not.toMatch(/#0c1220|#9bb0d3|#24324d|#0b1d33/);
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

  it("uses each combo series metric in the tooltip", () => {
    const chartPreview = {
      columns: ["day", "total_revenue", "orders_count"],
      rows: [["2026-05-24", 68271.31, 29]],
    };
    const option = buildChartOption(
      chartSpec("combo_line_bar", { x: "day", y: ["total_revenue", "orders_count"] }),
      chartPreview,
    );
    const formatter = (option?.tooltip as TooltipWithFormatter | undefined)?.formatter;

    const tooltip = formatter?.([
      {
        data: { day: "2026-05-24", total_revenue: 68271.31, orders_count: 29 },
        dimensionNames: chartPreview.columns,
        encode: { x: [0], y: [1] },
        seriesName: "total_revenue",
      },
      {
        data: { day: "2026-05-24", total_revenue: 68271.31, orders_count: 29 },
        dimensionNames: chartPreview.columns,
        encode: { x: [0], y: [2] },
        seriesName: "orders_count",
      },
    ]);

    expect(tooltip).toContain("total_revenue");
    expect(tooltip).toContain("orders_count");
    expect(tooltip).toContain("29");
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
  it("retains real ECharts zoom and hidden series when applying the light palette", () => {
    // SVG SSR exercises the real option merge and renderer without a browser or GPU.
    setPlatformAPI({ measureText: text => ({ width: String(text).length * 7 }) });
    const chart = init(null, undefined, { renderer: "svg", ssr: true, width: 800, height: 400 });
    const payload = chartSpec("line", { x: "day", y: ["gmv"], series: "segment" }, { transform: { ...spec.transform, limit: 200 } });
    const data: QueryPreview = { columns: ["day", "gmv", "segment"], rows: Array.from({ length: 120 }, (_, index) => [new Date(Date.UTC(2026, 0, Math.floor(index / 2) + 1)).toISOString(), index + 10, index % 2 ? "B" : "A"]) };
    try {
      chart.setOption(buildChartOption(payload, data, "dark")!, { notMerge: true });
      chart.dispatchAction({ type: "dataZoom", dataZoomIndex: 0, start: 20, end: 80 });
      chart.dispatchAction({ type: "legendUnSelect", name: "A" });
      chart.setOption(buildChartOption(payload, data, "light")!, { notMerge: false });
      const option = chart.getOption();
      expect(option.dataZoom).toEqual(expect.arrayContaining([expect.objectContaining({ start: 20, end: 80 })]));
      expect(option.legend).toEqual(expect.arrayContaining([expect.objectContaining({ selected: expect.objectContaining({ A: false }) })]));
      const svg = chart.renderToSVGString();
      expect(svg).toContain('fill="#ffffff"');
      expect(svg).not.toContain('fill="#edf3ff"');
      expect(svg).toContain("B");
    } finally {
      chart.dispose();
    }
  });

});
