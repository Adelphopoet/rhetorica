import type { ChatMessageViewModel } from "./chatViewModel";
import type { AnalysisBlock, AnalysisDocument } from "@/features/analysis/types";
import { mergeToolCallsWithOptimizationTrace } from "./chatViewModel";
import type {
  ExecutionStatusEntry,
  ThinkingBlockEntry,
  ToolCallEntry,
} from "./useChatStreaming";
import { DISPLAY_TIME_ZONE_LABEL, formatInMoscow } from "@/lib/dateTime";

function formatTimestampForCopy(value?: Date | string): string | null {
  if (!value) {
    return null;
  }

  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  const formatted = formatInMoscow(
    date,
    {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    },
    "sv-SE"
  );
  return formatted ? `${formatted} ${DISPLAY_TIME_ZONE_LABEL}` : null;
}

function indentBlock(value: string, spaces = 2): string {
  const prefix = " ".repeat(spaces);
  return value
    .split("\n")
    .map((line) => `${prefix}${line}`)
    .join("\n");
}

function formatExecutionStatus(status: string): string {
  return status.replace(/_/g, " ");
}

function formatToolArguments(argumentsPayload?: Record<string, unknown>): string | null {
  if (!argumentsPayload || Object.keys(argumentsPayload).length === 0) {
    return null;
  }

  return JSON.stringify(argumentsPayload, null, 2);
}

function serializeAnalysisBlock(block: AnalysisBlock): string | null {
  if (block.kind === "section_header") {
    return [`## ${block.title}`, block.description ?? ""].filter(Boolean).join("\n\n");
  }
  if (block.kind === "narrative_note") {
    const markdown = block.markdown.trim();
    if (!markdown || /^-{3,}$/.test(markdown)) {
      return null;
    }
    return [block.title ? `### ${block.title}` : null, markdown].filter(Boolean).join("\n\n");
  }
  if (block.kind === "finding") {
    return [`### ${block.title}`, block.body].join("\n\n");
  }
  if (block.kind === "caveat") {
    if (
      block.title.trim().toLowerCase() === "warnings" &&
      /^[a-z0-9_;,\s-]+$/.test(block.body.trim())
    ) {
      return null;
    }
    return [`### Оговорка: ${block.title}`, block.body].join("\n\n");
  }
  if (block.kind === "metric_grid") {
    const title = block.title ? `### ${block.title}` : "### Метрики";
    const rows = block.items.map((item) => {
      const details = [item.delta, item.context].filter(Boolean).join(" · ");
      return `- ${item.label}: ${item.value}${details ? ` (${details})` : ""}`;
    });
    return [title, block.description ?? "", rows.join("\n")].filter(Boolean).join("\n\n");
  }
  if (block.kind === "chart") {
    return [
      `### График: ${block.title ?? block.visualization.title ?? "Визуализация"}`,
      block.description ?? block.visualization.description ?? "",
      block.insight ?? block.visualization.summary ?? "",
    ].filter(Boolean).join("\n\n");
  }
  if (block.kind === "table") {
    return [
      `### ${block.title ?? "Строки детализации"}`,
      block.description ?? "Исходные строки доступны в инспекторе",
      `Действие: ${block.action_label ?? "Показать строки"}`,
    ].join("\n\n");
  }
  if (block.kind === "divider") {
    return "---";
  }
  return null;
}

export function serializeAnalysisDocumentForCopy(document?: AnalysisDocument | null): string | null {
  if (!document?.blocks.length && !document?.executive_summary) {
    return null;
  }

  const lines = [
    `# ${document.title?.trim() || "Аналитический отчёт"}`,
    document.executive_summary ? `## Краткое резюме\n\n${document.executive_summary}` : null,
    ...document.blocks.map(serializeAnalysisBlock),
  ].filter((item): item is string => Boolean(item && item.trim().length > 0));

  return lines.join("\n\n");
}

function serializeSqlSnapshotForCopy(payload?: ChatMessageViewModel["payload"]): string | null {
  const generatedSql = payload?.generated_sql?.trim() || null;
  const executedSql = payload?.execution?.normalized_sql?.trim() || null;
  const primarySql = executedSql ?? generatedSql;
  if (!primarySql) {
    return null;
  }

  const blocks = [
    executedSql && executedSql !== generatedSql
      ? `## Выполненный SQL\n\n\`\`\`sql\n${executedSql}\n\`\`\``
      : `## SQL\n\n\`\`\`sql\n${primarySql}\n\`\`\``,
  ];

  if (executedSql && generatedSql && executedSql !== generatedSql) {
    blocks.push(`## Исходный черновик SQL\n\n\`\`\`sql\n${generatedSql}\n\`\`\``);
  }

  return blocks.join("\n\n");
}

type CopyTimelineItem =
  | {
      kind: "thinking";
      timestamp?: string;
      index: number;
      block: ThinkingBlockEntry;
    }
  | {
      kind: "tool";
      timestamp?: string;
      index: number;
      tool: ToolCallEntry;
    }
  | {
      kind: "execution";
      timestamp?: string;
      index: number;
      status: ExecutionStatusEntry;
    };

function getSortableTimestamp(value?: string): number {
  if (!value) {
    return Number.POSITIVE_INFINITY;
  }

  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? Number.POSITIVE_INFINITY : timestamp;
}

