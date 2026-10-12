import type { EChartsOption } from "echarts";
import { getChartColors, type ChartColors, type ChartTheme } from "./chartTheme";

import type {
  QueryPreview,
  VisualizationAnnotation,
  VisualizationSpec,
  VisualizationValueFormat,
} from "./types";
import { DISPLAY_TIME_ZONE } from "@/lib/dateTime";

const LARGE_THRESHOLD = 2000;
const DATA_ZOOM_THRESHOLD = 50;
const MAX_DIMENSION_CARDINALITY = 50;
const MAX_SERIES_CARDINALITY = 12;

const DEFAULT_NUMBER_LOCALE = "en-US";
const DEFAULT_CURRENCY_LOCALE = "ru-RU";
const DEFAULT_CURRENCY_CODE = "RUB";

type DatasetRow = Record<string, string | number | boolean | null>;
type TooltipParam = {
  data?: DatasetRow;
  dimensionNames?: string[];
  encode?: {
    x?: number[];
    y?: number[];
    value?: number[];
  };
  seriesName?: unknown;
};
export type VisualizationFallbackReason =
  | "unsupported_kind"
  | "unsafe_encoding"
  | "high_cardinality"
  | "empty_preview";

const SUPPORTED_RENDERED_KINDS = new Set<string>([
  "bar",
  "bar_grouped",
  "bar_stacked",
  "line",
  "area",
  "area_stacked",
  "combo_line_bar",
  "scatter",
  "bubble",
  "pie",
  "donut",
  "heatmap",
  "treemap",
  "funnel",
  "histogram",
  "waterfall",
]);
const CARDINALITY_GUARDED_KINDS = new Set<string>([
  "bar",
  "bar_grouped",
  "bar_stacked",
  "pie",
  "donut",
  "treemap",
  "funnel",
  "waterfall",
]);

function normalizeSpec(spec: VisualizationSpec): VisualizationSpec {
  return {
    ...spec,
    family: spec.family ?? null,
    encoding: {
      x: spec.encoding?.x ?? null,
      y: spec.encoding?.y ?? [],
      series: spec.encoding?.series ?? null,
      color: spec.encoding?.color ?? null,
      size: spec.encoding?.size ?? null,
      facet: spec.encoding?.facet ?? null,
      label: spec.encoding?.label ?? null,
      tooltip: spec.encoding?.tooltip ?? [],
      stack: spec.encoding?.stack ?? null,
      secondary_y: spec.encoding?.secondary_y ?? [],
    },
    transform: {
      sort: spec.transform?.sort ?? null,
      limit: spec.transform?.limit ?? 50,
      aggregation: spec.transform?.aggregation ?? "none",
      top_n: spec.transform?.top_n ?? null,
      bin_size: spec.transform?.bin_size ?? null,
      window: spec.transform?.window ?? null,
      pivot: spec.transform?.pivot ?? null,
      normalize: spec.transform?.normalize ?? "none",
      cumulative: spec.transform?.cumulative ?? false,
    },
    annotations: spec.annotations ?? [],
    warnings: spec.warnings ?? [],
  };
}

function toDatasetRows(preview: QueryPreview): DatasetRow[] {
  return preview.rows.map((row) => {
    const record: DatasetRow = {};
    preview.columns.forEach((column, index) => {
      record[column] = row[index] ?? null;
    });
    return record;
  });
}

function uniqueValues(rows: DatasetRow[], column: string): Array<string | number | boolean> {
  const keys = new Set<string>();
  const values: Array<string | number | boolean> = [];
  for (const row of rows) {
    const value = row[column];
    if (value === null) {
      continue;
    }
    if (typeof value !== "string" && typeof value !== "number" && typeof value !== "boolean") {
      continue;
    }
    const key = `${typeof value}:${String(value)}`;
    if (keys.has(key)) {
      continue;
    }
    keys.add(key);
    values.push(value);
  }
  return values;
}

function uniqueFields(fields: Array<string | null | undefined>): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const field of fields) {
    if (!field || seen.has(field)) {
      continue;
    }
    seen.add(field);
    result.push(field);
  }
  return result;
}

function isIsoDateString(value: string): boolean {
  if (value.length < 8) {
    return false;
  }
  const candidate = value.trim().replace(" ", "T");
  const normalized = candidate.endsWith("Z")
    ? `${candidate.slice(0, -1)}+00:00`
    : candidate;
  return !Number.isNaN(Date.parse(normalized));
}

function parseTimeValue(value: unknown): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value !== "string") {
    return null;
  }
  const candidate = value.trim().replace(" ", "T");
  const normalized = candidate.endsWith("Z")
    ? `${candidate.slice(0, -1)}+00:00`
    : candidate;
  const parsed = Date.parse(normalized);
  return Number.isNaN(parsed) ? null : parsed;
}

function compareByColumn(
  left: DatasetRow,
  right: DatasetRow,
  column: string,
  axisType: "time" | "value" | "category",
  direction: 1 | -1,
): number {
  const leftRaw = left[column];
  const rightRaw = right[column];

  if (leftRaw === null && rightRaw === null) {
    return 0;
  }
  if (leftRaw === null) {
    return 1;
  }
  if (rightRaw === null) {
    return -1;
  }

  if (axisType === "value") {
    const leftValue = Number(leftRaw);
    const rightValue = Number(rightRaw);
    if (!Number.isFinite(leftValue) || !Number.isFinite(rightValue)) {
      return 0;
    }
    return direction * (leftValue - rightValue);
  }

  if (axisType === "time") {
    const leftValue = parseTimeValue(leftRaw);
    const rightValue = parseTimeValue(rightRaw);
    if (leftValue === null || rightValue === null) {
      return 0;
    }
    return direction * (leftValue - rightValue);
  }

  return direction * String(leftRaw).localeCompare(String(rightRaw));
}

