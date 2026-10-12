"use client";

import { useState } from "react";
import {
  FileCode2,
  Database,
  BookOpen,
  ExternalLink,
  Gitlab,
  AlertTriangle,
  CheckCircle,
  ChevronDown,
  ChevronUp,
  Copy,
  Sparkles,
} from "lucide-react";

import { Sheet, Badge, Button, ScrollArea, cn } from "@rhetorica/ui";
import type { AnswerClaim, AnswerEvidence } from "@/features/chat/streamingTypes";

interface EvidenceInspectorProps {
  /** Whether the inspector panel is open */
  open: boolean;
  /** Callback to close the inspector */
  onClose: () => void;
  /** The claim being inspected */
  claim: AnswerClaim | null;
  /** Evidence items linked to this claim */
  evidence: AnswerEvidence[];
}

/**
 * Map evidence types to icons and labels.
 */
const EVIDENCE_TYPE_CONFIG = {
  execution_result: {
    icon: FileCode2,
    label: "SQL",
    color: "text-accent",
  },
  metadata_entity: {
    icon: Database,
    label: "Модель метаданных",
    color: "text-success",
  },
  gitlab_file: {
    icon: Gitlab,
    label: "Файл GitLab",
    color: "text-warning",
  },
  airflow_runtime: {
    icon: ExternalLink,
    label: "Airflow",
    color: "text-accent",
  },
  rag_insight: {
    icon: BookOpen,
    label: "Фрагмент базы знаний",
    color: "text-warning",
  },
  internal_documentation: {
    icon: BookOpen,
    label: "Документация",
    color: "text-primary",
  },
} as const;

type EvidenceType = keyof typeof EVIDENCE_TYPE_CONFIG;

/**
 * Collapsible SQL preview for execution results.
 */
function ExecutionResultPreview({
  evidence,
}: {
  evidence: AnswerEvidence;
}) {
  const [isExpanded, setIsExpanded] = useState(false);

  // Extract SQL from the snippet or reference
  const hasSqlPreview =
    evidence.type === "execution_result" &&
    (evidence.snippet || evidence.reference);

  if (!hasSqlPreview) return null;

  const sqlContent = evidence.snippet || evidence.reference || "";

  // Try to extract row count from snippet if it looks like a query result
  const rowCountMatch = sqlContent.match(/(\d+)\s*row/i);
  const rowCount = rowCountMatch ? parseInt(rowCountMatch[1], 10) : null;

  return (
    <div className="mt-2 rounded-lg border border-border-strong bg-surface p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
<Badge variant="info" className="text-xs">
              <Sparkles className="mr-1 h-3 w-3" />
              Результат выполнения
            </Badge>
          {rowCount !== null && (
            <span className="text-xs text-muted">
              {rowCount} строк возвращено
            </span>
          )}
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setIsExpanded(!isExpanded)}
          className="h-7 px-2 text-xs"
        >
          {isExpanded ? (
            <>
              <ChevronUp className="mr-1 h-3 w-3" />
              Свернуть
            </>
          ) : (
            <>
              <ChevronDown className="mr-1 h-3 w-3" />
              Раскрыть
            </>
          )}
        </Button>
      </div>

      {isExpanded && (
        <div className="relative">
          <pre className="overflow-x-auto rounded bg-surface p-2 text-xs text-primary font-mono whitespace-pre-wrap">
            {sqlContent}
          </pre>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => navigator.clipboard.writeText(sqlContent)}
            className="absolute right-2 top-2 h-6 w-6 p-0"
          >
            <Copy className="h-3 w-3" />
          </Button>
        </div>
      )}
    </div>
  );
}

/**
 * Single evidence item with type icon, title, snippet, and reference.
 */