export function serializeSystemRunLog(args: {
  thinkingBlocks?: ThinkingBlockEntry[];
  toolCalls?: ToolCallEntry[];
  executionStatuses?: ExecutionStatusEntry[];
}): string | null {
  const thinkingBlocks = args.thinkingBlocks ?? [];
  const toolCalls = args.toolCalls ?? [];
  const executionStatuses = args.executionStatuses ?? [];

  const timeline: CopyTimelineItem[] = [
    ...thinkingBlocks.map((block, index) => ({
      kind: "thinking" as const,
      timestamp: block.timestamp,
      index,
      block,
    })),
    ...toolCalls.map((tool, index) => ({
      kind: "tool" as const,
      timestamp: tool.started_at ?? tool.completed_at,
      index: thinkingBlocks.length + index,
      tool,
    })),
    ...executionStatuses.map((status, index) => ({
      kind: "execution" as const,
      timestamp: status.timestamp,
      index: thinkingBlocks.length + toolCalls.length + index,
      status,
    })),
  ].sort((left, right) => {
    const timestampDiff = getSortableTimestamp(left.timestamp) - getSortableTimestamp(right.timestamp);
    if (timestampDiff !== 0) {
      return timestampDiff;
    }

    return left.index - right.index;
  });

  if (timeline.length === 0) {
    return null;
  }

  return timeline
    .map((item) => {
      if (item.kind === "thinking") {
        const timestamp = formatTimestampForCopy(item.block.timestamp);
        const header = timestamp ? `[${timestamp}] Ход рассуждений` : "Ход рассуждений";
        return `${header}\n${indentBlock(item.block.content || "(пустой блок)")}`;
      }

      if (item.kind === "tool") {
        const timestamp = formatTimestampForCopy(item.tool.started_at ?? item.tool.completed_at);
        const header = timestamp
          ? `[${timestamp}] Вызов инструмента: ${item.tool.tool_name}`
          : `Вызов инструмента: ${item.tool.tool_name}`;
        const lines = [header];
        const argumentsBlock = formatToolArguments(item.tool.arguments);

        if (argumentsBlock) {
          lines.push(`Аргументы:\n${indentBlock(argumentsBlock, 4)}`);
        }
        if (item.tool.result_summary) {
          lines.push(`Результат:\n${indentBlock(item.tool.result_summary, 4)}`);
        }
        if (typeof item.tool.success === "boolean") {
          lines.push(`Успех: ${item.tool.success ? "да" : "нет"}`);
        }

        return lines.join("\n");
      }

      const timestamp = formatTimestampForCopy(item.status.timestamp);
      const header = timestamp
        ? `[${timestamp}] Статус выполнения: ${formatExecutionStatus(item.status.status)}`
        : `Статус выполнения: ${formatExecutionStatus(item.status.status)}`;
      const lines = [header];

      if (item.status.message) {
        lines.push(`Сообщение:\n${indentBlock(item.status.message, 4)}`);
      }

      return lines.join("\n");
    })
    .join("\n\n");
}

export function serializeChatMessageForCopy(args: {
  role: "user" | "assistant";
  text: string;
  payload?: ChatMessageViewModel["payload"];
  timestamp?: Date;
  userLabel?: string;
  thinkingBlocks?: ThinkingBlockEntry[];
  toolCalls?: ToolCallEntry[];
  executionStatuses?: ExecutionStatusEntry[];
}): string {
  const roleLabel =
    args.role === "user"
      ? args.userLabel?.trim() || "Аналитик"
      : "Rhetorica Assistant";
  const timestamp = formatTimestampForCopy(args.timestamp);
  const header = timestamp ? `[${timestamp}] ${roleLabel}` : roleLabel;
  const messageBody = serializeChatMessageBodyForCopy(args.text, args.payload);
  const runLog = serializeSystemRunLog({
    thinkingBlocks: args.thinkingBlocks,
    toolCalls: args.toolCalls,
    executionStatuses: args.executionStatuses,
  });

  if (!runLog) {
    return `${header}\n\n${messageBody}`;
  }

  return `${header}\n\n${messageBody}\n\nЖурнал выполнения:\n${runLog}`;
}

export function serializeChatMessageBodyForCopy(
  text: string,
  payload?: ChatMessageViewModel["payload"]
): string {
  const plainText = text.trim().length > 0 ? text.trim() : "";
  const reportText = serializeAnalysisDocumentForCopy(payload?.analysis_document);
  const sqlText = serializeSqlSnapshotForCopy(payload);
  if (reportText) {
    return [reportText, sqlText].filter(Boolean).join("\n\n");
  }
  return [plainText || "(пустое сообщение)", sqlText].filter(Boolean).join("\n\n");
}

export function serializeChatHistoryForCopy(
  messages: ChatMessageViewModel[],
  options?: { userLabel?: string }
): string {
  return messages
    .map((message) =>
      serializeChatMessageForCopy({
        role: message.role,
        text: message.text,
        payload: message.payload,
        timestamp: message.timestamp,
        userLabel: options?.userLabel,
        thinkingBlocks: message.thinkingBlocks,
        toolCalls: mergeToolCallsWithOptimizationTrace({
          toolCalls: message.toolCalls,
          payload: message.payload,
          timestamp: message.timestamp,
        }),
        executionStatuses: message.executionStatuses,
      })
    )
    .join("\n\n---\n\n");
}

export async function copyTextToClipboard(text: string): Promise<void> {
  if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
    throw new Error("API буфера обмена недоступно");
  }

  await navigator.clipboard.writeText(text);
}
