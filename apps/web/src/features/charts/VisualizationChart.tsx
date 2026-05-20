"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import * as echarts from "echarts";
import { Badge, Button, cn } from "@rhetorica/ui";
import { BarChart3, Download, Maximize2, Minimize2 } from "lucide-react";

import {
  buildChartOption,
  collectVisualizationDebugWarnings,
  describePlannerMode,
  getVisualizationFallbackReason,
  humanizeWarning,
  summarizePreviewCoverage,
} from "./chartAdapter";
import type { QueryPreview, VisualizationSpec } from "./types";

interface VisualizationChartProps {
  spec?: VisualizationSpec | null;
  preview?: QueryPreview | null;
}

interface ChartCanvasProps {
  className: string;
  onChartReady: (chart: echarts.ECharts | null) => void;
  option: echarts.EChartsOption;
}

function goalLabel(goal: VisualizationSpec["goal"]): string {
  if (goal === "trend") {
    return "Trend";
  }
  if (goal === "comparison") {
    return "Comparison";
  }
  if (goal === "composition") {
    return "Composition";
  }
  if (goal === "correlation") {
    return "Correlation";
  }
  if (goal === "distribution") {
    return "Distribution";
  }
  if (goal === "ranking") {
    return "Ranking";
  }
  if (goal === "delta") {
    return "Delta";
  }
  return "Inspection";
}

function ChartCanvas({ className, onChartReady, option }: ChartCanvasProps) {
  const chartContainerRef = useRef<HTMLDivElement | null>(null);
  const chartInstanceRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const container = chartContainerRef.current;
    if (!container) {
      return;
    }

    const chart = echarts.init(container, undefined, {
      renderer: "canvas",
    });
    chartInstanceRef.current = chart;
    onChartReady(chart);

    const resizeObserver = new ResizeObserver(() => {
      chart.resize();
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chartInstanceRef.current = null;
      onChartReady(null);
      chart.dispose();
    };
  }, [onChartReady]);

  useEffect(() => {
    const chart = chartInstanceRef.current;
    if (!chart) {
      return;
    }

    chart.setOption(option, { notMerge: true });
    chart.resize({
      animation: {
        duration: 220,
        easing: "cubicOut",
      },
    });
  }, [option]);

  return <div ref={chartContainerRef} className={className} />;
}