function inferAxisType(rows: DatasetRow[], column: string): "time" | "value" | "category" {
  let numericCount = 0;
  let timeCount = 0;
  let nonNullCount = 0;

  for (const row of rows) {
    const value = row[column];
    if (value === null) {
      continue;
    }
    nonNullCount += 1;
    if (typeof value === "number") {
      numericCount += 1;
      continue;
    }
    if (typeof value === "string") {
      if (value.trim() && !Number.isNaN(Number(value))) {
        numericCount += 1;
      }
      if (isIsoDateString(value)) {
        timeCount += 1;
      }
    }
  }

  if (nonNullCount === 0) {
    return "category";
  }
  if (timeCount / nonNullCount >= 0.7) {
    return "time";
  }
  if (numericCount / nonNullCount >= 0.8) {
    return "value";
  }
  return "category";
}

function formatValue(
  value: unknown,
  valueFormat: VisualizationValueFormat,
  metricUnit?: string | null,
): string {
  if (value === null || value === undefined || value === "") {
    return "n/a";
  }
  if (typeof value === "string" && isIsoDateString(value)) {
    return new Intl.DateTimeFormat("en-GB", {
      year: "numeric",
      month: "short",
      day: "2-digit",
      timeZone: DISPLAY_TIME_ZONE,
    }).format(new Date(value));
  }
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) {
    return String(value);
  }

  let formatted: string;
  if (valueFormat === "integer") {
    formatted = new Intl.NumberFormat(DEFAULT_NUMBER_LOCALE, {
      maximumFractionDigits: 0,
    }).format(numeric);
  } else if (valueFormat === "percent") {
    const scaled = Math.abs(numeric) <= 1 ? numeric : numeric / 100;
    formatted = new Intl.NumberFormat(DEFAULT_NUMBER_LOCALE, {
      style: "percent",
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    }).format(scaled);
  } else if (valueFormat === "currency") {
    formatted = new Intl.NumberFormat(DEFAULT_CURRENCY_LOCALE, {
      style: "currency",
      currency: DEFAULT_CURRENCY_CODE,
      maximumFractionDigits: 2,
    }).format(numeric);
  } else if (valueFormat === "decimal") {
    formatted = new Intl.NumberFormat(DEFAULT_NUMBER_LOCALE, {
      maximumFractionDigits: 2,
    }).format(numeric);
  } else {
    formatted = new Intl.NumberFormat(DEFAULT_NUMBER_LOCALE, {
      maximumFractionDigits: Number.isInteger(numeric) ? 0 : 2,
    }).format(numeric);
  }

  return metricUnit && valueFormat !== "currency" ? `${formatted} ${metricUnit}` : formatted;
}

function aggregateRows(rows: DatasetRow[], spec: VisualizationSpec): DatasetRow[] {
  if (spec.transform.aggregation === "none") {
    return [...rows];
  }

  const groupFields = uniqueFields([
    spec.encoding.x,
    spec.encoding.series,
    spec.encoding.color,
  ]);
  const metricFields = uniqueFields([...spec.encoding.y, ...spec.encoding.secondary_y]);
  if (!groupFields.length || !metricFields.length) {
    return rows;
  }

  const buckets = new Map<
    string,
    {
      row: DatasetRow;
      counts: Record<string, number>;
      sums: Record<string, number>;
    }
  >();

  for (const row of rows) {
    const keyValues = groupFields.map((field) => row[field]);
    const key = JSON.stringify(keyValues);
    const bucket =
      buckets.get(key) ??
      (() => {
        const baseRow: DatasetRow = {};
        groupFields.forEach((field, index) => {
          baseRow[field] = keyValues[index] ?? null;
        });
        const created: {
          row: DatasetRow;
          counts: Record<string, number>;
          sums: Record<string, number>;
        } = { row: baseRow, counts: {}, sums: {} };
        buckets.set(key, created);
        return created;
      })();

    for (const metric of metricFields) {
      const value = Number(row[metric]);
      if (spec.transform.aggregation === "count") {
        bucket.counts[metric] = (bucket.counts[metric] ?? 0) + (row[metric] === null ? 0 : 1);
        continue;
      }
      if (!Number.isFinite(value)) {
        continue;
      }
      bucket.sums[metric] = (bucket.sums[metric] ?? 0) + value;
      bucket.counts[metric] = (bucket.counts[metric] ?? 0) + 1;
    }
  }

  return [...buckets.values()].map((bucket) => {
    for (const metric of metricFields) {
      if (spec.transform.aggregation === "count") {
        bucket.row[metric] = bucket.counts[metric] ?? 0;
      } else if (spec.transform.aggregation === "avg") {
        const count = bucket.counts[metric] ?? 0;
        bucket.row[metric] = count > 0 ? (bucket.sums[metric] ?? 0) / count : null;
      } else {
        bucket.row[metric] = bucket.sums[metric] ?? null;
      }
    }
    return bucket.row;
  });
}

