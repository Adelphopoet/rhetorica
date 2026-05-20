"use client";

import { useState } from "react";
import {
  FileCode2,
  Database,
  BookOpen,
  ExternalLink,
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
    color: "text-[#5da8ff]",
  },
  metadata_entity: {
    icon: Database,
    label: "Metadata model",
    color: "text-[#38c67d]",
  },
  rag_insight: {
    icon: BookOpen,
    label: "Knowledge snippet",
    color: "text-[#f2b14b]",
  },
  confluence_page: {
    icon: ExternalLink,
    label: "Confluence",
    color: "text-[#d5e2fb]",
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
    <div className="mt-2 rounded-lg border border-[#22314d] bg-[#0c1525] p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Badge variant="info" className="text-xs">
            <Sparkles className="mr-1 h-3 w-3" />
            Execution Result
          </Badge>
          {rowCount !== null && (
            <span className="text-xs text-[#7484a3]">
              {rowCount} row{rowCount !== 1 ? "s" : ""} returned
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
              Collapse
            </>
          ) : (
            <>
              <ChevronDown className="mr-1 h-3 w-3" />
              Expand
            </>
          )}
        </Button>
      </div>

      {isExpanded && (
        <div className="relative">
          <pre className="overflow-x-auto rounded bg-[#09111e] p-2 text-xs text-[#d5e2fb] font-mono whitespace-pre-wrap">
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
  index,
}: {
  evidence: AnswerEvidence;
  index: number;
}) {
  const config = EVIDENCE_TYPE_CONFIG[evidence.type as EvidenceType] ?? {
    icon: AlertTriangle,
    label: "Unknown",
    color: "text-[#ff6d7c]",
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
          ? "border-amber-500/30 bg-amber-500/5"
          : "border-[#22314d] bg-[#0c1525]/50"
      )}
    >
      {/* Header */}
      <div className="mb-3 flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <div className={cn("rounded-lg bg-[#0c1525] p-2", config.color)}>
            <Icon className="h-4 w-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-[#eef4ff]">
                {config.label}
              </span>
              <Badge
                variant={isVerified ? "success" : isUnverified ? "warning" : "default"}
                className="text-[10px] px-1.5 py-0"
              >
                {isVerified ? (
                  <>
                    <CheckCircle className="mr-0.5 h-2.5 w-2.5" />
                    verified
                  </>
                ) : isUnverified ? (
                  <>
                    <AlertTriangle className="mr-0.5 h-2.5 w-2.5" />
                    unverified
                  </>
                ) : (
                  evidence.status
                )}
              </Badge>
            </div>
            {evidence.title && (
              <p className="mt-0.5 text-xs text-[#a9b8d4]">{evidence.title}</p>
            )}
          </div>
        </div>
      </div>

      {/* Snippet */}
      {evidence.snippet ? (
        <blockquote className="mt-2 border-l-2 border-[#2e3d59] pl-3 text-sm text-[#d5e2fb] italic">
          {evidence.snippet}
        </blockquote>
      ) : !hasMetadata ? (
        <div className="mt-2 flex items-center gap-2 text-sm text-[#f2b14b]">
          <AlertTriangle className="h-4 w-4" />
          <span>Metadata unavailable</span>
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
              className="flex items-center gap-1 text-xs text-[#5da8ff] hover:underline"
            >
              <ExternalLink className="h-3 w-3" />
              {truncateReference(evidence.reference)}
            </a>
          ) : (
            <span className="text-xs text-[#7484a3]">
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
      title="Evidence Inspector"
      description={
        claim
          ? isUnverified
            ? "Claim has unverified evidence"
            : "Review the evidence supporting this claim"
          : "Select a claim to inspect its evidence"
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
                  ? "border-amber-500/30 bg-amber-500/5"
                  : "border-[#22314d] bg-[#0c1525]/50"
              )}
            >
              <div className="mb-2 flex items-center gap-2">
                {isVerified ? (
                  <CheckCircle className="h-4 w-4 text-[#38c67d]" />
                ) : (
                  <AlertTriangle className="h-4 w-4 text-[#f2b14b]" />
                )}
                <Badge
                  variant={isVerified ? "success" : "warning"}
                  className="text-xs"
                >
                  {claim.status}
                </Badge>
              </div>
              <p className="text-sm leading-relaxed text-[#eef4ff]">
                {claim.text}
              </p>
            </div>

            {/* Evidence section */}
            <div>
              <h4 className="mb-3 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-[#7484a3]">
                Evidence
                {evidence.length > 0 && (
                  <Badge variant="default" className="text-[10px]">
                    {evidence.length}
                  </Badge>
                )}
              </h4>

              {evidence.length > 0 ? (
                <div className="space-y-3">
                  {evidence.map((item, index) => (
                    <EvidenceItem key={item.evidence_id} evidence={item} index={index} />
                  ))}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-[#22314d] bg-[#09111e]/50 p-8 text-center">
                  <AlertTriangle className="mb-2 h-8 w-8 text-[#f2b14b]" />
                  <p className="text-sm font-medium text-[#f2b14b]">
                    No evidence linked
                  </p>
                  <p className="mt-1 text-xs text-[#7484a3]">
                    This claim has no associated evidence items.
                  </p>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <Sparkles className="mb-3 h-10 w-10 text-[#5da8ff]/50" />
            <p className="text-sm text-[#7484a3]">
              Select a claim to view its evidence
            </p>
          </div>
        )}
      </ScrollArea>
    </Sheet>
  );
}
