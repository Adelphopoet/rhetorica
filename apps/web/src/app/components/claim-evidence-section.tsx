"use client";

import { useState, useMemo } from "react";
import { Eye, CheckCircle, AlertTriangle } from "lucide-react";

import { Badge, cn } from "@rhetorica/ui";
import {
  AnswerClaim,
  AnswerEvidence,
  ClaimEvidenceLink,
} from "@/features/chat/streamingTypes";
import { EvidenceInspector } from "./evidence-inspector";

interface ClaimEvidenceSectionProps {
  claims: AnswerClaim[];
  evidence: AnswerEvidence[];
  claimEvidenceLinks: ClaimEvidenceLink[];
}

/**
 * Compact badge showing evidence count for a claim.
 */
function EvidenceCountBadge({
  count,
  typeLabels,
}: {
  count: number;
  typeLabels: string[];
}) {
  const label = typeLabels.length > 0 ? typeLabels.join(", ") : "ссылок";
  return (
    <Badge variant="default" className="text-xs">
      {count} {label}
    </Badge>
  );
}

/**
 * Compact claim item with status and evidence count.
 */
function ClaimBadgeItem({
  claim,
  evidenceCount,
  evidenceTypeSummary,
  isSelected,
  onClick,
}: {
  claim: AnswerClaim;
  evidenceCount: number;
  evidenceTypeSummary: string[];
  isSelected: boolean;
  onClick: () => void;
}) {
  const isVerified = claim.status === "verified";
  const isUnverified = claim.status === "unverified";

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex flex-col gap-1.5 rounded-xl border p-3 text-left transition-all duration-200",
        "hover:border-border-strong hover:bg-surface/50",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
        isSelected && "border-accent/60 bg-surface/70",
        isUnverified
          ? "border-warning/30 bg-warning/5"
          : "border-border-strong bg-surface/60"
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          {isVerified ? (
            <CheckCircle className="h-3.5 w-3.5 text-success" />
          ) : (
            <AlertTriangle className="h-3.5 w-3.5 text-warning" />
          )}
          <Badge
            variant={isVerified ? "success" : "warning"}
            className="text-[10px] px-1.5 py-0"
          >
            {claim.status}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <EvidenceCountBadge count={evidenceCount} typeLabels={evidenceTypeSummary} />
          <Eye className="h-4 w-4 text-muted hover:text-accent transition-colors" />
        </div>
      </div>

      <p
        className={cn(
          "text-sm leading-snug",
          isUnverified ? "text-warning/90" : "text-primary"
        )}
      >
        {truncateText(claim.text, 150)}
      </p>
    </button>
  );
}

/**
 * Truncate text with ellipsis.
 */
function truncateText(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength).trimEnd() + "…";
}

/**
 * Get evidence type summary labels for display.
 */
function getEvidenceTypeSummary(
  evidence: AnswerEvidence[]
): { count: number; labels: string[] } {
  const typeCounts: Record<string, number> = {};
  for (const item of evidence) {
    typeCounts[item.type] = (typeCounts[item.type] ?? 0) + 1;
  }

  const labels: string[] = [];
  if (typeCounts.execution_result) {
    labels.push(`${typeCounts.execution_result} SQL`);
  }
  if (typeCounts.metadata_entity) {
    labels.push(`${typeCounts.metadata_entity} моделей`);
  }
  if (typeCounts.gitlab_file) {
    labels.push(`${typeCounts.gitlab_file} файлов GitLab`);
  }
  if (typeCounts.airflow_runtime) {
    labels.push(`${typeCounts.airflow_runtime} Airflow`);
  }
  if (typeCounts.rag_insight) {
    labels.push(`${typeCounts.rag_insight} фрагментов`);
  }
  if (typeCounts.internal_documentation) {
    labels.push(`${typeCounts.internal_documentation} документов`);
  }

  return { count: evidence.length, labels };
}

/**
 * Claims card section - renders a list of claims with status badges
 * and opens an evidence inspector when a claim is clicked.
 */
export function ClaimEvidenceSection({
  claims,
  evidence,
  claimEvidenceLinks,
}: ClaimEvidenceSectionProps) {
  const [selectedClaimIndex, setSelectedClaimIndex] = useState<number | null>(
    null
  );

  // Build a map of claim_id -> linked evidence
  const claimEvidenceMap = useMemo(() => {
    const map = new Map<string, AnswerEvidence[]>();
    for (const link of claimEvidenceLinks) {
      const linkedEvidence = (link.evidence_ids ?? [])
        .map((evidenceId) => evidence.find((e) => e.evidence_id === evidenceId))
        .filter((e): e is AnswerEvidence => e != null);
      map.set(link.claim_id, linkedEvidence);
    }
    return map;
  }, [claimEvidenceLinks, evidence]);

  // Get the selected claim data
  const selectedClaim = useMemo(
    () =>
      selectedClaimIndex == null ? null : (claims[selectedClaimIndex] ?? null),
    [claims, selectedClaimIndex]
  );

  const selectedEvidence = selectedClaim
    ? claimEvidenceMap.get(selectedClaim.claim_id) ?? []
    : [];

  const handleClaimClick = (claimIndex: number) => {
    setSelectedClaimIndex((prev) => (prev === claimIndex ? null : claimIndex));
  };

  const handleCloseInspector = () => {
    setSelectedClaimIndex(null);
  };

  return (
    <>
      <div className="my-3 rounded-2xl border border-border bg-surface/70 p-3">
        <div className="mb-2 flex items-center gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-muted">
            Утверждения
          </span>
          <Badge variant="default" className="text-[10px]">
            {claims.length}
          </Badge>
        </div>

        <div className="space-y-2">
          {claims.map((claim, claimIndex) => {
            const linkedEvidence = claimEvidenceMap.get(claim.claim_id) ?? [];
            const { labels } = getEvidenceTypeSummary(linkedEvidence);

            return (
              <ClaimBadgeItem
                key={`${claim.claim_id}:${claimIndex}`}
                claim={claim}
                evidenceCount={linkedEvidence.length}
                evidenceTypeSummary={labels}
                isSelected={selectedClaimIndex === claimIndex}
                onClick={() => handleClaimClick(claimIndex)}
              />
            );
          })}
        </div>
      </div>

      <EvidenceInspector
        open={selectedClaimIndex !== null}
        onClose={handleCloseInspector}
        claim={selectedClaim}
        evidence={selectedEvidence}
      />
    </>
  );
}