function applyTransform(rows: DatasetRow[], spec: VisualizationSpec): DatasetRow[] {
  const nextRows = aggregateRows(rows, spec);
  const x = spec.encoding.x;
  const metric = spec.encoding.y[0];
  const rowLimit = spec.transform.top_n ?? spec.transform.limit;

  if (spec.transform.sort && metric) {
    const direction: 1 | -1 = spec.transform.sort === "asc" ? 1 : -1;
    if (spec.kind === "line" && x) {
      const xAxisType = inferAxisType(nextRows, x);
      nextRows.sort((left, right) => compareByColumn(left, right, x, xAxisType, direction));
    } else {
      nextRows.sort((left, right) => compareByColumn(left, right, metric, "value", direction));
    }
  } else if (spec.transform.sort && x) {
    const direction: 1 | -1 = spec.transform.sort === "asc" ? 1 : -1;
    const xAxisType = inferAxisType(nextRows, x);
    nextRows.sort((left, right) => compareByColumn(left, right, x, xAxisType, direction));
  }

  return nextRows.slice(0, Math.max(1, rowLimit || rows.length));
}

function annotationColor(annotation: VisualizationAnnotation, colors: ChartColors): string {
  if (annotation.style === "success") {
    return colors.success;
  }
  if (annotation.style === "warn") {
    return colors.warning;
  }
  return colors.secondary;
}

function buildMarkLine(spec: VisualizationSpec, colors: ChartColors) {
  if (!spec.annotations.length) {
    return undefined;
  }
  const data = spec.annotations
    .filter((annotation) => annotation.axis === "y")
    .map((annotation) => ({
      yAxis: annotation.value,
      label: {
        formatter: annotation.label,
        color: annotationColor(annotation, colors),
      },
      lineStyle: {
        color: annotationColor(annotation, colors),
        type: "dashed" as const,
        width: 1.2,
      },
    }));

  return data.length
    ? {
        symbol: "none",
        silent: true,
        precision: 4,
        data,
      }
    : undefined;
}

function buildAxisLabelFormatter(
  spec: VisualizationSpec,
  axis: "x" | "y",
  axisType: "time" | "value" | "category",
) {
  if (axis === "x" && axisType === "time") {
    return (value: string | number) =>
      new Intl.DateTimeFormat("en-GB", {
        day: "2-digit",
        month: "short",
        timeZone: DISPLAY_TIME_ZONE,
      }).format(new Date(Number(value)));
  }
  if (axis === "x" && axisType !== "value") {
    return (value: string | number) =>
      formatValue(axisType === "time" ? new Date(Number(value)).toISOString() : value, "auto");
  }
  if (axis === "y" || axisType === "value") {
    return (value: string | number) => formatValue(value, spec.value_format, spec.metric_unit);
  }
  return undefined;
}

function buildTooltipFormatter(spec: VisualizationSpec) {
  return (params: unknown) => {
    const items = Array.isArray(params) ? params : [params];
    const lines: string[] = [];
    const metricFields = uniqueFields([...spec.encoding.y, ...spec.encoding.secondary_y]);
    const hasMultipleMetrics = metricFields.length > 1;

    for (const item of items) {
      if (!item || typeof item !== "object") {
        continue;
      }
      const tooltipItem = item as TooltipParam;
      const data = tooltipItem.data;
      const seriesName = String(tooltipItem.seriesName ?? "");
      if (!data) {
        continue;
      }

      const xKey = spec.encoding.x;
      const encodedYIndex = tooltipItem.encode?.y?.[0] ?? tooltipItem.encode?.value?.[0];
      const encodedYKey =
        encodedYIndex !== undefined ? tooltipItem.dimensionNames?.[encodedYIndex] : undefined;
      const yKey =
        encodedYKey && Object.prototype.hasOwnProperty.call(data, encodedYKey)
          ? encodedYKey
          : seriesName && Object.prototype.hasOwnProperty.call(data, seriesName)
            ? seriesName
            : spec.encoding.y[0];
      const seriesKey = spec.encoding.series;

      if (xKey && lines.length === 0) {
        lines.push(`${spec.x_axis_label ?? xKey}: ${formatValue(data[xKey], "auto")}`);
      }
      if (seriesKey && data[seriesKey] !== null) {
        lines.push(`${seriesKey}: ${String(data[seriesKey])}`);
      } else if (seriesName && spec.encoding.series) {
        lines.push(`${spec.encoding.series}: ${seriesName}`);
      }
      if (yKey) {
        lines.push(
          `${hasMultipleMetrics ? yKey : spec.y_axis_label ?? yKey}: ${formatValue(
            data[yKey],
            spec.value_format,
            spec.metric_unit,
          )}`,
        );
      }
    }

    return lines.join("<br/>");
  };
}

