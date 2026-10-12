"use client";

import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, Wrench, AlertCircle, CheckCircle2 } from "lucide-react";
import { useId, useState } from "react";
import { cn } from "@rhetorica/ui";
import { formatInMoscow } from "@/lib/dateTime";
import { toolDisplayName } from "@/features/chat/toolDisplay";

interface ToolCallCardProps {
  tool_name: string;
  arguments?: Record<string, unknown>;
  result_summary?: string;
  success?: boolean;
  started_at?: string;
  completed_at?: string;
  isComplete?: boolean;
  isInterrupted?: boolean;
}

interface OptimizerFindingDetails {
  rule_id: string;
  title: string;
  summary: string;
  severity?: string;
}

interface OptimizerActionDetails {
  action_id: string;
  title: string;
  summary: string;
  applied: boolean;
}

interface OptimizerToolArguments {
  rules_triggered: string[];
  skills_used: string[];
  evidence_used: string[];
  findings: OptimizerFindingDetails[];
  recommended_actions: OptimizerActionDetails[];
}

interface MetadataScoringCandidate {
  fqn: string;
  relevance_score: number;
  matched_on: string[];
  reason_codes: string[];
  certification?: string | null;
  tier?: string | null;
  owners: string[];
  owner_teams?: string[];
  domains: string[];
  glossary_terms?: string[];
  related_metrics?: string[];
  quality_status?: string | null;
  quality_summary?: {
    status: string;
    total: number;
    success: number;
    failed: number;
    aborted: number;
    failed_tests: string[];
  } | null;
  suspicious: boolean;
}

interface MetadataScoringArguments {
  chosen_table_fqn?: string | null;
  selection_rationale?: string | null;
  candidates: MetadataScoringCandidate[];
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function getOptimizerArguments(
  args?: Record<string, unknown>
): OptimizerToolArguments | null {
  if (!args) {
    return null;
  }

  const findings = Array.isArray(args.findings)
    ? args.findings.filter(
        (item): item is OptimizerFindingDetails =>
          Boolean(
            item &&
              typeof item === "object" &&
              "rule_id" in item &&
              "title" in item &&
              "summary" in item
          )
      )
    : [];
  const recommendedActions = Array.isArray(args.recommended_actions)
    ? args.recommended_actions.filter(
        (item): item is OptimizerActionDetails =>
          Boolean(
            item &&
              typeof item === "object" &&
              "action_id" in item &&
              "title" in item &&
              "summary" in item &&
              "applied" in item
          )
      )
    : [];

  const hasOptimizerShape =
    findings.length > 0 ||
    recommendedActions.length > 0 ||
    isStringArray(args.rules_triggered) ||
    isStringArray(args.evidence_used) ||
    isStringArray(args.skills_used);
  if (!hasOptimizerShape) {
    return null;
  }

  return {
    rules_triggered: isStringArray(args.rules_triggered) ? args.rules_triggered : [],
    skills_used: isStringArray(args.skills_used) ? args.skills_used : [],
    evidence_used: isStringArray(args.evidence_used) ? args.evidence_used : [],
    findings,
    recommended_actions: recommendedActions,
  };
}

function getMetadataScoringArguments(
  args?: Record<string, unknown>
): MetadataScoringArguments | null {
  const raw = args?.semantic_grounding;
  if (!raw || typeof raw !== "object" || !("candidates" in raw) || !Array.isArray(raw.candidates)) {
    return null;
  }

  const candidates = raw.candidates.filter(
    (item): item is MetadataScoringCandidate =>
      Boolean(
        item &&
          typeof item === "object" &&
          "fqn" in item &&
          "relevance_score" in item &&
          "matched_on" in item &&
          "reason_codes" in item
      )
  );
  if (candidates.length === 0) {
    return null;
  }

  return {
    chosen_table_fqn:
      "chosen_table_fqn" in raw && typeof raw.chosen_table_fqn === "string"
        ? raw.chosen_table_fqn
        : null,
    selection_rationale:
      "selection_rationale" in raw && typeof raw.selection_rationale === "string"
        ? raw.selection_rationale
        : null,
    candidates,
  };
}

/**
 * Tool call card - displays tool execution status
 */
export function ToolCallCard({
  tool_name,
  arguments: args,
  result_summary,
  success,
  started_at,
  completed_at,
  isComplete = false,
  isInterrupted = false,
}: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);
  const hasArgs = Boolean(args && Object.keys(args).length);
  const detailsId = useId();
  const optimizer = getOptimizerArguments(args);
  const metadataScoring = getMetadataScoringArguments(args);


