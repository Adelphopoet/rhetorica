// Canvas renderers need resolved color values; CSS variables are not interpreted by ECharts.
export type ChartTheme = "light" | "dark";

const darkColors = {
  background: "#0c1220", text: "#edf3ff", secondary: "#a9b8d4", axis: "#9bb0d3",
  border: "#24324d", axisLine: "#32415c", grid: "rgba(50, 65, 92, 0.45)",
  tooltip: "rgba(8, 12, 22, 0.94)", zoom: "rgba(10, 17, 29, 0.8)",
  zoomFill: "rgba(93, 168, 255, 0.18)", success: "#38c67d", warning: "#f2b14b",
  palette: ["#5da8ff", "#38c67d", "#f2b14b", "#ff8b95", "#7d8cff", "#53d7c2"],
  heatmap: ["#0b1d33", "#5da8ff", "#f2b14b"],
  insideLabel: "#061224",
};

export type ChartColors = typeof darkColors;

const lightColors: ChartColors = {
  background: "#ffffff", text: "#17243b", secondary: "#415575", axis: "#526581",
  border: "#c7d3e3", axisLine: "#879bb8", grid: "rgba(135, 155, 184, 0.3)",
  tooltip: "rgba(255, 255, 255, 0.97)", zoom: "#edf2f8",
  zoomFill: "rgba(29, 78, 216, 0.16)", success: "#147d47", warning: "#9a5b00",
  palette: ["#2563b8", "#147d47", "#a86600", "#be344b", "#6452c3", "#087f78"],
  heatmap: ["#edf3ff", "#2563b8", "#9a5b00"],
  insideLabel: "#ffffff",
};

export function getChartColors(theme: ChartTheme): ChartColors {
  return theme === "light" ? lightColors : darkColors;
}