function buildCartesianOption(
  kind:
    | "line"
    | "area"
    | "bar"
    | "bar_grouped"
    | "bar_stacked"
    | "area_stacked"
    | "combo_line_bar",
  rows: DatasetRow[],
  spec: VisualizationSpec,
  colors: ChartColors,
): EChartsOption | null {
  const x = spec.encoding.x;
  const metrics = spec.encoding.y;
  if (!x || metrics.length === 0) {
    return null;
  }

  const xAxisType =
    kind === "bar" || kind === "bar_grouped" || kind === "bar_stacked"
      ? "category"
      : kind === "combo_line_bar"
        ? (inferAxisType(rows, x) === "time" ? "time" : "category")
        : inferAxisType(rows, x);
  const seriesField = spec.encoding.series;
  const primaryMetrics =
    kind === "combo_line_bar" && spec.encoding.secondary_y.length === 0 && metrics.length > 1
      ? [metrics[0] as string]
      : metrics;
  const secondaryMetrics =
    kind === "combo_line_bar" && spec.encoding.secondary_y.length === 0 && metrics.length > 1
      ? metrics.slice(1)
      : spec.encoding.secondary_y;
  const enableLarge = rows.length > LARGE_THRESHOLD;
  const withZoom = rows.length > DATA_ZOOM_THRESHOLD;
  const markLine = buildMarkLine(spec, colors);

  const buildSeries = (
    datasetId: string,
    metric: string,
    name: string,
    color: string,
    yAxisIndex = 0,
  ) => {
    const isLineLike =
      kind === "line" ||
      kind === "area" ||
      kind === "area_stacked" ||
      kind === "combo_line_bar";
    const isAreaLike = kind === "area" || kind === "area_stacked";
    const isComboSecondary = kind === "combo_line_bar" && yAxisIndex === 1;
    const isBarLike =
      kind === "bar" ||
      kind === "bar_grouped" ||
      kind === "bar_stacked" ||
      (kind === "combo_line_bar" && yAxisIndex === 0);
    if (isLineLike && !isComboSecondary && !isBarLike) {
      return {
        type: "line" as const,
        name,
        datasetId,
        encode: { x, y: metric, tooltip: [x, metric] },
        sampling: enableLarge ? ("lttb" as const) : undefined,
        showSymbol: rows.length <= 40,
        symbolSize: 8,
        smooth: true,
        areaStyle: isAreaLike ? { opacity: 0.18 } : undefined,
        lineStyle: { width: 2.5, color },
        itemStyle: { color },
        stack: kind === "area_stacked" ? spec.encoding.stack ?? "total" : undefined,
        yAxisIndex,
        emphasis: { focus: "series" as const },
        markLine,
      };
    }

    return {
      type: isComboSecondary ? ("line" as const) : ("bar" as const),
      name,
      datasetId,
      encode: { x, y: metric, tooltip: [x, metric] },
      large: enableLarge,
      largeThreshold: LARGE_THRESHOLD,
      itemStyle: {
        color,
        borderRadius: [10, 10, 0, 0],
      },
      barMaxWidth: 42,
      stack: kind === "bar_stacked" ? spec.encoding.stack ?? "total" : undefined,
      yAxisIndex,
      emphasis: { focus: "series" as const },
      markLine,
    };
  };

  const baseOption: EChartsOption = {
    aria: { enabled: true },
    backgroundColor: colors.background,
    textStyle: { color: colors.text },
    darkMode: colors.background !== "#ffffff",
    color: colors.palette,
    animationDuration: 350,
    grid: { top: 32, left: 20, right: 20, bottom: withZoom ? 72 : 28, containLabel: true },
    tooltip: {
      backgroundColor: colors.tooltip, borderColor: colors.border, textStyle: { color: colors.text },
      trigger: "axis",
      formatter: buildTooltipFormatter(spec),
    },
    xAxis: {
      type: xAxisType,
      name: spec.x_axis_label ?? undefined,
      nameLocation: "middle",
      nameGap: 28,
      axisLabel: {
        color: colors.axis,
        hideOverlap: true,
        margin: 14,
        rotate: xAxisType === "time" ? 18 : 0,
        formatter: buildAxisLabelFormatter(spec, "x", xAxisType),
      },
      axisLine: { lineStyle: { color: colors.axisLine } },
      splitLine: { show: false },
      splitNumber: xAxisType === "time" ? Math.min(4, rows.length) : undefined,
    },
    yAxis: [
      {
        type: "value" as const,
        name: spec.y_axis_label ?? undefined,
        nameTextStyle: { color: colors.axis, padding: [0, 0, 8, 0] },
        axisLabel: {
          color: colors.axis,
          formatter: buildAxisLabelFormatter(spec, "y", "value"),
        },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: colors.grid } },
      },
      ...(secondaryMetrics.length
        ? [
            {
              type: "value" as const,
              name: secondaryMetrics[0] ?? undefined,
              nameTextStyle: { color: colors.axis, padding: [0, 0, 8, 0] },
              axisLabel: {
                color: colors.axis,
                formatter: (value: string | number) =>
                  formatValue(value, spec.value_format, spec.metric_unit),
              },
              splitLine: { show: false },
            },
          ]
        : []),
    ],
    dataZoom: withZoom
      ? [
          { type: "inside", xAxisIndex: [0] },
          {
            type: "slider",
            textStyle: { color: colors.axis },
            handleStyle: { color: colors.axis },
            xAxisIndex: [0],
            height: 20,
            bottom: 12,
            borderColor: colors.border,
            backgroundColor: colors.zoom,
            fillerColor: colors.zoomFill,
          },
        ]
      : [],
  };

  if (!seriesField) {
    const primarySeries = primaryMetrics.map((metric, index) =>
      buildSeries(
        "raw",
        metric,
        metric,
        colors.palette[index % colors.palette.length],
        0,
      ),
    );
    const secondarySeries = secondaryMetrics.map((metric, index) =>
      buildSeries(
        "raw",
        metric,
        metric,
        colors.palette[(primaryMetrics.length + index) % colors.palette.length],
        1,
      ),
    );
    return {
      ...baseOption,
      dataset: [{ id: "raw", source: rows }],
      series: [...primarySeries, ...secondarySeries],
    };
  }

  const categories = uniqueValues(rows, seriesField).slice(0, 12);
  if (categories.length === 0) {
    return null;
  }

  return {
    ...baseOption,
    legend: {
      top: 0,
      type: "scroll",
      pageTextStyle: { color: colors.secondary },
      pageIconColor: colors.axis,
      pageIconInactiveColor: colors.border,
      inactiveColor: colors.axis,
      textStyle: { color: colors.secondary },
    },
    dataset: [
      { id: "raw", source: rows },
      ...categories.map((value, index) => ({
        id: `filtered-${index}`,
        fromDatasetId: "raw",
        transform: {
          type: "filter",
          config: {
            dimension: seriesField,
            "=": value,
          },
        },
      })),
    ],
    series: categories.map((value, index) =>
      buildSeries(
        `filtered-${index}`,
        primaryMetrics[0] as string,
        String(value),
        colors.palette[index % colors.palette.length],
        0,
      ),
    ),
  };
}

