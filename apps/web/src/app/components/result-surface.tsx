"use client";

import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Alert, Badge, Button, ScrollArea, Skeleton, Tabs, TabsContent, TabsList, TabsTrigger, useToast } from "@rhetorica/ui";
import {
  ChevronDown,
  ChevronUp,
  Database,
  Download,
  FileCode2,
  GripVertical,
  Lightbulb,
  PanelRightClose,
  Rows3,
  ShieldAlert,
} from "lucide-react";

import { cn } from "@rhetorica/ui";
import {
  buildResultSurfaceViewModel,
  formatExecutionTimestamp,
  shortenSql,
  type ChatAskResponse,
  type ExecutionHistoryItem,
} from "@/features/chat/chatViewModel";
import type {
  AnalysisBlock,
  AnalysisMetricGridBlock,
} from "@/features/analysis/types";
import { downloadAnalysisExport, type AnalysisExportFormat } from "@/features/chat/exportApi";
import { MarkdownMessage } from "./markdown-message";
import { StatusBadge } from "./status-badge";

const LazyVisualizationChart = lazy(async () => ({
  default: (await import("@/features/charts")).VisualizationChart,
}));

const LazyResultTable = lazy(async () => ({
  default: (await import("./result-table")).ResultTable,
}));

const LazySQLEditor = lazy(async () => ({
  default: (await import("./sql-editor")).SQLEditor,
}));

interface ResultSurfaceProps {
  payload?: ChatAskResponse;
  recentExecutions: ExecutionHistoryItem[];
  isOpen: boolean;
  onClose: () => void;
}

const DESKTOP_BREAKPOINT_PX = 1280;
const DEFAULT_PANEL_WIDTH_PX = 520;
const MIN_PANEL_WIDTH_PX = 380;
const MIN_MAIN_WORKSPACE_WIDTH_PX = 520;

function getMaxPanelWidth(layoutWidth: number): number {
  return Math.max(MIN_PANEL_WIDTH_PX, Math.floor(layoutWidth - MIN_MAIN_WORKSPACE_WIDTH_PX));
}

function clampPanelWidth(width: number, layoutWidth: number): number {
  const maxWidth = getMaxPanelWidth(layoutWidth);
  return Math.min(Math.max(Math.round(width), MIN_PANEL_WIDTH_PX), maxWidth);
}

export function calculateNextDesktopPanelWidth(clientX: number, layoutWidth: number, layoutRight: number): number {
  return clampPanelWidth(layoutRight - clientX, layoutWidth);
}

function resolveDesktopLayoutBounds(panel: HTMLElement | null): { width: number; right: number } | null {
  if (typeof window === "undefined") {
    return null;
  }

  const parentRect = panel?.parentElement?.getBoundingClientRect();
  if (parentRect && Number.isFinite(parentRect.width) && parentRect.width > 0) {
    return {
      width: parentRect.width,
      right: parentRect.right,
    };
  }

  return {
    width: window.innerWidth,
    right: window.innerWidth,
  };
}

function SurfaceSectionSkeleton({ className }: { className?: string }) {
  return (
    <div className={cn("rounded-2xl border border-[#1b2740] bg-[#09111e]/80 p-4", className)}>
      <Skeleton className="h-4 w-40" />
      <Skeleton className="mt-4 h-48 w-full" />
    </div>
  );
}