  const formatArgs = (args?: Record<string, unknown>) => {
    if (!args) return null;
    return Object.entries(args)
      .map(([key, value]) => {
        if (key === "argv" && Array.isArray(value)) return value.map(String).join(" ");
        if (Array.isArray(value)) return `${key}: ${value.map(item => typeof item === "string" ? item : JSON.stringify(item)).join(", ")}`;
        if (value && typeof value === "object") {
          return `${key}: {${Object.keys(value).length} полей}`;
        }
        return `${key}: ${JSON.stringify(value)}`;
      })
      .join(", ");
  };

  const duration = started_at && completed_at
    ? Math.round((new Date(completed_at).getTime() - new Date(started_at).getTime()) / 1000 * 100) / 100
    : null;
  const displayTimestamp = started_at ?? completed_at;

  return (
    <motion.div
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.2 }}
      className={cn(
        "my-2 border rounded-lg overflow-hidden",
        isComplete
          ? success
            ? "border-success/30 bg-success-subtle/40"
            : "border-danger/30 bg-danger-subtle/40"
          : "border-accent/30 bg-surface/40"
      )}
    >
      <button type="button" disabled={!hasArgs} onClick={() => setExpanded(!expanded)} aria-expanded={hasArgs ? expanded : undefined} aria-controls={hasArgs ? detailsId : undefined} aria-label={hasArgs ? `${expanded ? "Скрыть" : "Показать"} аргументы: ${toolDisplayName(tool_name)}` : undefined} className="flex w-full items-center gap-2 px-3 py-2 text-left enabled:hover:bg-hover disabled:cursor-default">
        {isComplete ? (
          success ? (
            <CheckCircle2 className="h-4 w-4 text-success" />
          ) : (
            <AlertCircle className="h-4 w-4 text-danger" />
          )
        ) : (
          <Wrench className={cn("h-4 w-4 text-accent", !isInterrupted && "animate-pulse")} />
        )}
        <span className={cn(
          "text-xs font-medium",
          isComplete
            ? success
              ? "text-success"
              : "text-danger"
            : "text-accent"
        )}>
          {toolDisplayName(tool_name)}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {displayTimestamp ? (
            <span className="text-xs text-muted">
              {formatInMoscow(displayTimestamp, {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              })}
            </span>
          ) : null}
          {!isComplete && (
            <span className="text-xs text-accent/60 animate-pulse">
              {isInterrupted ? "Нет результата" : "Выполняется…"}
            </span>
          )}
        </div>
        {duration !== null && (
          <span className="text-xs text-muted ml-2">
            {duration}s
          </span>
        )}
        {hasArgs && <ChevronDown aria-hidden="true" className={cn("h-3.5 w-3.5 shrink-0 text-muted transition-transform", expanded && "rotate-180")} />}
      </button>

      {(hasArgs || result_summary) && (
        <div className="px-3 pb-2">
          {hasArgs && (
            <div className="truncate text-xs text-muted mb-1" title={formatArgs(args) ?? undefined}>
              <span className="font-medium">Аргументы:</span> {formatArgs(args)}
            </div>
          )}
          {result_summary && (
            <div className={cn(
              "text-xs whitespace-pre-wrap break-words",
              success === false ? "text-danger/80" : "text-secondary"
            )}>
              <span className="font-medium">Результат:</span> {result_summary}
            </div>
          )}
          {metadataScoring ? (
            <div className="mt-3 space-y-3 rounded-lg border border-border-strong/40 bg-surface/45 p-3">
              {metadataScoring.chosen_table_fqn ? (
                <div className="text-xs text-primary">
                  <span className="font-medium text-accent">Выбрано:</span>{" "}
                  {metadataScoring.chosen_table_fqn}
                </div>
              ) : null}

              <div className="space-y-2">
                {metadataScoring.candidates.map((candidate) => (
                  <div
                    key={candidate.fqn}
                    className={cn(
                      "rounded-md border px-2.5 py-2",
                      candidate.suspicious
                        ? "border-danger-border bg-danger-subtle/60"
                        : "border-border-strong bg-surface/60"
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <span className="rounded-full border border-accent-border bg-elevated px-2 py-0.5 text-[11px] font-semibold text-accent">
                        Оценка: {Math.round(candidate.relevance_score * 1000) / 1000}
                      </span>
                      {candidate.tier ? (
                        <span className="text-[11px] uppercase tracking-wide text-success">
                          {candidate.tier}
                        </span>
                      ) : null}
                      {candidate.certification ? (
                        <span className="text-[11px] uppercase tracking-wide text-warning">
                          {candidate.certification}
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-1 text-xs font-medium text-primary">{candidate.fqn}</div>
                    {candidate.matched_on.length > 0 ? (
                      <p className="mt-1 text-xs leading-5 text-primary">
                        совпадения по: {candidate.matched_on.join(", ")}
                      </p>
                    ) : null}
                    {candidate.reason_codes.length > 0 ? (
                      <p className="text-xs leading-5 text-secondary">
                        причины: {candidate.reason_codes.join(", ")}
                      </p>
                    ) : null}
                    {[
                      ...(candidate.owner_teams ?? []).map((item) => `команда: ${item}`),
                      ...(candidate.domains ?? []).map((item) => `домен: ${item}`),
                      ...(candidate.glossary_terms ?? []).map((item) => `термин: ${item}`),
                      ...(candidate.related_metrics ?? []).map((item) => `метрика: ${item}`),
                    ].length > 0 ? (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {[
                          ...(candidate.owner_teams ?? []).map((item) => `команда: ${item}`),
                          ...(candidate.domains ?? []).map((item) => `домен: ${item}`),
                          ...(candidate.glossary_terms ?? []).map((item) => `термин: ${item}`),
                          ...(candidate.related_metrics ?? []).map((item) => `метрика: ${item}`),
                        ].slice(0, 8).map((label) => (
                          <span
                            key={label}
                            className="rounded border border-border-strong bg-elevated px-1.5 py-0.5 text-[11px] text-secondary"
                          >
                            {label}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {candidate.quality_status ? (
                      <p
                        className={cn(
                          "mt-2 text-xs leading-5",
                          candidate.quality_status === "failed" || candidate.quality_status === "aborted"
                            ? "text-danger"
                            : "text-success"
                        )}
                      >
                        качество: {candidate.quality_status}
                        {candidate.quality_summary
                          ? ` (${candidate.quality_summary.success}/${candidate.quality_summary.total} успешно)`
                          : ""}
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {optimizer ? (
            <div className="mt-3 space-y-3 rounded-lg border border-border-strong/40 bg-surface/45 p-3">
              {optimizer.rules_triggered.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {optimizer.rules_triggered.map((ruleId) => (
                    <span
                      key={ruleId}
                      className="rounded-full border border-accent-border bg-elevated px-2 py-0.5 text-[11px] text-accent"
                    >
                      {ruleId}
                    </span>
                  ))}
                </div>
              ) : null}

              {optimizer.findings.length > 0 ? (
                <div className="space-y-2">
                  {optimizer.findings.map((finding) => (
                    <div
                      key={finding.rule_id}
                      className="rounded-md border border-warning-border bg-warning-subtle/50 px-2.5 py-2"
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] font-semibold uppercase tracking-wide text-warning">
                          {finding.severity ?? "info"}
                        </span>
                        <span className="text-xs font-medium text-primary">{finding.title}</span>
                      </div>
                      <p className="mt-1 text-xs leading-5 text-primary">{finding.summary}</p>
                    </div>
                  ))}
                </div>
              ) : null}

              {optimizer.recommended_actions.length > 0 ? (
                <div className="space-y-2">
                  {optimizer.recommended_actions.map((action) => (
                    <div
                      key={action.action_id}
                      className="rounded-md border border-success-border bg-success-subtle/60 px-2.5 py-2"
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] font-semibold uppercase tracking-wide text-success">
                          {action.applied ? "Применено" : "Предложено"}
                        </span>
                        <span className="text-xs font-medium text-primary">{action.title}</span>
                      </div>
                      <p className="mt-1 text-xs leading-5 text-primary">{action.summary}</p>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      )}

      <AnimatePresence>
        {expanded && hasArgs && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden border-t border-border-strong/30"
          >
            <pre id={detailsId} className="max-h-72 px-3 py-2 text-xs text-secondary/80 font-mono overflow-auto bg-surface/40">
              {JSON.stringify(args, null, 2)}
            </pre>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