function buildScatterOption(rows: DatasetRow[], spec: VisualizationSpec, colors: ChartColors): EChartsOption | null {
  const x = spec.encoding.x;
  const metric = spec.encoding.y[0];
  if (!x || !metric) {
    return null;
  }
  const withZoom = rows.length > DATA_ZOOM_THRESHOLD;

  return {
    aria: { enabled: true },
    backgroundColor: colors.background,
    textStyle: { color: colors.text },
    darkMode: colors.background !== "#ffffff",
    color: colors.palette,
    animationDuration: 350,
    grid: { top: 28, left: 20, right: 20, bottom: withZoom ? 72 : 28, containLabel: true },
    dataset: [{ id: "raw", source: rows }],
    tooltip: {
      backgroundColor: colors.tooltip, borderColor: colors.border, textStyle: { color: colors.text },
      trigger: "item",
      formatter: buildTooltipFormatter(spec),
    },
    xAxis: {
      type: "value",
      name: spec.x_axis_label ?? undefined,
      nameLocation: "middle",
      nameGap: 28,
      axisLabel: {
        color: colors.axis,
        formatter: (value: string | number) => formatValue(value, "decimal"),
      },
      splitLine: { lineStyle: { color: colors.grid } },
    },
    yAxis: {
      type: "value",
      name: spec.y_axis_label ?? undefined,
      nameTextStyle: { color: colors.axis, padding: [0, 0, 8, 0] },
      axisLabel: {
        color: colors.axis,
        formatter: (value: string | number) => formatValue(value, spec.value_format, spec.metric_unit),
      },
      splitLine: { lineStyle: { color: colors.grid } },
    },
    dataZoom: withZoom
      ? [{ type: "inside", xAxisIndex: [0] }, { type: "slider", xAxisIndex: [0], height: 20, bottom: 12, borderColor: colors.border, backgroundColor: colors.zoom, fillerColor: colors.zoomFill, textStyle: { color: colors.axis }, handleStyle: { color: colors.axis } }]
      : [],
    series: [
      {
        type: "scatter",
        datasetId: "raw",
        encode: { x, y: metric, tooltip: [x, metric] },
        large: rows.length > LARGE_THRESHOLD,
        largeThreshold: LARGE_THRESHOLD,
        symbolSize:
          spec.kind === "bubble" && spec.encoding.size
            ? (value: Array<string | number | boolean | null> | DatasetRow) => {
                const raw =
                  Array.isArray(value)
                    ? (() => {
                        const sizeIndex = previewDimensionIndex(rows[0], spec.encoding.size);
                        return sizeIndex >= 0 ? Number(value[sizeIndex]) : NaN;
                      })()
                    : Number(value[spec.encoding.size as string] ?? NaN);
                if (!Number.isFinite(raw)) {
                  return 12;
                }
                return Math.min(42, Math.max(12, raw));
              }
            : 12,
        itemStyle: { color: colors.palette[0], opacity: 0.82 },
        emphasis: { itemStyle: { borderColor: colors.text, borderWidth: 1 } },
      },
    ],
  };
}

function buildPieOption(rows: DatasetRow[], spec: VisualizationSpec, colors: ChartColors): EChartsOption | null {
  const x = spec.encoding.x;
  const metric = spec.encoding.y[0];
  if (!x || !metric) {
    return null;
  }

  return {
    aria: { enabled: true },
    backgroundColor: colors.background,
    textStyle: { color: colors.text },
    darkMode: colors.background !== "#ffffff",
    color: colors.palette,
    animationDuration: 350,
    tooltip: {
      backgroundColor: colors.tooltip, borderColor: colors.border, textStyle: { color: colors.text },
      trigger: "item",
      formatter: buildTooltipFormatter(spec),
    },
    legend: {
      type: "scroll",
      pageTextStyle: { color: colors.secondary },
      pageIconColor: colors.axis,
      pageIconInactiveColor: colors.border,
      inactiveColor: colors.axis,
      bottom: 0,
      textStyle: { color: colors.secondary },
    },
    dataset: [{ id: "raw", source: rows }],
    series: [
      {
        type: "pie",
        datasetId: "raw",
        radius: spec.kind === "donut" ? ["38%", "72%"] : ["0%", "72%"],
        top: 12,
        bottom: 32,
        avoidLabelOverlap: true,
        itemStyle: {
          borderColor: colors.background,
          borderWidth: 2,
        },
        label: {
          color: colors.text,
          formatter: "{b}\n{d}%",
        },
        encode: {
          itemName: x,
          value: metric,
          tooltip: [x, metric],
        },
      },
    ],
  };
}

function previewDimensionIndex(row: DatasetRow | undefined, field: string | null): number {
  if (!row || !field) {
    return -1;
  }
  return Object.keys(row).indexOf(field);
}

