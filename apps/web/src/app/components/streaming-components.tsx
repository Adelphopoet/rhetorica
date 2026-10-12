"use client";

import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, ChevronUp, Brain, Wrench, AlertCircle, CheckCircle2 } from "lucide-react";
import { useState } from "react";
import { cn } from "@rhetorica/ui";
import { formatInMoscow } from "@/lib/dateTime";
import type {
  ExecutionStatusEntry,
  ExecutionStatusValue,
  ThinkingBlockEntry,
  ToolCallEntry,
} from "@/features/chat/useChatStreaming";

interface ThinkingBlockProps {
  content: string;
  timestamp?: string;
  isCollapsed?: boolean;
}

/**
 * Thinking block component - displays LLM reasoning
 */
export function ThinkingBlock({ content, timestamp, isCollapsed = false }: ThinkingBlockProps) {
  const [expanded, setExpanded] = useState(!isCollapsed);

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="my-2 border border-border-strong/50 rounded-lg overflow-hidden bg-surface/40"
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 bg-elevated/60 hover:bg-elevated/80 transition-colors"
      >
        <Brain className="h-4 w-4 text-muted" />
        <span className="text-xs font-medium text-muted uppercase tracking-wider">
          Ход рассуждений
        </span>
        <span className="text-xs text-accent/60 ml-auto">
          {timestamp
            ? formatInMoscow(timestamp, {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              }) ?? ""
            : ""}
        </span>
        {expanded ? (
          <ChevronUp className="h-3 w-3 text-muted" />
        ) : (
          <ChevronDown className="h-3 w-3 text-muted" />
        )}
      </button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="px-3 py-2.5 text-xs text-secondary/80 italic leading-relaxed border-t border-border-strong/30">
              {content}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

interface ToolCallCardProps {
  tool_name: string;
  arguments?: Record<string, unknown>;
  result_summary?: string;
  success?: boolean;
  started_at?: string;
  completed_at?: string;
  isComplete?: boolean;
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
}: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);
  const optimizer = getOptimizerArguments(args);
  const metadataScoring = getMetadataScoringArguments(args);

  const formatToolName = (name: string) => {
    return name
      .split("_")
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" ");
  };

  const formatArgs = (args?: Record<string, unknown>) => {
    if (!args) return null;
    return Object.entries(args)
      .map(([key, value]) => {
        if (Array.isArray(value)) {
          return `${key}: [${value.length} элементов]`;
        }
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
      <div className="flex items-center gap-2 px-3 py-2">
        {isComplete ? (
          success ? (
            <CheckCircle2 className="h-4 w-4 text-success" />
          ) : (
            <AlertCircle className="h-4 w-4 text-danger" />
          )
        ) : (
          <Wrench className="h-4 w-4 text-accent animate-pulse" />
        )}
        <span className={cn(
          "text-xs font-medium",
          isComplete
            ? success
              ? "text-success"
              : "text-danger"
            : "text-accent"
        )}>
          {formatToolName(tool_name)}
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
              Выполняется…
            </span>
          )}
        </div>
        {duration && (
          <span className="text-xs text-muted ml-2">
            {duration}s
          </span>
        )}
      </div>

      {(args || result_summary) && (
        <div className="px-3 pb-2">
          {args && (
            <div className="text-xs text-muted mb-1">
              <span className="font-medium">Аргументы:</span> {formatArgs(args)}
            </div>
          )}
          {result_summary && (
            <div className={cn(
              "text-xs",
              success ? "text-success/80" : "text-danger/80"
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

      {args && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full px-3 py-1.5 bg-elevated/60 hover:bg-elevated/80 transition-colors flex items-center justify-center gap-1"
        >
          <span className="text-xs text-accent/80">
            {expanded ? "Скрыть" : "Показать"} аргументы
          </span>
          {expanded ? (
            <ChevronUp className="h-3 w-3 text-accent" />
          ) : (
            <ChevronDown className="h-3 w-3 text-accent" />
          )}
        </button>
      )}

      <AnimatePresence>
        {expanded && args && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden border-t border-border-strong/30"
          >
            <pre className="px-3 py-2 text-xs text-secondary/80 font-mono overflow-x-auto bg-surface/40">
              {JSON.stringify(args, null, 2)}
            </pre>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

interface ExecutionProgressProps {
  statuses: ExecutionStatusEntry[];
}

/**
 * Execution progress indicator
 */
export function ExecutionProgress({ statuses }: ExecutionProgressProps) {
  if (statuses.length === 0) return null;

  const latestStatus = statuses[statuses.length - 1];

  const getStatusLabel = (status: ExecutionStatusValue) => {
    switch (status) {
      case "queued":
        return "В очереди";
      case "running":
        return "Выполняется";
      case "streaming_results":
        return "Поток результатов";
      case "done":
        return "Завершено";
      case "failed":
        return "Не удалось";
      default:
        return status;
    }
  };

  const getStatusColor = (status: ExecutionStatusValue) => {
    switch (status) {
      case "queued":
        return "text-muted";
      case "running":
        return "text-accent";
      case "streaming_results":
        return "text-success";
      case "done":
        return "text-success";
      case "failed":
        return "text-danger";
      default:
        return "text-muted";
    }
  };

  const getStatusIcon = (status: ExecutionStatusValue) => {
    switch (status) {
      case "queued":
        return "⏳";
      case "running":
        return "🔄";
      case "streaming_results":
        return "📊";
      case "done":
        return "✅";
      case "failed":
        return "❌";
      default:
        return "•";
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="my-2 p-3 border border-border-strong/50 rounded-lg bg-surface/40"
    >
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs font-medium text-muted uppercase tracking-wider">
          Выполнение SQL
        </span>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-lg">{getStatusIcon(latestStatus.status)}</span>
        <span className={cn("text-sm font-medium", getStatusColor(latestStatus.status))}>
          {getStatusLabel(latestStatus.status)}
        </span>
        {latestStatus.message && (
          <span className="text-xs text-muted ml-2">
            {latestStatus.message}
          </span>
        )}
      </div>

      {statuses.length > 1 && (
        <div className="mt-2 flex items-center gap-1">
          {statuses.map((s) => (
            <div
              key={`${s.timestamp}-${s.status}`}
              className={cn(
                "h-1.5 flex-1 rounded-full",
                getStatusColor(s.status).replace("text-", "bg-")
              )}
            />
          ))}
        </div>
      )}
    </motion.div>
  );
}

interface CollapsibleThinkingProps {
  thinkingBlocks: ThinkingBlockEntry[];
  toolCalls: ToolCallEntry[];
  executionStatuses: ExecutionStatusEntry[];
  defaultExpanded?: boolean;
  isStreaming?: boolean;
}

type TimelineItem =
  | {
      kind: "thinking";
      id: string;
      renderKey: string;
      timestamp?: string;
      insertionKey: number;
      block: ThinkingBlockEntry;
    }
  | {
      kind: "tool_call";
      id: string;
      renderKey: string;
      timestamp?: string;
      insertionKey: number;
      tool: ToolCallEntry;
    };

function getSortableTimestamp(value?: string): number {
  if (!value) {
    return Number.POSITIVE_INFINITY;
  }

  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? Number.POSITIVE_INFINITY : parsed;
}

function getInsertionKey(id: string, fallback: number): number {
  const [, raw] = id.split("-");
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function nextOccurrenceKey(
  kind: TimelineItem["kind"],
  id: string,
  occurrences: Map<string, number>
): string {
  const occurrence = occurrences.get(id) ?? 0;
  occurrences.set(id, occurrence + 1);
  return `${kind}:${id}:${occurrence}`;
}

function buildIntermediateTimeline(
  thinkingBlocks: ThinkingBlockEntry[],
  toolCalls: ToolCallEntry[]
): TimelineItem[] {
  const thinkingOccurrences = new Map<string, number>();
  const toolOccurrences = new Map<string, number>();

  return [
    ...thinkingBlocks.map((block, index) => ({
      kind: "thinking" as const,
      id: block.id,
      renderKey: nextOccurrenceKey("thinking", block.id, thinkingOccurrences),
      timestamp: block.timestamp,
      insertionKey: block.sequence ?? getInsertionKey(block.id, index),
      block,
    })),
    ...toolCalls.map((tool, index) => ({
      kind: "tool_call" as const,
      id: tool.id,
      renderKey: nextOccurrenceKey("tool_call", tool.id, toolOccurrences),
      timestamp: tool.started_at ?? tool.completed_at,
      insertionKey: tool.sequence ?? getInsertionKey(tool.id, thinkingBlocks.length + index),
      tool,
    })),
  ].sort((left, right) => {
    const timestampDiff = getSortableTimestamp(left.timestamp) - getSortableTimestamp(right.timestamp);
    if (timestampDiff !== 0) {
      return timestampDiff;
    }

    return left.insertionKey - right.insertionKey;
  });
}

/**
 * Collapsible container for all intermediate steps
 */
export function CollapsibleThinking({
  thinkingBlocks,
  toolCalls,
  executionStatuses,
  defaultExpanded = false,
  isStreaming = false,
}: CollapsibleThinkingProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  const hasContent = thinkingBlocks.length > 0 || toolCalls.length > 0 || executionStatuses.length > 0;
  if (!hasContent) return null;
  const timeline = buildIntermediateTimeline(thinkingBlocks, toolCalls);

  const thinkingCountLabel =
    thinkingBlocks.length === 1
      ? "блок рассуждений"
      : thinkingBlocks.length >= 2 && thinkingBlocks.length <= 4
        ? "блока рассуждений"
        : "блоков рассуждений";
  const toolCountLabel =
    toolCalls.length === 1
      ? "вызов инструмента"
      : toolCalls.length >= 2 && toolCalls.length <= 4
        ? "вызова инструмента"
        : "вызовов инструмента";

  const summary = (
    <span className="truncate text-xs text-muted">
      {thinkingBlocks.length > 0 && `${thinkingBlocks.length} ${thinkingCountLabel}`}
      {toolCalls.length > 0 && `${thinkingBlocks.length > 0 ? ", " : ""}${toolCalls.length} ${toolCountLabel}`}
      {executionStatuses.length > 0 && `${thinkingBlocks.length > 0 || toolCalls.length > 0 ? ", " : ""}SQL выполнен`}
    </span>
  );

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className={cn(
        "my-2 overflow-hidden rounded-xl border",
        isStreaming
          ? "border-accent/35 bg-surface/[0.28]"
          : "border-border-strong/55 bg-surface/[0.38]"
      )}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        aria-label={`${expanded ? "Скрыть" : "Показать"} журнал выполнения`}
        className={cn(
          "flex w-full items-center gap-2 px-3 py-2 transition-colors",
          isStreaming
            ? "bg-elevated/[0.58] hover:bg-elevated/[0.72]"
            : "bg-surface/[0.52] hover:bg-elevated/[0.68]"
        )}
      >
        {expanded ? (
          <ChevronUp className="h-3.5 w-3.5 text-muted" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 text-muted" />
        )}
        <span className="flex shrink-0 items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary">
          Журнал выполнения
          {isStreaming && (
            <span className="text-accent normal-case animate-pulse">онлайн</span>
          )}
        </span>
        {summary}
      </button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden border-t border-border-strong/30"
          >
            <div className="space-y-2 bg-surface/[0.42] p-3">
              {timeline.map((item) =>
                item.kind === "thinking" ? (
                  <ThinkingBlock
                    key={item.renderKey}
                    content={item.block.content}
                    timestamp={item.block.timestamp}
                    isCollapsed={false}
                  />
                ) : (
                  <ToolCallCard
                    key={item.renderKey}
                    tool_name={item.tool.tool_name}
                    arguments={item.tool.arguments}
                    result_summary={item.tool.result_summary}
                    success={item.tool.success}
                    started_at={item.tool.started_at}
                    completed_at={item.tool.completed_at}
                    isComplete={!!item.tool.completed_at}
                  />
                )
              )}
              {executionStatuses.length > 0 && (
                <ExecutionProgress statuses={executionStatuses} />
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
