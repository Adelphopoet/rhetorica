import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const chartApi = {
  clear: vi.fn(),
  dispose: vi.fn(),
  getDataURL: vi.fn(() => "data:image/png;base64,mock"),
  resize: vi.fn(),
  setOption: vi.fn(),
};

vi.mock("echarts", () => ({
  init: vi.fn(() => chartApi),
}));

import { VisualizationChart } from "./VisualizationChart";
import type { VisualizationSpec } from "./types";

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
  annotations: [],
  warnings: [],
  rationale: {
    why_kind: "Detected time column with metric.",
    why_fields: ["day as x", "gmv as y"],
  },
};

describe("VisualizationChart", () => {
  beforeEach(() => {
    chartApi.clear.mockClear();
    chartApi.dispose.mockClear();
    chartApi.getDataURL.mockClear();
    chartApi.resize.mockClear();
    chartApi.setOption.mockClear();
    document.body.style.overflow = "";
  });

  it("opens the chart in a fullscreen overlay and restores scroll on close", async () => {
    render(<VisualizationChart spec={spec} preview={preview} />);

    fireEvent.click(screen.getByRole("button", { name: /expand/i }));

    expect(await screen.findByRole("dialog", { name: /expanded chart view/i })).toBeInTheDocument();
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.click(screen.getByRole("button", { name: /close expanded chart/i }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /expanded chart view/i })).not.toBeInTheDocument();
    });
    expect(document.body.style.overflow).toBe("");
  });
});