export function VisualizationChart({ spec, preview }: VisualizationChartProps) {
  const chartRef = useRef<echarts.ECharts | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const isClient = typeof window !== "undefined";

  const option = useMemo(() => buildChartOption(spec, preview), [preview, spec]);
  const fallbackReason = useMemo(
    () => getVisualizationFallbackReason(spec, preview),
    [preview, spec],
  );
  const debugWarnings = useMemo(
    () => collectVisualizationDebugWarnings(spec, preview),
    [preview, spec],
  );

  useEffect(() => {
    if (typeof document === "undefined" || !isExpanded) {
      return;
    }

    const previousOverflow = document.body.style.overflow;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsExpanded(false);
      }
    };

    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isExpanded]);

  useEffect(() => {
    if (!spec || debugWarnings.length === 0) {
      return;
    }
    console.warn("Visualization debug warnings", {
      kind: spec.kind,
      warnings: debugWarnings,
    });
  }, [debugWarnings, spec]);

  const handleChartReady = useCallback((chart: echarts.ECharts | null) => {
    chartRef.current = chart;
  }, []);

  const handleExport = () => {
    const chart = chartRef.current;
    if (!chart || !spec?.title) {
      return;
    }
    const url = chart.getDataURL({
      type: "png",
      pixelRatio: 2,
      backgroundColor: "#0c1220",
    });
    const link = document.createElement("a");
    link.href = url;
    link.download = `${spec.title.toLowerCase().replace(/\s+/g, "-")}.png`;
    link.click();
  };

  if (!spec || !preview) {
    return (
      <div className="flex min-h-[320px] flex-col items-center justify-center rounded-2xl border border-[#1f2a3f] bg-[#0b101c]/80 py-12 text-center">
        <BarChart3 className="mb-4 h-12 w-12 text-[#7484a3]" />
        <p className="text-sm text-[#a9b8d4]">Chart is not available for this answer</p>
      </div>
    );
  }

  if (spec.kind === "table" || option === null) {
    const fallbackMessage =
      fallbackReason !== null
        ? humanizeWarning(fallbackReason)
        : "Planner kept the table view for this result";
    return (
      <div className="flex min-h-[320px] flex-col items-center justify-center rounded-2xl border border-[#1f2a3f] bg-[#0b101c]/80 py-12 text-center">
        <BarChart3 className="mb-4 h-12 w-12 text-[#7484a3]" />
        <p className="text-sm text-[#a9b8d4]">
          {fallbackMessage}
        </p>
        <p className="mt-2 max-w-[42ch] text-xs text-[#7484a3]">{spec.rationale.why_kind}</p>
      </div>
    );
  }

  const chartCard = (
    <section
      role={isExpanded ? "dialog" : undefined}
      aria-modal={isExpanded || undefined}
      aria-label={isExpanded ? "Expanded chart view" : undefined}
      className={cn(
        "chart-zone",
        isExpanded &&
          "relative mx-auto w-full max-w-[min(1280px,calc(100vw-32px))] rounded-[28px] border-[#22314d] bg-[linear-gradient(180deg,rgba(10,15,27,0.98),rgba(7,11,19,0.99))] p-5 shadow-[0_28px_120px_rgba(0,0,0,0.65)] sm:max-w-[min(1280px,calc(100vw-56px))] sm:p-6"
      )}
    >
      <div className="chart-hero">
        <div>
          {spec.title ? <h3 className="chart-title">{spec.title}</h3> : null}
          {spec.summary ? <p className="chart-summary">{spec.summary}</p> : null}
          {spec.description ? <p className="chart-description">{spec.description}</p> : null}
        </div>

        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" className="gap-1.5" onClick={handleExport}>
            <Download className="h-4 w-4" />
            PNG
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="gap-1.5"
            onClick={() => setIsExpanded((value) => !value)}
          >
            {isExpanded ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
            {isExpanded ? "Compact" : "Expand"}
          </Button>
        </div>
      </div>

      <div className="chart-meta-strip">
        <Badge variant="primary">{goalLabel(spec.goal)}</Badge>
        <Badge variant="info">{describePlannerMode(spec)}</Badge>
        <Badge variant={spec.chart_safe_on_preview ? "success" : "warning"}>
          {spec.chart_safe_on_preview ? "Preview-safe" : "Preview-limited"}
        </Badge>
        <Badge variant="default">Confidence {Math.round(spec.confidence_score * 100)}%</Badge>
        <Badge variant="default">{summarizePreviewCoverage(spec)}</Badge>
      </div>

      <ChartCanvas
        className={isExpanded ? "h-[min(72vh,760px)] w-full" : "h-[400px] w-full"}
        onChartReady={handleChartReady}
        option={option}
      />

      <div className="chart-footer">
        <div className="chart-explain">
          <p className="chart-explain-label">Why this chart</p>
          <p className="chart-explain-text">{spec.rationale.why_kind}</p>
        </div>

        {debugWarnings.length > 0 ? (
          <ul className="chart-warning-list">
            {debugWarnings.map((warning) => (
              <li key={warning}>{humanizeWarning(warning)}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );

  return (
    <>
      {!isExpanded ? chartCard : null}
      {isExpanded && isClient
        ? createPortal(
            <div className="fixed inset-0 z-[180] flex items-center justify-center p-4 sm:p-7">
              <button
                type="button"
                aria-label="Close expanded chart"
                className="absolute inset-0 bg-[#02050c]/84 backdrop-blur-md"
                onClick={() => setIsExpanded(false)}
              />
              <div className="relative z-10 flex w-full items-center justify-center">{chartCard}</div>
            </div>,
            document.body
          )
        : null}
    </>
  );
}