function buildHeatmapOption(rows: DatasetRow[], spec: VisualizationSpec, colors: ChartColors): EChartsOption | null {
  const x = spec.encoding.x;
  const yCategory = spec.encoding.color ?? spec.encoding.series;
  const metric = spec.encoding.y[0];
  if (!x || !yCategory || !metric) {
    return null;
  }
  return {
    aria: { enabled: true },
    backgroundColor: colors.background,
    textStyle: { color: colors.text },
    darkMode: colors.background !== "#ffffff",
    color: colors.palette,
    tooltip: {
      backgroundColor: colors.tooltip, borderColor: colors.border, textStyle: { color: colors.text },
      trigger: "item",
      formatter: buildTooltipFormatter(spec),
    },
    grid: { top: 32, left: 20, right: 20, bottom: 36, containLabel: true },
    dataset: [{ id: "raw", source: rows }],
    xAxis: { type: "category", name: spec.x_axis_label ?? undefined, axisLabel: { color: colors.axis }, axisLine: { lineStyle: { color: colors.axisLine } } },
    yAxis: { type: "category", name: yCategory, axisLabel: { color: colors.axis }, axisLine: { lineStyle: { color: colors.axisLine } } },
    visualMap: {
      textStyle: { color: colors.secondary },
      min: 0,
      max: Math.max(
        ...rows.map((row) => Number(row[metric] ?? 0)).filter((value) => Number.isFinite(value)),
        1,
      ),
      calculable: true,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      inRange: { color: colors.heatmap },
    },
    series: [
      {
        type: "heatmap",
        datasetId: "raw",
        encode: {
          x,
          y: yCategory,
          value: metric,
          tooltip: [x, yCategory, metric],
        },
        label: { show: false },
      },
    ],
  };
}

function buildTreemapOption(rows: DatasetRow[], spec: VisualizationSpec, colors: ChartColors): EChartsOption | null {
  const nameField = spec.encoding.x;
  const metric = spec.encoding.y[0];
  if (!nameField || !metric) {
    return null;
  }
  return {
    aria: { enabled: true },
    backgroundColor: colors.background,
    textStyle: { color: colors.text },
    darkMode: colors.background !== "#ffffff",
    color: colors.palette,
    tooltip: { formatter: buildTooltipFormatter(spec), backgroundColor: colors.tooltip, borderColor: colors.border, textStyle: { color: colors.text } },
    series: [
      {
        type: "treemap",
        label: { color: colors.insideLabel },
        itemStyle: { borderColor: colors.background },
        roam: false,
        nodeClick: false,
        breadcrumb: { show: false },
        data: rows.map((row) => ({
          name: String(row[nameField] ?? "n/a"),
          value: Number(row[metric] ?? 0),
        })),
      },
    ],
  };
}

function buildFunnelOption(rows: DatasetRow[], spec: VisualizationSpec, colors: ChartColors): EChartsOption | null {
  const nameField = spec.encoding.x;
  const metric = spec.encoding.y[0];
  if (!nameField || !metric) {
    return null;
  }
  const funnelSort = spec.transform.sort === "asc" ? "ascending" : "descending";
  return {
    aria: { enabled: true },
    backgroundColor: colors.background,
    textStyle: { color: colors.text },
    darkMode: colors.background !== "#ffffff",
    color: colors.palette,
    tooltip: { formatter: buildTooltipFormatter(spec), backgroundColor: colors.tooltip, borderColor: colors.border, textStyle: { color: colors.text } },
    series: [
      {
        type: "funnel",
        label: { color: colors.text },
        itemStyle: { borderColor: colors.background },
        left: "10%",
        top: 24,
        bottom: 24,
        width: "80%",
        sort: funnelSort,
        data: rows.map((row) => ({
          name: String(row[nameField] ?? "n/a"),
          value: Number(row[metric] ?? 0),
        })),
      },
    ],
  };
}

function buildHistogramOption(rows: DatasetRow[], spec: VisualizationSpec, colors: ChartColors): EChartsOption | null {
  const x = spec.encoding.x;
  const metric = spec.encoding.y[0];
  if (!x || !metric) {
    return null;
  }
  const withZoom = rows.length > DATA_ZOOM_THRESHOLD;

  return {
    aria: { enabled: true },
    backgroundColor: colors.background,
    textStyle: { color: colors.text },
    darkMode: colors.background !== "#ffffff",
    color: [colors.palette[0]],
    animationDuration: 350,
    grid: { top: 28, left: 20, right: 20, bottom: withZoom ? 72 : 40, containLabel: true },
    dataset: [{ id: "raw", source: rows }],
    tooltip: {
      backgroundColor: colors.tooltip, borderColor: colors.border, textStyle: { color: colors.text },
      trigger: "axis",
      formatter: buildTooltipFormatter(spec),
    },
    xAxis: {
      type: "category",
      name: spec.x_axis_label ?? undefined,
      nameLocation: "middle",
      nameGap: 30,
      axisLabel: {
        color: colors.axis,
        hideOverlap: true,
        margin: 14,
        rotate: rows.length > 16 ? 20 : 0,
        formatter: buildAxisLabelFormatter(spec, "x", "category"),
      },
      axisLine: { lineStyle: { color: colors.axisLine } },
    },
    yAxis: {
      type: "value",
      name: spec.y_axis_label ?? metric,
      nameTextStyle: { color: colors.axis, padding: [0, 0, 8, 0] },
      axisLabel: {
        color: colors.axis,
        formatter: buildAxisLabelFormatter(spec, "y", "value"),
      },
      splitLine: { lineStyle: { color: colors.grid } },
    },
    dataZoom: withZoom
      ? [
          { type: "inside", xAxisIndex: [0] },
          {
            type: "slider",
            textStyle: { color: colors.axis },
            handleStyle: { color: colors.axis },
            xAxisIndex: [0],
            height: 20,
            bottom: 12,
            borderColor: colors.border,
            backgroundColor: colors.zoom,
            fillerColor: colors.zoomFill,
          },
        ]
      : [],
    series: [
      {
        type: "bar",
        name: metric,
        datasetId: "raw",
        encode: { x, y: metric, tooltip: [x, metric] },
        barCategoryGap: "4%",
        itemStyle: {
          color: colors.palette[0],
          borderRadius: [4, 4, 0, 0],
        },
        emphasis: { focus: "series" as const },
      },
    ],
  };
}

