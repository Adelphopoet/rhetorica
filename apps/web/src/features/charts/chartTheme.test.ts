// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import postcss from "postcss";
import tailwindcss from "tailwindcss";
import { describe, expect, it } from "vitest";
import tailwindConfig from "../../../tailwind.config";
import { getChartColors } from "./chartTheme";

function luminance(hex: string): number {
  const rgb = hex.slice(1).match(/.{2}/g)!.map(channel => parseInt(channel, 16) / 255);
  const linear = rgb.map(channel => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4);
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
}
function contrast(first: string, second: string): number {
  const [low, high] = [luminance(first), luminance(second)].sort((a, b) => a - b);
  return (high + 0.05) / (low + 0.05);
}

describe.each(["dark", "light"] as const)("%s chart palette", theme => {
  const colors = getChartColors(theme);
  it.each(colors.palette)("keeps the treemap label readable on %s", fill => {
    expect(contrast(colors.insideLabel, fill)).toBeGreaterThanOrEqual(4.5);
  });
  it.each(["text", "secondary", "axis"] as const)("keeps %s readable on the chart canvas", text => {
    expect(contrast(colors[text], colors.background)).toBeGreaterThanOrEqual(4.5);
  });
});

it("generates the actual fullscreen overlay and canvas shadows with theme tokens", async () => {
  const content = ["src/features/charts/VisualizationChart.tsx", "src/features/scenarios/WorkflowCanvas.tsx"].map(file => ({ raw: readFileSync(resolve(process.cwd(), file), "utf8"), extension: "tsx" }));
  const result = await postcss([tailwindcss({ ...tailwindConfig, content })]).process("@tailwind utilities;", { from: undefined });
  expect(result.css).toContain("rgb(var(--color-overlay) / 0.84)");
  expect(result.css).toContain("0 28px 120px rgb(var(--color-shadow)/calc(0.65 * var(--shadow-opacity-scale)))");
  expect(result.css).toContain("0 6px 24px rgb(var(--color-shadow)/calc(0.2 * var(--shadow-opacity-scale)))");
});