function ReportMetricGrid({ block }: { block: AnalysisMetricGridBlock }) {
  return (
    <section className="rounded-[24px] border border-[#1b2740] bg-[#09111e]/84 p-4">
      {block.title ? <h3 className="text-sm font-semibold text-[#edf3ff]">{block.title}</h3> : null}
      {block.description ? (
        <p className="mt-1 text-xs leading-6 text-[#7f93b4]">{block.description}</p>
      ) : null}
      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {block.items.map((item) => (
          <div
            key={`${item.label}-${item.value}`}
            className="rounded-2xl border border-[#21314d] bg-[#0b1423]/90 px-4 py-3"
          >
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#6f84a6]">
              {item.label}
            </p>
            <p className="mt-2 text-base font-semibold text-[#edf3ff]">{item.value}</p>
            {item.delta ? (
              <p className="mt-1 text-xs text-[#9ec6ff]">{item.delta}</p>
            ) : null}
            {item.context ? (
              <p className="mt-2 text-xs text-[#7f93b4]">{item.context}</p>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}

function ReportTextBlock({
  title,
  body,
  tone = "default",
}: {
  title: string;
  body: string;
  tone?: "default" | "warning" | "critical";
}) {
  return (
    <section
      className={cn(
        "rounded-[24px] border p-4",
        tone === "critical"
          ? "border-[#5b2630] bg-[#170b11]"
          : tone === "warning"
            ? "border-[#5c4a22] bg-[#17120b]"
            : "border-[#1b2740] bg-[#09111e]/84"
      )}
    >
      <h3 className="text-sm font-semibold text-[#edf3ff]">{title}</h3>
      <div className="mt-3 text-sm leading-7 text-[#d5e2fb]">
        <MarkdownMessage text={body} />
      </div>
    </section>
  );
}

function normalizeSummaryText(value: string | null | undefined): string {
  return (value ?? "").replace(/\s+/g, " ").trim().toLowerCase();
}

function isDuplicateExecutiveSummaryBlock(
  block: AnalysisBlock,
  executiveSummary: string | null | undefined
): boolean {
  if (block.kind !== "finding") {
    return false;
  }

  const normalizedSummary = normalizeSummaryText(executiveSummary);
  if (!normalizedSummary) {
    return false;
  }

  const normalizedTitle = normalizeSummaryText(block.title);
  const normalizedBody = normalizeSummaryText(block.body);
  return (
    normalizedBody === normalizedSummary &&
    (normalizedTitle === "key takeaway" || normalizedTitle === "executive summary")
  );
}

export function ResultSurface({
  payload,
  recentExecutions,
  isOpen,
  onClose,
}: ResultSurfaceProps) {
  const { addToast } = useToast();
  const shouldReduceMotion = useReducedMotion();
  const resultViewModel = useMemo(
    () => buildResultSurfaceViewModel(payload, recentExecutions),
    [payload, recentExecutions]
  );

  const [historyExpanded, setHistoryExpanded] = useState(false);
  const [desktopWidth, setDesktopWidth] = useState(DEFAULT_PANEL_WIDTH_PX);
  const [isDesktopViewport, setIsDesktopViewport] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [exportingFormat, setExportingFormat] = useState<AnalysisExportFormat | null>(null);
  const [isExportMenuOpen, setIsExportMenuOpen] = useState(false);
  const [expandedTableBlocks, setExpandedTableBlocks] = useState<Record<string, boolean>>({});
  const panelRef = useRef<HTMLElement | null>(null);
  const tabsResetKey = `${resultViewModel.defaultTab}:${resultViewModel.generatedSql ? "sql" : "no-sql"}:${
    resultViewModel.hasOptimization ? "optimizer" : "no-optimizer"
  }:${resultViewModel.hasEvidence ? "evidence" : "no-evidence"}`;

  const syncWidthToLayout = useCallback(() => {
    if (typeof window === "undefined") {
      return;
    }

    const nextIsDesktopViewport = window.innerWidth >= DESKTOP_BREAKPOINT_PX;
    setIsDesktopViewport(nextIsDesktopViewport);

    if (!nextIsDesktopViewport) {
      return;
    }

    const layoutBounds = resolveDesktopLayoutBounds(panelRef.current);
    if (!layoutBounds) {
      return;
    }

    setDesktopWidth((current) => clampPanelWidth(current, layoutBounds.width));
  }, []);

  useEffect(() => {
    const frameId = window.requestAnimationFrame(() => {
      syncWidthToLayout();
    });

    function handleResize() {
      syncWidthToLayout();
    }

    window.addEventListener("resize", handleResize);
    return () => {
      window.cancelAnimationFrame(frameId);
      window.removeEventListener("resize", handleResize);
    };
  }, [syncWidthToLayout]);

  const resizeFromPointer = useCallback((clientX: number) => {
    if (
      typeof window === "undefined" ||
      window.innerWidth < DESKTOP_BREAKPOINT_PX ||
      !isOpen ||
      !Number.isFinite(clientX)
    ) {
      return;
    }

    const layoutBounds = resolveDesktopLayoutBounds(panelRef.current);
    if (!layoutBounds) {
      return;
    }

    const nextWidth = calculateNextDesktopPanelWidth(clientX, layoutBounds.width, layoutBounds.right);
    setDesktopWidth((current) => (current === nextWidth ? current : nextWidth));
  }, [isOpen]);

  const stopResizing = useCallback(() => {
    setIsResizing(false);
  }, []);

  const beginResize = useCallback((clientX: number) => {
    if (typeof window === "undefined" || window.innerWidth < DESKTOP_BREAKPOINT_PX) {
      return;
    }

    setIsResizing(true);
    resizeFromPointer(clientX);
  }, [resizeFromPointer]);

  useEffect(() => {
    if (!isResizing) {
      return;
    }

    function handlePointerMove(event: PointerEvent) {
      resizeFromPointer(event.clientX);
    }

    function handlePointerUp() {
      stopResizing();
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);

    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };
  }, [isResizing, resizeFromPointer, stopResizing]);

  useEffect(() => {
    if (typeof document === "undefined" || !isResizing) {
      return;
    }

    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;

    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    return () => {
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
    };
  }, [isResizing]);

  const summaryCards = [
    { label: "Rows", value: String(resultViewModel.rowCount), tone: "text-[#edf3ff]" },
    { label: "Latency", value: resultViewModel.durationLabel, tone: "text-[#edf3ff]" },
    {
      label: "Connector",
      value: resultViewModel.connectorLabel,
      tone: "text-[#9ec6ff]",
    },
  ];
  const panelVisible = Boolean(payload) && isOpen;

  if (!payload) {
    return null;
  }

  const exportConversationId = payload?.conversation_id;
  const exportMessageId = payload?.assistant_message_id;
  const canExport = Boolean(
    (resultViewModel.analysisDocument || resultViewModel.visualization) &&
      exportConversationId &&
      exportMessageId
  );
  const executiveSummary = resultViewModel.analysisDocument?.executive_summary;
  const reportBlocks = (resultViewModel.analysisDocument?.blocks ?? []).filter(
    (block) => !isDuplicateExecutiveSummaryBlock(block, executiveSummary)
  );

  function toggleTableBlock(blockKey: string) {
    setExpandedTableBlocks((current) => ({
      ...current,
      [blockKey]: !current[blockKey],
    }));
  }

  function renderReportBlock(block: AnalysisBlock, index: number) {
    if (block.kind === "chart") {
      return (
        <Suspense key={`chart-${index}`} fallback={<SurfaceSectionSkeleton />}>
          <LazyVisualizationChart
            spec={block.visualization}
            preview={block.preview ?? resultViewModel.execution?.preview}
          />
        </Suspense>
      );
    }
    if (block.kind === "metric_grid") {
      return <ReportMetricGrid key={`metric-grid-${index}`} block={block} />;
    }
    if (block.kind === "finding") {
      return (
        <ReportTextBlock
          key={`finding-${index}`}
          title={block.title}
          body={block.body}
          tone={block.emphasis === "critical" ? "critical" : "default"}
        />
      );
    }
    if (block.kind === "narrative_note") {
      return (
        <ReportTextBlock
          key={`note-${index}`}
          title={block.title ?? "Analyst note"}
          body={block.markdown}
        />
      );
    }
    if (block.kind === "caveat") {
      return (
        <ReportTextBlock
          key={`caveat-${index}`}
          title={block.title}
          body={block.body}
          tone="warning"
        />
      );
    }
    if (block.kind === "section_header") {
      return (
        <section key={`header-${index}`} className="pt-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#7f93b4]">
            {block.title}
          </p>
          {block.description ? (
            <p className="mt-1 text-sm leading-6 text-[#a8bbd8]">{block.description}</p>
          ) : null}
        </section>
      );
    }
    if (block.kind === "divider") {
      return <div key={`divider-${index}`} className="h-px bg-[#1b2740]" />;
    }
    if (block.kind === "table") {
      const blockKey = `table-${index}`;
      const visible =
        expandedTableBlocks[blockKey] ?? !block.preferred_hidden;
      return (
        <section
          key={blockKey}
          className="rounded-[24px] border border-[#1b2740] bg-[#09111e]/84 p-4"
        >
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-[#edf3ff]">
                {block.title ?? "Drilldown rows"}
              </h3>
              <p className="mt-1 text-xs leading-6 text-[#7f93b4]">
                {block.description ?? "Raw rows stay secondary until you explicitly ask for them."}
              </p>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="rounded-full border border-[#23324e] bg-[#0d1729]/85"
              onClick={() => toggleTableBlock(blockKey)}
            >
              {visible ? "Hide rows" : block.action_label ?? "Show rows"}
            </Button>
          </div>
          {visible ? (
            <div className="mt-4">
              <Suspense fallback={<SurfaceSectionSkeleton className="min-h-[260px]" />}>
                <LazyResultTable
                  columns={block.preview?.columns ?? block.columns ?? resultViewModel.execution?.preview.columns ?? []}
                  rows={block.preview?.rows ?? resultViewModel.execution?.preview.rows ?? []}
                  emptyMessage="Execution results will appear here once a guarded query finishes."
                />
              </Suspense>
            </div>
          ) : null}
        </section>
      );
    }
    return null;
  }

  async function handleExport(format: AnalysisExportFormat) {
    if (!exportConversationId || !exportMessageId) {
      addToast({
        type: "error",
        title: "Export unavailable",
        description: "This answer does not have a persisted snapshot yet.",
      });
      return;
    }

    setExportingFormat(format);
    setIsExportMenuOpen(false);
    try {
      await downloadAnalysisExport({
        conversationId: exportConversationId,
        messageId: exportMessageId,
        format,
      });
      addToast({
        type: "success",
        title: "Export ready",
        description: `${format.toUpperCase()} snapshot downloaded.`,
      });
    } catch (error) {
      addToast({
        type: "error",
        title: "Export failed",
        description: error instanceof Error ? error.message : "Unknown export failure.",
      });
    } finally {
      setExportingFormat(null);
    }
  }

  const panelWidthStyle = {
    "--result-surface-width": `${desktopWidth}px`,
  } as CSSProperties;

  return (
    <>
      <button
        type="button"
        aria-label="Close result panel"
        onClick={onClose}
        className={cn(
          "fixed inset-0 z-30 bg-[#02050c]/72 backdrop-blur-sm transition xl:hidden",
          panelVisible ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"
        )}
      />

      <motion.aside
        ref={panelRef}
        initial={false}
        animate={
          shouldReduceMotion
            ? { opacity: panelVisible ? 1 : 0 }
            : isDesktopViewport
              ? { x: 0, opacity: 1 }
              : { x: panelVisible ? 0 : 32, opacity: panelVisible ? 1 : 0 }
        }
        transition={{ duration: 0.22, ease: "easeOut" }}
        style={panelWidthStyle}
        data-panel-state={panelVisible ? "open" : "closed"}
        data-panel-width={desktopWidth}
        className={cn(
          "fixed inset-y-3 right-3 z-40 flex w-full max-w-[min(100vw-24px,560px)] flex-col rounded-[28px] border border-[#172235] bg-[linear-gradient(180deg,rgba(7,11,19,0.98),rgba(8,13,23,0.98))] shadow-[-24px_0_80px_rgba(0,0,0,0.45)] duration-200 ease-out xl:relative xl:inset-auto xl:right-auto xl:z-0 xl:h-full xl:max-w-none xl:shrink-0 xl:overflow-hidden xl:rounded-none xl:rounded-r-[32px] xl:border-y-0 xl:border-r-0 xl:shadow-none",
          isResizing ? "transition-none" : "transition-[width,opacity,transform,border-color]",
          panelVisible
            ? "translate-x-0 xl:w-[var(--result-surface-width)] xl:min-w-[var(--result-surface-width)]"
            : "pointer-events-none translate-x-[calc(100%+20px)] xl:w-0 xl:min-w-0 xl:translate-x-0 xl:border-l-0"
        )}
      >
        <button
          type="button"
          aria-label="Resize result surface"
          role="separator"
          aria-orientation="vertical"
          onPointerDown={(event) => {
            event.preventDefault();
            event.currentTarget.setPointerCapture?.(event.pointerId);
            beginResize(event.clientX);
          }}
          className={cn(
            "absolute left-1 top-0 z-20 hidden h-full w-6 cursor-col-resize items-center justify-start border-0 bg-transparent pl-1 text-[#5d7497] transition hover:text-[#9ec6ff] xl:flex",
            (!panelVisible || !payload) && "xl:hidden"
          )}
        >
          <span className="flex h-16 w-2 items-center justify-center rounded-full border border-[#22314d] bg-[#09111e]/94 shadow-[0_0_0_1px_rgba(8,13,23,0.9)]">
            <GripVertical className="h-4 w-4" />
          </span>
        </button>

        <div
          className={cn(
            "flex h-full flex-col transition-[opacity,transform] duration-180 ease-out",
            panelVisible
              ? "opacity-100 translate-x-0 delay-75"
              : "translate-x-3 opacity-0 delay-0"
          )}
        >
          <div className="flex items-center justify-between gap-4 border-b border-[#16233a] px-4 py-3 sm:px-5 xl:px-6">
            <h2 className="text-base font-semibold tracking-[-0.03em] text-[#eef4ff]">
              Analyst inspection rail
            </h2>

            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={onClose}
              className="gap-2"
            >
              <PanelRightClose className="h-4 w-4" />
              <span className="hidden xl:inline">Hide</span>
            </Button>
          </div>

          <ScrollArea className="flex-1">
            <div className="space-y-5 px-4 py-4 sm:px-5 xl:px-6">
            <section className="rounded-[24px] border border-[#1b2740] bg-[radial-gradient(circle_at_top_right,rgba(93,168,255,0.14),transparent_26%),linear-gradient(180deg,rgba(9,14,24,0.96),rgba(10,16,28,0.98))] p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="primary" className="gap-1.5 px-3 py-1">
                  <Database className="h-3.5 w-3.5" />
                  {resultViewModel.connectorLabel}
                </Badge>
                <StatusBadge status={resultViewModel.resultStatus} />
                <Badge variant="info" className="px-3 py-1">
                  Sources: {resultViewModel.sourcesLabel}
                </Badge>
              </div>

              <div className="mt-4 grid gap-3 sm:grid-cols-3">
                {summaryCards.map((card) => (
                  <div
                    key={card.label}
                    className="rounded-2xl border border-[#21314d] bg-[#0b1423]/90 px-4 py-3"
                  >
                    <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#6f84a6]">
                      {card.label}
                    </p>
                    <p className={cn("mt-2 text-base font-semibold", card.tone)}>{card.value}</p>
                  </div>
                ))}
              </div>

              {resultViewModel.warnings.length ? (
                <Alert
                  variant="warning"
                  title="Execution warnings"
                  description={
                    <ul className="space-y-1">
                      {resultViewModel.warnings.map((warning, index) => (
                        <li key={`${warning}-${index}`}>{warning}</li>
                      ))}
                    </ul>
                  }
                  className="mt-4"
                />
              ) : null}

              {resultViewModel.execution?.error_message ? (
                <Alert
                  variant="danger"
                  title="Execution error"
                  description={resultViewModel.execution.error_message}
                  className="mt-4"
                />
              ) : null}

              {canExport ? (
                <div className="relative mt-4 inline-flex">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="gap-2 rounded-full border border-[#23324e] bg-[#0d1729]/85"
                    onClick={() => {
                      setIsExportMenuOpen((current) => !current);
                    }}
                    aria-expanded={isExportMenuOpen}
                    aria-haspopup="menu"
                    disabled={exportingFormat !== null}
                  >
                    <Download className="h-4 w-4" />
                    {exportingFormat ? `Exporting ${exportingFormat.toUpperCase()}...` : "Export"}
                    <ChevronDown className="h-4 w-4" />
                  </Button>
                  {isExportMenuOpen ? (
                    <div
                      role="menu"
                      aria-label="Export report"
                      className="absolute left-0 top-full z-20 mt-2 min-w-36 overflow-hidden rounded-2xl border border-[#23324e] bg-[#0b1423] p-1 shadow-2xl shadow-black/35"
                    >
                      {(["html", "pdf", "ipynb"] as AnalysisExportFormat[]).map((format) => (
                        <button
                          key={format}
                          type="button"
                          role="menuitem"
                          className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-xs font-semibold uppercase text-[#d5e2fb] transition hover:bg-[#162238] hover:text-[#edf3ff]"
                          onClick={() => {
                            void handleExport(format);
                          }}
                        >
                          <Download className="h-4 w-4" />
                          {format.toUpperCase()}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </section>

            <section className="rounded-[24px] border border-[#1b2740] bg-[#09111e]/84 p-4">
              <Tabs key={tabsResetKey} defaultValue={resultViewModel.defaultTab}>
                <TabsList className="w-full justify-start gap-2 overflow-auto border-0 bg-transparent p-0">
                  <TabsTrigger
                    value="result"
                    className="rounded-full border border-[#23324e] bg-[#0d1729]/85 px-3 py-2 data-[state=active]:border-[#5da8ff] data-[state=active]:bg-[#5da8ff] data-[state=active]:text-white"
                  >
                    <Rows3 className="mr-1.5 h-4 w-4" />
                    Result
                  </TabsTrigger>
                  <TabsTrigger
                    value="sql"
                    disabled={!resultViewModel.generatedSql}
                    className="rounded-full border border-[#23324e] bg-[#0d1729]/85 px-3 py-2 data-[state=active]:border-[#5da8ff] data-[state=active]:bg-[#5da8ff] data-[state=active]:text-white"
                  >
                    <FileCode2 className="mr-1.5 h-4 w-4" />
                    SQL
                  </TabsTrigger>
                  <TabsTrigger
                    value="evidence"
                    disabled={!resultViewModel.hasEvidence}
                    className="rounded-full border border-[#23324e] bg-[#0d1729]/85 px-3 py-2 data-[state=active]:border-[#5da8ff] data-[state=active]:bg-[#5da8ff] data-[state=active]:text-white"
                  >
                    <ShieldAlert className="mr-1.5 h-4 w-4" />
                    Evidence
                  </TabsTrigger>
                  <TabsTrigger
                    value="optimizer"
                    disabled={!resultViewModel.hasOptimization}
                    className="rounded-full border border-[#23324e] bg-[#0d1729]/85 px-3 py-2 data-[state=active]:border-[#5da8ff] data-[state=active]:bg-[#5da8ff] data-[state=active]:text-white"
                  >
                    <Lightbulb className="mr-1.5 h-4 w-4" />
                    Optimizer
                  </TabsTrigger>
                </TabsList>

                <TabsContent value="result" className="mt-4 space-y-4">
                  {executiveSummary ? (
                    <section className="rounded-[24px] border border-[#1b2740] bg-[radial-gradient(circle_at_top_right,rgba(93,168,255,0.14),transparent_26%),linear-gradient(180deg,rgba(9,14,24,0.96),rgba(10,16,28,0.98))] p-4">
                      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#89b7ff]">
                        Executive summary
                      </p>
                      <div className="mt-3 text-sm leading-7 text-[#e6efff]">
                        <MarkdownMessage text={executiveSummary} />
                      </div>
                    </section>
                  ) : null}

                  {resultViewModel.hasAnalysisDocument ? (
                    reportBlocks.map((block, index) => renderReportBlock(block, index))
                  ) : resultViewModel.hasVisualization ? (
                    <>
                      <Suspense fallback={<SurfaceSectionSkeleton />}>
                        <LazyVisualizationChart
                          spec={resultViewModel.visualization}
                          preview={resultViewModel.execution?.preview}
                        />
                      </Suspense>
                      <section className="rounded-[24px] border border-[#1b2740] bg-[#09111e]/84 p-4">
                        <div className="flex items-center justify-between gap-3">
                          <div>
                            <p className="text-sm font-semibold text-[#edf3ff]">Drilldown rows</p>
                            <p className="mt-1 text-xs text-[#7f93b4]">
                              Table stays collapsed until you ask for row-level detail.
                            </p>
                          </div>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="rounded-full border border-[#23324e] bg-[#0d1729]/85"
                            onClick={() => toggleTableBlock("legacy-table")}
                          >
                            {expandedTableBlocks["legacy-table"] ? "Hide rows" : "Show rows"}
                          </Button>
                        </div>
                        {expandedTableBlocks["legacy-table"] ? (
                          <div className="mt-4">
                            <Suspense fallback={<SurfaceSectionSkeleton className="min-h-[260px]" />}>
                              <LazyResultTable
                                columns={resultViewModel.execution?.preview.columns ?? []}
                                rows={resultViewModel.execution?.preview.rows ?? []}
                                emptyMessage="Execution results will appear here once a guarded query finishes."
                              />
                            </Suspense>
                          </div>
                        ) : null}
                      </section>
                    </>
                  ) : (
                    <Suspense fallback={<SurfaceSectionSkeleton className="min-h-[260px]" />}>
                      <LazyResultTable
                        columns={resultViewModel.execution?.preview.columns ?? []}
                        rows={resultViewModel.execution?.preview.rows ?? []}
                        emptyMessage="Execution results will appear here once a guarded query finishes."
                      />
                    </Suspense>
                  )}
                </TabsContent>

                <TabsContent value="sql" className="mt-4">
                  <div className="space-y-4">
                    {resultViewModel.generatedSql ? (
                      <div className="rounded-2xl border border-[#22314d] bg-[#0c1525]/88 p-4">
                        <div className="mb-3 flex items-center justify-between gap-3">
                          <p className="text-sm font-semibold text-[#edf3ff]">Original SQL draft</p>
                          <Badge variant="info">LLM output</Badge>
                        </div>
                        <Suspense fallback={<SurfaceSectionSkeleton className="min-h-[240px]" />}>
                          <LazySQLEditor
                            sql={resultViewModel.generatedSql}
                            engine={resultViewModel.execution?.engine ?? "SQL"}
                          />
                        </Suspense>
                      </div>
                    ) : (
                      <div className="rounded-2xl border border-dashed border-[#26334d] bg-[#0c1525]/80 px-4 py-8 text-sm text-[#7f93b4]">
                        SQL has not been generated for this answer yet.
                      </div>
                    )}

                    {resultViewModel.executedSql &&
                    resultViewModel.executedSql !== resultViewModel.generatedSql ? (
                      <div className="rounded-2xl border border-[#1f3552] bg-[#09111e] p-4">
                        <div className="mb-3 flex items-center justify-between gap-3">
                          <p className="text-sm font-semibold text-[#edf3ff]">Executed SQL</p>
                          <Badge variant="success">Optimizer-applied</Badge>
                        </div>
                        <Suspense fallback={<SurfaceSectionSkeleton className="min-h-[240px]" />}>
                          <LazySQLEditor
                            sql={resultViewModel.executedSql}
                            engine={resultViewModel.execution?.engine ?? "SQL"}
                          />
                        </Suspense>
                      </div>
                    ) : null}
                  </div>
                </TabsContent>

                <TabsContent value="optimizer" className="mt-4">
                  {resultViewModel.optimizationTrace?.optimizer_used ? (
                    <div className="space-y-4">
                      <div className="rounded-2xl border border-[#22314d] bg-[#0c1525]/88 p-4">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant={resultViewModel.optimizationTrace.applied ? "success" : "warning"}>
                            {resultViewModel.optimizationTrace.applied ? "Applied" : "Advisory"}
                          </Badge>
                          <Badge variant="info">
                            Risk: {resultViewModel.optimizationTrace.risk_level ?? "unknown"}
                          </Badge>
                          <Badge variant="default">
                            Provider: {resultViewModel.optimizationTrace.optimizer_provider ?? "n/a"}
                          </Badge>
                          {typeof resultViewModel.optimizationTrace.confidence === "number" ? (
                            <Badge variant="default">
                              Confidence: {Math.round(resultViewModel.optimizationTrace.confidence * 100)}%
                            </Badge>
                          ) : null}
                        </div>

                        {resultViewModel.optimizationTrace.warnings.length ? (
                          <Alert
                            variant="warning"
                            title="Optimizer warnings"
                            description={resultViewModel.optimizationTrace.warnings.join(", ")}
                            className="mt-4"
                          />
                        ) : null}

                        <div className="mt-4 grid gap-3 sm:grid-cols-2">
                          <div className="rounded-2xl border border-[#1c2941] bg-[#09111e] p-4">
                            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#7f93b4]">
                              Original SQL
                            </p>
                            <p className="mt-2 whitespace-pre-wrap break-all font-mono text-xs leading-6 text-[#dbe8ff]">
                              {resultViewModel.optimizationTrace.input_sql ?? resultViewModel.generatedSql ?? "n/a"}
                            </p>
                          </div>
                          <div className="rounded-2xl border border-[#1c2941] bg-[#09111e] p-4">
                            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#7f93b4]">
                              Executed SQL
                            </p>
                            <p className="mt-2 whitespace-pre-wrap break-all font-mono text-xs leading-6 text-[#dbe8ff]">
                              {resultViewModel.executedSql ?? resultViewModel.optimizationTrace.optimized_sql_draft ?? "n/a"}
                            </p>
                          </div>
                        </div>
                      </div>

                      {resultViewModel.optimizationTrace.findings.length ? (
                        <div className="space-y-3">
                          {resultViewModel.optimizationTrace.findings.map((finding) => (
                            <div
                              key={finding.rule_id}
                              className="rounded-2xl border border-[#22314d] bg-[#0c1525]/88 p-4"
                            >
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge variant={finding.severity === "warning" ? "warning" : "info"}>
                                  {finding.rule_id}
                                </Badge>
                                <p className="text-sm font-semibold text-[#edf3ff]">{finding.title}</p>
                              </div>
                              <p className="mt-2 text-sm leading-6 text-[#a8bbd8]">{finding.summary}</p>
                              {finding.applies_to.length ? (
                                <p className="mt-2 text-xs text-[#7f93b4]">
                                  Applies to: {finding.applies_to.join(", ")}
                                </p>
                              ) : null}
                            </div>
                          ))}
                        </div>
                      ) : null}

                      {resultViewModel.optimizationTrace.recommended_actions.length ? (
                        <div className="space-y-3">
                          {resultViewModel.optimizationTrace.recommended_actions.map((action) => (
                            <div
                              key={action.action_id}
                              className="rounded-2xl border border-[#22314d] bg-[#0c1525]/88 p-4"
                            >
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge variant={action.applied ? "success" : "info"}>
                                  {action.applied ? "Applied" : "Suggested"}
                                </Badge>
                                <p className="text-sm font-semibold text-[#edf3ff]">{action.title}</p>
                              </div>
                              <p className="mt-2 text-sm leading-6 text-[#a8bbd8]">{action.summary}</p>
                            </div>
                          ))}
                        </div>
                      ) : null}

                      <div className="grid gap-3 lg:grid-cols-2">
                        <div className="rounded-2xl border border-[#22314d] bg-[#0c1525]/88 p-4">
                          <p className="text-sm font-semibold text-[#edf3ff]">Evidence used</p>
                          <p className="mt-2 text-xs leading-6 text-[#a8bbd8]">
                            {resultViewModel.optimizationTrace.evidence_used.join(", ") || "n/a"}
                          </p>
                        </div>
                        <div className="rounded-2xl border border-[#22314d] bg-[#0c1525]/88 p-4">
                          <p className="text-sm font-semibold text-[#edf3ff]">Citations</p>
                          <div className="mt-2 space-y-2 text-xs leading-6 text-[#a8bbd8]">
                            {resultViewModel.optimizationTrace.citations.length ? (
                              resultViewModel.optimizationTrace.citations.map((citation, index) => (
                                <div key={`${citation.reference}-${index}`}>
                                  <span className="font-semibold text-[#dbe8ff]">{citation.label}</span>
                                  {" · "}
                                  <span>{citation.reference}</span>
                                </div>
                              ))
                            ) : (
                              <span>n/a</span>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="rounded-2xl border border-dashed border-[#26334d] bg-[#0c1525]/80 px-4 py-8 text-sm text-[#7f93b4]">
                      Optimizer trace appears only for supported pre-execution advisory runs.
                    </div>
                  )}
                </TabsContent>

                <TabsContent value="evidence" className="mt-4">
                  {resultViewModel.hasEvidence ? (
                    <div className="space-y-3">
                      {resultViewModel.claims.map((claim, claimIndex) => {
                        const link = resultViewModel.claimEvidenceLinks.find(
                          (item) => item.claim_id === claim.claim_id
                        );
                        const linkedEvidence = (link?.evidence_ids ?? [])
                          .map((id: string) =>
                            resultViewModel.evidence.find((item) => item.evidence_id === id)
                          )
                          .filter(
                            (
                              item
                            ): item is NonNullable<typeof item> => item != null
                          );

                        return (
                          <div
                            key={`${claim.claim_id}:${claimIndex}`}
                            className="rounded-2xl border border-[#22314d] bg-[#0c1525]/88 p-4"
                          >
                            <div className="flex flex-wrap items-center gap-2">
                              <Badge variant={claim.status === "verified" ? "success" : "warning"}>
                                {claim.status}
                              </Badge>
                              <p className="text-sm font-medium leading-6 text-[#eaf2ff]">
                                {claim.text}
                              </p>
                            </div>

                            {linkedEvidence.length ? (
                              <div className="mt-3 grid gap-2">
                                {linkedEvidence.map((item) => (
                                  <div
                                    key={item.evidence_id}
                                    className="rounded-xl border border-[#1c2941] bg-[#09111e] px-3 py-2 text-xs leading-6 text-[#97accf]"
                                  >
                                    <span className="font-semibold text-[#d8e7ff]">
                                      {item.type}
                                    </span>{" "}
                                    {item.reference}
                                    {item.title ? ` • ${item.title}` : ""}
                                  </div>
                                ))}
                              </div>
                            ) : null}
                          </div>
                        );
                      })}

                      {resultViewModel.uncertaintyFlags.length ? (
                        <Alert
                          variant="warning"
                          title="Uncertainty flags"
                          description={resultViewModel.uncertaintyFlags.join(", ")}
                        />
                      ) : null}
                    </div>
                  ) : (
                    <div className="rounded-2xl border border-dashed border-[#26334d] bg-[#0c1525]/80 px-4 py-8 text-sm text-[#7f93b4]">
                      Evidence cards appear when the answer includes claims and linked sources.
                    </div>
                  )}
                </TabsContent>
              </Tabs>
            </section>

            <section className="rounded-[24px] border border-[#1b2740] bg-[#09111e]/84 p-4">
              <button
                type="button"
                onClick={() => setHistoryExpanded((value) => !value)}
                className="flex w-full items-center justify-between gap-3"
              >
                <div>
                  <p className="text-sm font-semibold text-[#edf3ff]">Recent executions</p>
                  <p className="mt-1 text-xs text-[#7f93b4]">
                    Keep operational history nearby, not in the way.
                  </p>
                </div>
                <Badge variant="default" className="gap-2">
                  {recentExecutions.length}
                  {historyExpanded ? (
                    <ChevronUp className="h-3.5 w-3.5" />
                  ) : (
                    <ChevronDown className="h-3.5 w-3.5" />
                  )}
                </Badge>
              </button>

              {historyExpanded ? (
                <div className="mt-4 space-y-3">
                  {recentExecutions.length ? (
                    recentExecutions.map((item) => (
                      <div
                        key={item.execution_id}
                        className="rounded-2xl border border-[#22314d] bg-[#0c1525]/88 px-4 py-3"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <StatusBadge status={item.status} />
                          <span className="text-xs text-[#7f93b4]">
                            {formatExecutionTimestamp(item.updated_at)} UTC
                          </span>
                        </div>
                        <p className="mt-3 text-sm leading-6 text-[#e2ecff]">
                          {shortenSql(item.submitted_sql)}
                        </p>
                        <div className="mt-2 flex flex-wrap gap-3 text-xs text-[#7f93b4]">
                          <span>ID {item.execution_id.slice(0, 8)}</span>
                          <span>Rows {item.row_count ?? "-"}</span>
                          <span>Time {item.duration_ms ? `${item.duration_ms} ms` : "-"}</span>
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="rounded-2xl border border-dashed border-[#26334d] bg-[#0c1525]/80 px-4 py-6 text-sm text-[#7f93b4]">
                      No executions yet. Ask a question and the guarded execution history will land here.
                    </p>
                  )}
                </div>
              ) : null}
            </section>
            </div>
          </ScrollArea>
        </div>
      </motion.aside>
    </>
  );
}
