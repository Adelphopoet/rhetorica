"use client";

import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, ChevronUp, Brain } from "lucide-react";
import { useId, useState } from "react";
import { cn } from "@rhetorica/ui";
import { formatInMoscow } from "@/lib/dateTime";
import { ToolCallCard } from "./tool-call-card";
export { ToolCallCard } from "./tool-call-card";
import { toolDisplayName } from "@/features/chat/toolDisplay";
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
        aria-expanded={expanded}
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

      {!expanded && <div className="px-3 py-2"><p className="line-clamp-2 text-xs leading-relaxed text-muted">{content}</p></div>}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="max-h-64 overflow-auto whitespace-pre-wrap px-3 py-2.5 text-xs text-secondary/80 leading-relaxed border-t border-border-strong/30">
              {content}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

interface ExecutionProgressProps {
  statuses: ExecutionStatusEntry[];
}

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

/**
 * Execution progress indicator
 */
export function ExecutionProgress({ statuses }: ExecutionProgressProps) {
  if (statuses.length === 0) return null;

  const latestStatus = statuses[statuses.length - 1];

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
  const journalId = useId();

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

  const failedCount = toolCalls.filter(tool => tool.success === false).length;
  const activeTool = isStreaming ? [...toolCalls].reverse().find(tool => !tool.completed_at) : undefined;
  const summary = (
    <span className="truncate text-xs text-muted">
      {thinkingBlocks.length > 0 && `${thinkingBlocks.length} ${thinkingCountLabel}`}
      {toolCalls.length > 0 && `${thinkingBlocks.length > 0 ? ", " : ""}${toolCalls.length} ${toolCountLabel}`}
      {executionStatuses.length > 0 && `${thinkingBlocks.length > 0 || toolCalls.length > 0 ? ", " : ""}SQL: ${getStatusLabel(executionStatuses.at(-1)!.status)}`}
      {failedCount > 0 && <span className="ml-2 text-danger">Ошибок: {failedCount}</span>}
      {activeTool && <span className="ml-2 text-accent">{toolDisplayName(activeTool.tool_name)}</span>}
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
        aria-expanded={expanded}
        aria-controls={journalId}
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
            <div id={journalId} className="space-y-2 bg-surface/[0.42] p-3">
              {timeline.map((item) =>
                item.kind === "thinking" ? (
                  <ThinkingBlock
                    key={item.renderKey}
                    content={item.block.content}
                    timestamp={item.block.timestamp}
                    isCollapsed={!isStreaming}
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
                    isInterrupted={!isStreaming && !item.tool.completed_at}
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
