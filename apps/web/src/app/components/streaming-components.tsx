"use client";

import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, ChevronUp, Brain, Wrench, AlertCircle, CheckCircle2 } from "lucide-react";
import { useState } from "react";
import { cn } from "@rhetorica/ui";
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
      className="my-2 border border-[#2e3d59]/50 rounded-lg overflow-hidden bg-[#0a111d]/40"
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 bg-[#101c30]/60 hover:bg-[#101c30]/80 transition-colors"
      >
        <Brain className="h-4 w-4 text-[#7484a3]" />
        <span className="text-xs font-medium text-[#7484a3] uppercase tracking-wider">
          Thinking
        </span>
        <span className="text-xs text-[#5da8ff]/60 ml-auto">
          {timestamp ? new Date(timestamp).toLocaleTimeString("en-GB") : ""}
        </span>
        {expanded ? (
          <ChevronUp className="h-3 w-3 text-[#7484a3]" />
        ) : (
          <ChevronDown className="h-3 w-3 text-[#7484a3]" />
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
            <div className="px-3 py-2.5 text-xs text-[#a9b8d4]/80 italic leading-relaxed border-t border-[#2e3d59]/30">
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
          return `${key}: [${value.length} items]`;
        }
        if (value && typeof value === "object") {
          return `${key}: {${Object.keys(value).length} fields}`;
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
            ? "border-[#38c67d]/30 bg-[#0b1a12]/40"
            : "border-[#ff6b6b]/30 bg-[#1a0b0b]/40"
          : "border-[#5da8ff]/30 bg-[#0a1525]/40"
      )}
    >
      <div className="flex items-center gap-2 px-3 py-2">
        {isComplete ? (
          success ? (
            <CheckCircle2 className="h-4 w-4 text-[#38c67d]" />
          ) : (
            <AlertCircle className="h-4 w-4 text-[#ff6b6b]" />
          )
        ) : (
          <Wrench className="h-4 w-4 text-[#5da8ff] animate-pulse" />
        )}
        <span className={cn(
          "text-xs font-medium",
          isComplete
            ? success
              ? "text-[#38c67d]"
              : "text-[#ff6b6b]"
            : "text-[#5da8ff]"
        )}>
          {formatToolName(tool_name)}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {displayTimestamp ? (
            <span className="text-xs text-[#7484a3]">
              {new Date(displayTimestamp).toLocaleTimeString("en-GB")}
            </span>
          ) : null}
          {!isComplete && (
            <span className="text-xs text-[#5da8ff]/60 animate-pulse">
              Running...
            </span>
          )}
        </div>
        {duration && (
          <span className="text-xs text-[#7484a3] ml-2">
            {duration}s
          </span>
        )}
      </div>

      {(args || result_summary) && (
        <div className="px-3 pb-2">
          {args && (
            <div className="text-xs text-[#7484a3] mb-1">
              <span className="font-medium">Arguments:</span> {formatArgs(args)}
            </div>
          )}
          {result_summary && (
            <div className={cn(
              "text-xs",
              success ? "text-[#38c67d]/80" : "text-[#ff6b6b]/80"
            )}>
              <span className="font-medium">Result:</span> {result_summary}
            </div>
          )}
          {metadataScoring ? (
            <div className="mt-3 space-y-3 rounded-lg border border-[#2e3d59]/40 bg-[#0a111d]/45 p-3">
              {metadataScoring.chosen_table_fqn ? (
                <div className="text-xs text-[#d8e5ff]">
                  <span className="font-medium text-[#9ec3ff]">Selected:</span>{" "}
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
                        ? "border-[#5b302f] bg-[#1a0f10]/60"
                        : "border-[#233654] bg-[#0d1828]/60"
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <span className="rounded-full border border-[#35517d] bg-[#10213a] px-2 py-0.5 text-[11px] font-semibold text-[#9ec3ff]">
                        Score {Math.round(candidate.relevance_score * 1000) / 1000}
                      </span>
                      {candidate.tier ? (
                        <span className="text-[11px] uppercase tracking-wide text-[#7be4a9]">
                          {candidate.tier}
                        </span>
                      ) : null}
                      {candidate.certification ? (
                        <span className="text-[11px] uppercase tracking-wide text-[#f5c06a]">
                          {candidate.certification}
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-1 text-xs font-medium text-[#f4f7ff]">{candidate.fqn}</div>
                    {candidate.matched_on.length > 0 ? (
                      <p className="mt-1 text-xs leading-5 text-[#c6d4ee]">
                        matched on: {candidate.matched_on.join(", ")}
                      </p>
                    ) : null}
                    {candidate.reason_codes.length > 0 ? (
                      <p className="text-xs leading-5 text-[#8fa5c8]">
                        reasons: {candidate.reason_codes.join(", ")}
                      </p>
                    ) : null}
                    {[
                      ...(candidate.owner_teams ?? []).map((item) => `team: ${item}`),
                      ...(candidate.domains ?? []).map((item) => `domain: ${item}`),
                      ...(candidate.glossary_terms ?? []).map((item) => `term: ${item}`),
                      ...(candidate.related_metrics ?? []).map((item) => `metric: ${item}`),
                    ].length > 0 ? (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {[
                          ...(candidate.owner_teams ?? []).map((item) => `team: ${item}`),
                          ...(candidate.domains ?? []).map((item) => `domain: ${item}`),
                          ...(candidate.glossary_terms ?? []).map((item) => `term: ${item}`),
                          ...(candidate.related_metrics ?? []).map((item) => `metric: ${item}`),
                        ].slice(0, 8).map((label) => (
                          <span
                            key={label}
                            className="rounded border border-[#314867] bg-[#101d31] px-1.5 py-0.5 text-[11px] text-[#a9b8d4]"
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
                            ? "text-[#ff9a9a]"
                            : "text-[#7be4a9]"
                        )}
                      >
                        quality: {candidate.quality_status}
                        {candidate.quality_summary
                          ? ` (${candidate.quality_summary.success}/${candidate.quality_summary.total} passing)`
                          : ""}
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {optimizer ? (
            <div className="mt-3 space-y-3 rounded-lg border border-[#2e3d59]/40 bg-[#0a111d]/45 p-3">
              {optimizer.rules_triggered.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {optimizer.rules_triggered.map((ruleId) => (
                    <span
                      key={ruleId}
                      className="rounded-full border border-[#35517d] bg-[#10213a] px-2 py-0.5 text-[11px] text-[#9ec3ff]"
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
                      className="rounded-md border border-[#3a2f22] bg-[#181209]/50 px-2.5 py-2"
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] font-semibold uppercase tracking-wide text-[#f5c06a]">
                          {finding.severity ?? "info"}
                        </span>
                        <span className="text-xs font-medium text-[#f4f7ff]">{finding.title}</span>
                      </div>
                      <p className="mt-1 text-xs leading-5 text-[#c6d4ee]">{finding.summary}</p>
                    </div>
                  ))}
                </div>
              ) : null}

              {optimizer.recommended_actions.length > 0 ? (
                <div className="space-y-2">
                  {optimizer.recommended_actions.map((action) => (
                    <div
                      key={action.action_id}
                      className="rounded-md border border-[#1f4d35] bg-[#0d1812]/60 px-2.5 py-2"
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] font-semibold uppercase tracking-wide text-[#7be4a9]">
                          {action.applied ? "Applied" : "Suggested"}
                        </span>
                        <span className="text-xs font-medium text-[#f4f7ff]">{action.title}</span>
                      </div>
                      <p className="mt-1 text-xs leading-5 text-[#c6d4ee]">{action.summary}</p>
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
          className="w-full px-3 py-1.5 bg-[#0f1829]/60 hover:bg-[#0f1829]/80 transition-colors flex items-center justify-center gap-1"
        >
          <span className="text-xs text-[#5da8ff]/80">
            {expanded ? "Hide" : "Show"} arguments
          </span>
          {expanded ? (
            <ChevronUp className="h-3 w-3 text-[#5da8ff]" />
          ) : (
            <ChevronDown className="h-3 w-3 text-[#5da8ff]" />
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
            className="overflow-hidden border-t border-[#2e3d59]/30"
          >
            <pre className="px-3 py-2 text-xs text-[#a9b8d4]/80 font-mono overflow-x-auto bg-[#0a111d]/40">
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

  const getStatusColor = (status: ExecutionStatusValue) => {
    switch (status) {
      case "queued":
        return "text-[#7484a3]";
      case "running":
        return "text-[#5da8ff]";
      case "streaming_results":
        return "text-[#38c67d]";
      case "done":
        return "text-[#38c67d]";
      case "failed":
        return "text-[#ff6b6b]";
      default:
        return "text-[#7484a3]";
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
      className="my-2 p-3 border border-[#2e3d59]/50 rounded-lg bg-[#0a111d]/40"
    >
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs font-medium text-[#7484a3] uppercase tracking-wider">
          SQL Execution
        </span>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-lg">{getStatusIcon(latestStatus.status)}</span>
        <span className={cn("text-sm font-medium", getStatusColor(latestStatus.status))}>
          {latestStatus.status.replace("_", " ").toUpperCase()}
        </span>
        {latestStatus.message && (
          <span className="text-xs text-[#7484a3] ml-2">
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
      timestamp?: string;
      insertionKey: number;
      block: ThinkingBlockEntry;
    }
  | {
      kind: "tool_call";
      id: string;
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

function buildIntermediateTimeline(
  thinkingBlocks: ThinkingBlockEntry[],
  toolCalls: ToolCallEntry[]
): TimelineItem[] {
  return [
    ...thinkingBlocks.map((block, index) => ({
      kind: "thinking" as const,
      id: block.id,
      timestamp: block.timestamp,
      insertionKey: block.sequence ?? getInsertionKey(block.id, index),
      block,
    })),
    ...toolCalls.map((tool, index) => ({
      kind: "tool_call" as const,
      id: tool.id,
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

  const summary = (
    <span className="truncate text-xs text-[#7484a3]">
      {thinkingBlocks.length > 0 && `${thinkingBlocks.length} thinking block${thinkingBlocks.length !== 1 ? "s" : ""}`}
      {toolCalls.length > 0 && `${thinkingBlocks.length > 0 ? ", " : ""}${toolCalls.length} tool call${toolCalls.length !== 1 ? "s" : ""}`}
      {executionStatuses.length > 0 && `${thinkingBlocks.length > 0 || toolCalls.length > 0 ? ", " : ""}SQL executed`}
    </span>
  );

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className={cn(
        "my-2 overflow-hidden rounded-xl border",
        isStreaming
          ? "border-[#5da8ff]/35 bg-[#0a1525]/28"
          : "border-[#22314d]/55 bg-[#07101d]/38"
      )}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        aria-label={`${expanded ? "Hide" : "Show"} system run log`}
        className={cn(
          "flex w-full items-center gap-2 px-3 py-2 transition-colors",
          isStreaming
            ? "bg-[#0d1d32]/58 hover:bg-[#0f223b]/72"
            : "bg-[#0c1525]/52 hover:bg-[#101c30]/68"
        )}
      >
        {expanded ? (
          <ChevronUp className="h-3.5 w-3.5 text-[#7484a3]" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 text-[#7484a3]" />
        )}
        <span className="flex shrink-0 items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#8ea1c3]">
          System log
          {isStreaming && (
            <span className="text-[#5da8ff] normal-case animate-pulse">live</span>
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
            className="overflow-hidden border-t border-[#2e3d59]/30"
          >
            <div className="space-y-2 bg-[#08101c]/42 p-3">
              {timeline.map((item) =>
                item.kind === "thinking" ? (
                  <ThinkingBlock
                    key={item.id}
                    content={item.block.content}
                    timestamp={item.block.timestamp}
                    isCollapsed={false}
                  />
                ) : (
                  <ToolCallCard
                    key={item.id}
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