function buildWaterfallOption(rows: DatasetRow[], spec: VisualizationSpec, colors: ChartColors): EChartsOption | null {
  const x = spec.encoding.x;
  const metric = spec.encoding.y[0];
  if (!x || !metric) {
    return null;
  }

  let runningTotal = 0;
  const points = rows.map((row) => {
    const delta = Number(row[metric] ?? 0);
    const safeDelta = Number.isFinite(delta) ? delta : 0;
    const start = runningTotal;
    const end = runningTotal + safeDelta;
    runningTotal = end;
    return {
      label: String(row[x] ?? "n/a"),
      delta: safeDelta,
      offset: Math.min(start, end),
      amount: Math.abs(safeDelta),
      end,
    };
  });
  if (!points.length) {
    return null;
  }
  const axisMin = Math.min(0, ...points.map((point) => Math.min(point.offset, point.end)));
  const axisMax = Math.max(0, ...points.map((point) => Math.max(point.offset + point.amount, point.end)));

  return {
    aria: { enabled: true },
    backgroundColor: colors.background,
    textStyle: { color: colors.text },
    darkMode: colors.background !== "#ffffff",
    animationDuration: 350,
    grid: { top: 28, left: 20, right: 20, bottom: 46, containLabel: true },
    tooltip: {
      backgroundColor: colors.tooltip, borderColor: colors.border, textStyle: { color: colors.text },
      trigger: "axis",
      formatter: (params: unknown) => {
        const items = Array.isArray(params) ? params : [params];
        const first = items.find(
          (item): item is { dataIndex: number } =>
            Boolean(item) && typeof item === "object" && "dataIndex" in item,
        );
        const point = first ? points[first.dataIndex] : null;
        if (!point) {
          return "";
        }
        return [
          `${spec.x_axis_label ?? x}: ${point.label}`,
          `${spec.y_axis_label ?? metric}: ${formatValue(point.delta, spec.value_format, spec.metric_unit)}`,
          `Cumulative: ${formatValue(point.end, spec.value_format, spec.metric_unit)}`,
        ].join("<br/>");
      },
    },
    xAxis: {
      type: "category",
      data: points.map((point) => point.label),
      name: spec.x_axis_label ?? undefined,
      nameLocation: "middle",
      nameGap: 30,
      axisLabel: {
        color: colors.axis,
        hideOverlap: true,
        margin: 14,
        rotate: points.length > 12 ? 18 : 0,
      },
      axisLine: { lineStyle: { color: colors.axisLine } },
    },
    yAxis: {
      type: "value",
      min: axisMin,
      max: axisMax,
      name: spec.y_axis_label ?? metric,
      nameTextStyle: { color: colors.axis, padding: [0, 0, 8, 0] },
      axisLabel: {
        color: colors.axis,
        formatter: buildAxisLabelFormatter(spec, "y", "value"),
      },
      splitLine: { lineStyle: { color: colors.grid } },
    },
    series: [
      {
        name: "Offset",
        type: "bar",
        stack: "waterfall",
        data: points.map((point) => point.offset),
        itemStyle: { color: "transparent", borderColor: "transparent" },
        emphasis: { disabled: true },
        tooltip: { show: false },
      },
      {
        name: metric,
        type: "bar",
        stack: "waterfall",
        data: points.map((point) => ({
          value: point.amount,
          itemStyle: { color: point.delta >= 0 ? colors.palette[1] : colors.palette[3] },
          label: {
            show: true,
            position: point.delta >= 0 ? "top" : "bottom",
            color: colors.text,
            formatter: () =>
              `${point.delta >= 0 ? "+" : ""}${formatValue(
                point.delta,
                spec.value_format,
                spec.metric_unit,
              )}`,
          },
        })),
        barMaxWidth: 48,
        emphasis: { focus: "series" as const },
      },
    ],
  };
}

