import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const currentTheme = vi.hoisted(() => ({ resolvedTheme: "dark" as "dark" | "light" }));
vi.mock("@/features/theme/ThemeProvider", () => ({ useTheme: () => currentTheme }));

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
import * as echarts from "echarts";
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
  annotations: [],
  warnings: [],
  rationale: {
    why_kind: "Detected time column with metric",
    why_fields: ["day as x", "gmv as y"],
  },
};

describe("VisualizationChart", () => {
  beforeEach(() => {
    currentTheme.resolvedTheme = "dark";
    vi.mocked(echarts.init).mockClear();
    chartApi.clear.mockClear();
    chartApi.dispose.mockClear();
    chartApi.getDataURL.mockClear();
    chartApi.resize.mockClear();
    chartApi.setOption.mockClear();
    document.body.style.overflow = "";
  });

  it("opens the chart in a fullscreen overlay and restores scroll on close", async () => {
    render(<VisualizationChart spec={spec} preview={preview} />);

    fireEvent.click(screen.getByRole("button", { name: /развернуть/i }));

    expect(await screen.findByRole("dialog", { name: /развёрнутый вид графика/i })).toBeInTheDocument();
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.click(screen.getByRole("button", { name: /закрыть развёрнутый график/i }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /развёрнутый вид графика/i })).not.toBeInTheDocument();
    });
    expect(document.body.style.overflow).toBe("");
  });
  it("updates an existing canvas palette without replacing interactive zoom and legend state", () => {
    const view = render(<VisualizationChart spec={spec} preview={preview} />);
    expect(echarts.init).toHaveBeenCalledTimes(1);
    expect(chartApi.setOption).toHaveBeenLastCalledWith(expect.objectContaining({ backgroundColor: "#0c1220" }), { notMerge: true });
    currentTheme.resolvedTheme = "light";
    view.rerender(<VisualizationChart spec={spec} preview={preview} />);
    expect(echarts.init).toHaveBeenCalledTimes(1);
    expect(chartApi.dispose).not.toHaveBeenCalled();
    expect(chartApi.setOption).toHaveBeenLastCalledWith(expect.objectContaining({ backgroundColor: "#ffffff" }), { notMerge: false });
    view.rerender(<VisualizationChart spec={spec} preview={{ ...preview, rows: [["2026-03-04", 40]] }} />);
    expect(chartApi.setOption).toHaveBeenLastCalledWith(expect.objectContaining({ backgroundColor: "#ffffff" }), { notMerge: true });
    view.unmount();
    expect(chartApi.dispose).toHaveBeenCalledTimes(1);
  });

  it.each(["dark", "light"] as const)("exports PNG in the %s theme, including the expanded portal", async (theme) => {
    currentTheme.resolvedTheme = theme;
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const view = render(<VisualizationChart spec={spec} preview={preview} />);
    fireEvent.click(screen.getByRole("button", { name: "PNG" }));
    expect(chartApi.getDataURL).toHaveBeenLastCalledWith({ type: "png", pixelRatio: 2, backgroundColor: theme === "light" ? "#ffffff" : "#0c1220" });
    fireEvent.click(screen.getByRole("button", { name: /развернуть/i }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    currentTheme.resolvedTheme = theme === "light" ? "dark" : "light";
    view.rerender(<VisualizationChart spec={spec} preview={preview} />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "PNG" }));
    expect(chartApi.getDataURL).toHaveBeenLastCalledWith(expect.objectContaining({ backgroundColor: theme === "light" ? "#0c1220" : "#ffffff" }));
    expect(click).toHaveBeenCalledTimes(2);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe("");
    click.mockRestore();
  });

  it.each(["dark", "light"] as const)("keeps empty and table fallbacks available in %s", (theme) => {
    currentTheme.resolvedTheme = theme;
    const view = render(<VisualizationChart />);
    expect(screen.getByText("Для этого ответа график недоступен")).toBeInTheDocument();
    view.rerender(<VisualizationChart spec={{ ...spec, kind: "table" }} preview={preview} />);
    expect(screen.getByText("Планировщик оставил табличное представление для этого результата")).toBeInTheDocument();
    expect(echarts.init).not.toHaveBeenCalled();
  });

});