function EvidenceItem({
  evidence,
}: {
  evidence: AnswerEvidence;
}) {
  const config = EVIDENCE_TYPE_CONFIG[evidence.type as EvidenceType] ?? {
    icon: AlertTriangle,
    label: "Неизвестно",
    color: "text-danger",
  };

  const Icon = config.icon;
  const isVerified = evidence.status === "verified";
  const isUnverified = evidence.status === "unverified";

  // Check if reference is a valid URL
  const isUrl =
    evidence.reference?.startsWith("http://") ||
    evidence.reference?.startsWith("https://");

  // Check if evidence has minimal metadata
  const hasMetadata =
    (evidence.title && evidence.title.length > 0) ||
    (evidence.snippet && evidence.snippet.length > 0);

  return (
    <div
      className={cn(
        "rounded-xl border p-4",
        isUnverified
          ? "border-warning/30 bg-warning/5"
          : "border-border-strong bg-surface/50"
      )}
    >
      {/* Header */}
      <div className="mb-3 flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <div className={cn("rounded-lg bg-surface p-2", config.color)}>
            <Icon className="h-4 w-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-primary">
                {config.label}
              </span>
              <Badge
                variant={isVerified ? "success" : isUnverified ? "warning" : "default"}
                className="text-[10px] px-1.5 py-0"
              >
                {isVerified ? (
                  <>
                    <CheckCircle className="mr-0.5 h-2.5 w-2.5" />
                    Подтверждено
                  </>
                ) : isUnverified ? (
                  <>
                    <AlertTriangle className="mr-0.5 h-2.5 w-2.5" />
                    Не проверено
                  </>
                ) : (
                  evidence.status
                )}
              </Badge>
            </div>
            {evidence.title && (
              <p className="mt-0.5 text-xs text-secondary">{evidence.title}</p>
            )}
          </div>
        </div>
      </div>

      {/* Snippet */}
      {evidence.snippet ? (
        <blockquote className="mt-2 border-l-2 border-border-strong pl-3 text-sm text-primary italic">
          {evidence.snippet}
        </blockquote>
      ) : !hasMetadata ? (
        <div className="mt-2 flex items-center gap-2 text-sm text-warning">
          <AlertTriangle className="h-4 w-4" />
          <span>Метаданные недоступны</span>
        </div>
      ) : null}

      {/* Reference link */}
      {evidence.reference && (
        <div className="mt-3 flex items-center gap-1">
          {isUrl ? (
            <a
              href={evidence.reference}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-xs text-accent hover:underline"
            >
              <ExternalLink className="h-3 w-3" />
              {truncateReference(evidence.reference)}
            </a>
          ) : (
            <span className="text-xs text-muted">
              {truncateReference(evidence.reference)}
            </span>
          )}
        </div>
      )}

      {/* Execution result preview (collapsible SQL) */}
      {evidence.type === "execution_result" && (
        <ExecutionResultPreview evidence={evidence} />
      )}
    </div>
  );
}

/**
 * Truncate a reference string for display.
 */
function truncateReference(ref: string, maxLength = 60): string {
  if (ref.length <= maxLength) return ref;
  return ref.slice(0, maxLength).trimEnd() + "…";
}

/**
 * Evidence Inspector Sheet - right-side panel for inspecting claim evidence.
 */
export function EvidenceInspector({
  open,
  onClose,
  claim,
  evidence,
}: EvidenceInspectorProps) {
  const isVerified = claim?.status === "verified";
  const isUnverified = claim?.status === "unverified";

  return (
    <Sheet
      open={open}
      onOpenChange={(isOpen) => !isOpen && onClose()}
      title="Инспектор доказательств"
      description={
        claim
          ? isUnverified
            ? "У утверждения есть непроверенные доказательства"
            : "Проверьте доказательства, подтверждающие это утверждение"
          : "Выберите утверждение, чтобы проверить его доказательства"
      }
    >
      <ScrollArea className="h-[calc(100vh-8rem)] pr-4">
        {claim ? (
          <div className="space-y-4">
            {/* Claim header */}
            <div
              className={cn(
                "rounded-xl border p-4",
                isUnverified
                  ? "border-warning/30 bg-warning/5"
                  : "border-border-strong bg-surface/50"
              )}
            >
              <div className="mb-2 flex items-center gap-2">
                {isVerified ? (
                  <CheckCircle className="h-4 w-4 text-success" />
                ) : (
                  <AlertTriangle className="h-4 w-4 text-warning" />
                )}
                <Badge
                  variant={isVerified ? "success" : "warning"}
                  className="text-xs"
                >
                  {claim.status}
                </Badge>
              </div>
              <p className="text-sm leading-relaxed text-primary">
                {claim.text}
              </p>
            </div>

            {/* Evidence section */}
            <div>
              <h4 className="mb-3 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted">
                Доказательства
                {evidence.length > 0 && (
                  <Badge variant="default" className="text-[10px]">
                    {evidence.length}
                  </Badge>
                )}
              </h4>

              {evidence.length > 0 ? (
                <div className="space-y-3">
                  {evidence.map((item) => (
                    <EvidenceItem key={item.evidence_id} evidence={item} />
                  ))}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border-strong bg-surface/50 p-8 text-center">
                  <AlertTriangle className="mb-2 h-8 w-8 text-warning" />
                  <p className="text-sm font-medium text-warning">
                    Доказательства не привязаны
                  </p>
                  <p className="mt-1 text-xs text-muted">
                    У этого утверждения нет связанных доказательств.
                  </p>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <Sparkles className="mb-3 h-10 w-10 text-accent/50" />
            <p className="text-sm text-muted">
              Выберите утверждение, чтобы посмотреть его доказательства
            </p>
          </div>
        )}
      </ScrollArea>
    </Sheet>
  );
}
