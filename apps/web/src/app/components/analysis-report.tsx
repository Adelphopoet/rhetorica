"use client";

import { lazy, Suspense, useState } from "react";
import { Badge, Button, cn, Skeleton } from "@rhetorica/ui";
import { AlertTriangle, ChevronDown, ChevronUp } from "lucide-react";

import type { AnalysisBlock, AnalysisDocument, AnalysisMetricGridBlock } from "@/features/analysis/types";
import type { QueryPreview, VisualizationSpec } from "@/features/charts";
import { MarkdownMessage } from "./markdown-message";

const LazyVisualizationChart = lazy(async () => ({
  default: (await import("@/features/charts")).VisualizationChart,
}));

const LazyResultTable = lazy(async () => ({
  default: (await import("./result-table")).ResultTable,
}));

type AnalysisReportMeta = {
  connectorLabel?: string | null;
  sourcesLabel?: string | null;
  rowCount?: number | null;
  durationLabel?: string | null;
  resultStatus?: string | null;
};

interface AnalysisReportProps {
  document: AnalysisDocument;
  fallbackPreview?: QueryPreview | null;
  fallbackVisualization?: VisualizationSpec | null;
  meta?: AnalysisReportMeta;
  variant?: "chat" | "surface";
}

function ReportSkeleton() {
  return (
    <div className="rounded-xl border border-border bg-surface/80 p-4">
      <Skeleton className="h-4 w-40" />
      <Skeleton className="mt-4 h-52 w-full" />
    </div>
  );
}

function normalizeSummaryText(value: string | null | undefined): string {
  return (value ?? "").replace(/\s+/g, " ").trim().toLowerCase();
}