export function buildChartOption(
  spec: VisualizationSpec | null | undefined,
  preview: QueryPreview | null | undefined,
  theme: ChartTheme = "dark",
): EChartsOption | null {
  const colors = getChartColors(theme);
  if (!spec || !preview) {
    return null;
  }
  if (spec.kind === "table") {
    return null;
  }
  if (!preview.columns.length || !preview.rows.length) {
    return null;
  }

  const normalizedSpec = normalizeSpec(spec);
  if (getVisualizationFallbackReason(normalizedSpec, preview) !== null) {
    return null;
  }

  const transformedRows = applyTransform(toDatasetRows(preview), normalizedSpec);

  if (
    normalizedSpec.kind === "line" ||
    normalizedSpec.kind === "area" ||
    normalizedSpec.kind === "area_stacked" ||
    normalizedSpec.kind === "bar" ||
    normalizedSpec.kind === "bar_grouped" ||
    normalizedSpec.kind === "bar_stacked" ||
    normalizedSpec.kind === "combo_line_bar"
  ) {
    return buildCartesianOption(normalizedSpec.kind, transformedRows, normalizedSpec, colors);
  }
  if (normalizedSpec.kind === "scatter" || normalizedSpec.kind === "bubble") {
    return buildScatterOption(transformedRows, normalizedSpec, colors);
  }
  if (normalizedSpec.kind === "pie" || normalizedSpec.kind === "donut") {
    return buildPieOption(transformedRows, normalizedSpec, colors);
  }
  if (normalizedSpec.kind === "heatmap") {
    return buildHeatmapOption(transformedRows, normalizedSpec, colors);
  }
  if (normalizedSpec.kind === "treemap") {
    return buildTreemapOption(transformedRows, normalizedSpec, colors);
  }
  if (normalizedSpec.kind === "funnel") {
    return buildFunnelOption(transformedRows, normalizedSpec, colors);
  }
  if (normalizedSpec.kind === "histogram") {
    return buildHistogramOption(transformedRows, normalizedSpec, colors);
  }
  if (normalizedSpec.kind === "waterfall") {
    return buildWaterfallOption(transformedRows, normalizedSpec, colors);
  }
  return null;
}

export function getVisualizationFallbackReason(
  spec: VisualizationSpec | null | undefined,
  preview: QueryPreview | null | undefined,
): VisualizationFallbackReason | null {
  if (!spec || !preview || !preview.columns.length || !preview.rows.length) {
    return "empty_preview";
  }
  if (spec.kind === "table") {
    return null;
  }
  if (!SUPPORTED_RENDERED_KINDS.has(spec.kind)) {
    return "unsupported_kind";
  }

  const normalizedSpec = normalizeSpec(spec);
  const columns = new Set(preview.columns);
  const x = normalizedSpec.encoding.x;
  const metrics = normalizedSpec.encoding.y;
  if (!x || !columns.has(x) || metrics.length === 0) {
    return "unsafe_encoding";
  }
  if (metrics.some((metric) => !columns.has(metric))) {
    return "unsafe_encoding";
  }
  if (normalizedSpec.encoding.secondary_y.some((metric) => !columns.has(metric))) {
    return "unsafe_encoding";
  }
  if (normalizedSpec.encoding.series && !columns.has(normalizedSpec.encoding.series)) {
    return "unsafe_encoding";
  }
  if (normalizedSpec.encoding.color && !columns.has(normalizedSpec.encoding.color)) {
    return "unsafe_encoding";
  }
  if (normalizedSpec.encoding.size && !columns.has(normalizedSpec.encoding.size)) {
    return "unsafe_encoding";
  }
  if (normalizedSpec.kind === "heatmap" && !normalizedSpec.encoding.color && !normalizedSpec.encoding.series) {
    return "unsafe_encoding";
  }

  const rows = toDatasetRows(preview);
  if (
    CARDINALITY_GUARDED_KINDS.has(normalizedSpec.kind) &&
    uniqueValues(rows, x).length > MAX_DIMENSION_CARDINALITY
  ) {
    return "high_cardinality";
  }
  if (
    normalizedSpec.encoding.series &&
    uniqueValues(rows, normalizedSpec.encoding.series).length > MAX_SERIES_CARDINALITY
  ) {
    return "high_cardinality";
  }

  return null;
}

export function collectVisualizationDebugWarnings(
  spec: VisualizationSpec | null | undefined,
  preview: QueryPreview | null | undefined,
): string[] {
  const warnings = new Set<string>(spec?.warnings ?? []);
  if (spec?.data_source.is_truncated) {
    warnings.add("truncated_preview");
  }
  const fallbackReason = getVisualizationFallbackReason(spec, preview);
  if (fallbackReason) {
    warnings.add(fallbackReason);
  }
  return [...warnings];
}

export function summarizePreviewCoverage(spec: VisualizationSpec): string {
  const shown = Math.min(spec.transform.top_n ?? spec.transform.limit, spec.data_source.row_count);
  if (spec.data_source.is_truncated) {
    return `Показано строк: ${shown} из усечённого предпросмотра (${spec.data_source.row_count})`;
  }
  return `Показано строк: ${shown} из ${spec.data_source.row_count} доступных`;
}

export function describePlannerMode(spec: VisualizationSpec): string {
  if (spec.planner_mode === "llm_validated") {
    return "Проверено LLM";
  }
  if (spec.planner_mode === "table_fallback") {
    return "Запасной вариант — таблица";
  }
  return "Запасной вариант — правила";
}

export function humanizeWarning(warning: string): string {
  if (warning === "preview_truncated") {
    return "Предпросмотр усечён. Используйте график для оценки тенденции, а не точного охвата";
  }
  if (warning === "truncated_preview") {
    return "Отладка: усечённый предпросмотр";
  }
  if (warning === "unsupported_kind") {
    return "Отладка: неподдерживаемый вид графика";
  }
  if (warning === "unsafe_encoding") {
    return "Отладка: небезопасное кодирование графика";
  }
  if (warning === "high_cardinality") {
    return "Отладка: график с большим количеством уникальных значений";
  }
  if (warning === "empty_preview") {
    return "Отладка: пустой предпросмотр выполнения";
  }
  if (warning === "visualization_llm_parse_failed") {
    return "Планировщик использовал безопасный график, поскольку ответ модели оказался некорректным";
  }
  if (warning === "visualization_llm_validation_failed") {
    return "Планировщик отклонил небезопасное предложение графика и использовал заданный запасной вариант";
  }
  return warning.replace(/_/g, " ");
}