export function isDuplicateExecutiveSummaryBlock(
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

function ReportMetricGrid({ block }: { block: AnalysisMetricGridBlock }) {
  return (
    <section className="rounded-xl border border-border bg-surface/[0.78] p-3.5">
      {block.title ? <h3 className="text-sm font-semibold text-primary">{block.title}</h3> : null}
      {block.description ? <p className="mt-1 text-xs leading-6 text-secondary">{block.description}</p> : null}
      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {block.items.map((item) => (
          <div key={`${item.label}-${item.value}`} className="min-w-0 rounded-lg border border-border-strong bg-surface/[0.86] px-3 py-2.5">
            <p className="truncate text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary">
              {item.label}
            </p>
            <p className="mt-1 break-words text-base font-semibold text-primary">{item.value}</p>
            {item.delta ? <p className="mt-1 text-xs text-accent">{item.delta}</p> : null}
            {item.context ? <p className="mt-1 text-xs leading-5 text-secondary">{item.context}</p> : null}
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
        "rounded-xl border p-3.5",
        tone === "critical"
          ? "border-danger-border bg-purple-subtle/[0.82]"
          : tone === "warning"
            ? "border-warning-border bg-warning-subtle/[0.82]"
            : "border-border bg-surface/[0.78]"
      )}
    >
      <h3 className="text-sm font-semibold text-primary">{title}</h3>
      <div className="mt-2 text-sm leading-7 text-primary">
        <MarkdownMessage text={body} />
      </div>
    </section>
  );
}

function normalizeNarrativeNote(value: string): string | null {
  const normalized = value.trim();
  if (!normalized || /^-{3,}$/.test(normalized)) {
    return null;
  }
  return normalized;
}

function ReportNarrativeNote({
  title,
  markdown,
}: {
  title?: string | null;
  markdown: string;
}) {
  const normalized = normalizeNarrativeNote(markdown);
  if (!normalized) {
    return null;
  }

  return (
    <section className="px-0.5 py-1 text-sm leading-7 text-primary">
      {title ? <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary">{title}</p> : null}
      <MarkdownMessage text={normalized} />
    </section>
  );
}

function isTechnicalWarningBlock(block: AnalysisBlock): boolean {
  return (
    block.kind === "caveat" &&
    block.title.trim().toLowerCase() === "warnings" &&
    /^[a-z0-9_;,\s-]+$/.test(block.body.trim())
  );
}

export function AnalysisReport({
  document,
  fallbackPreview,
  fallbackVisualization,
  meta,
  variant = "chat",
}: AnalysisReportProps) {
  const [expandedTableBlocks, setExpandedTableBlocks] = useState<Record<string, boolean>>({});
  const executiveSummary = document.executive_summary;
  const reportBlocks = document.blocks.filter(
    (block) =>
      !isDuplicateExecutiveSummaryBlock(block, executiveSummary) &&
      !isTechnicalWarningBlock(block)
  );
  const evidenceBadges = [
    meta?.connectorLabel,
    typeof meta?.rowCount === "number" ? `${meta.rowCount} строк` : null,
    meta?.resultStatus,
    meta?.durationLabel,
    meta?.sourcesLabel,
  ].filter((item): item is string => Boolean(item));

  function toggleTableBlock(blockKey: string) {
    setExpandedTableBlocks((current) => ({ ...current, [blockKey]: !current[blockKey] }));
  }

  function renderReportBlock(block: AnalysisBlock, index: number) {
    if (block.kind === "chart") {
      return (
        <Suspense key={`chart-${index}`} fallback={<ReportSkeleton />}>
          <LazyVisualizationChart spec={block.visualization} preview={block.preview ?? fallbackPreview} />
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
      return <ReportNarrativeNote key={`note-${index}`} title={block.title} markdown={block.markdown} />;
    }

    if (block.kind === "caveat") {
      return <ReportTextBlock key={`caveat-${index}`} title={block.title} body={block.body} tone="warning" />;
    }

    if (block.kind === "section_header") {
      return (
        <section key={`header-${index}`} className="border-t border-border-strong/70 pt-5 first:border-t-0 first:pt-1">
          <h3 className="break-words text-lg font-semibold tracking-normal text-primary">
            {block.title}
          </h3>
          {block.description ? <p className="mt-1.5 text-sm leading-6 text-secondary">{block.description}</p> : null}
        </section>
      );
    }

    if (block.kind === "divider") {
      return <div key={`divider-${index}`} className="h-px bg-hover" />;
    }

    if (block.kind === "table") {
      const blockKey = `table-${index}`;
      const visible = expandedTableBlocks[blockKey] ?? !block.preferred_hidden;
      const preview = block.preview ?? fallbackPreview;
      return (
        <section key={blockKey} className="rounded-xl border border-border bg-surface/[0.78] p-3.5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-primary">{block.title ?? "Строки детализации"}</h3>
              <p className="mt-1 text-xs leading-6 text-secondary">
                {block.description ?? "Исходные строки скрыты, пока не открыты"}
              </p>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-8 gap-1.5 rounded-full border border-border-strong bg-surface/85 px-3"
              onClick={() => toggleTableBlock(blockKey)}
            >
              {visible ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
              {visible ? "Скрыть строки" : block.action_label ?? "Показать строки"}
            </Button>
          </div>
          {visible ? (
            <div className="mt-3">
              <Suspense fallback={<ReportSkeleton />}>
                <LazyResultTable
                  columns={preview?.columns ?? block.columns ?? []}
                  rows={preview?.rows ?? []}
                  emptyMessage="Строки результата для этого ответа недоступны"
                />
              </Suspense>
            </div>
          ) : null}
        </section>
      );
    }

    return null;
  }

  return (
    <div
      aria-label="Отчёт об анализе"
      className={cn(
        "analysis-report mt-3 space-y-3",
        variant === "surface" ? "mt-0" : ""
      )}
    >
      {executiveSummary ? (
        <section className="rounded-xl border border-border-strong bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.92),rgb(var(--color-surface)/0.9))] p-3.5">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-accent">Краткое резюме</p>
          <div className="mt-2 text-sm leading-7 text-primary">
            <MarkdownMessage text={executiveSummary} />
          </div>
        </section>
      ) : null}

      {reportBlocks.length > 0 ? (
        reportBlocks.map((block, index) => renderReportBlock(block, index))
      ) : fallbackVisualization ? (
        <Suspense fallback={<ReportSkeleton />}>
          <LazyVisualizationChart spec={fallbackVisualization} preview={fallbackPreview} />
        </Suspense>
      ) : (
        <div className="flex items-center gap-2 rounded-xl border border-dashed border-border-strong bg-surface/70 px-3 py-3 text-sm text-secondary">
          <AlertTriangle className="h-4 w-4 text-warning" />
          Блоки отчёта для этого ответа недоступны.
        </div>
      )}

      {evidenceBadges.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {evidenceBadges.map((label) => (
            <Badge key={label} variant="default" className="px-2.5 py-1 text-[11px] text-secondary">
              {label}
            </Badge>
          ))}
        </div>
      ) : null}
    </div>
  );
}
