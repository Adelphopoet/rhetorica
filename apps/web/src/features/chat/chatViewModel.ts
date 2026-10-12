import type { AnalysisDocument } from "@/features/analysis/types";
import type { QueryPreview, VisualizationSpec } from "@/features/charts";
import type {
  DoneEvent,
  ExecutionStatusEntry,
  OptimizationTrace,
  ThinkingBlockEntry,
  ToolCallEntry,
} from "./useChatStreaming";
import type {
  AnswerClaim,
  AnswerEvidence,
  CapabilityTrace,
  ClaimEvidenceLink,
  ClarificationRequest,
} from "./streamingTypes";
import { DISPLAY_TIME_ZONE } from "@/lib/dateTime";
import type { FileArtifactRef } from "@/features/files/fileApi";

export type ChatFeedbackValue = "useful" | "wrong" | "unsafe" | "incomplete";

export type FeedbackReason =
  | "wrong_source"
  | "wrong_metric"
  | "unsafe_sql"
  | "incomplete"
  | "bad_chart"
  | "other";

export type ChatFeedbackDetails = {
  whatWentWrong?: string | null;
  expectedAnswer?: string | null;
};

export type ChatDeliveryState =
  | "idle"
  | "streaming"
  | "complete"
  | "cancelled"
  | "error";

export type ExecutionHistoryItem = {
  execution_id: string;
  status: string;
  submitted_sql: string;
  updated_at: string;
  duration_ms: number | null;
  row_count: number | null;
};

export type ExecuteSqlResponse = {
  execution_id: string;
  status: string;
  engine: string;
  normalized_sql: string | null;
  row_count: number | null;
  duration_ms: number | null;
  error_message: string | null;
  preview: QueryPreview;
};

export type ChatAskResponse = {
  answer: string;
  conversation_id: string | null;
  generated_sql: string | null;
  optimization_trace: OptimizationTrace | null;
  capability_trace?: CapabilityTrace | null;
  selected_database_connector: string | null;
  assistant_message_id: string | null;
  metadata_sources: string[];
  context_tables: string[];
  claims: AnswerClaim[];
  evidence: AnswerEvidence[];
  claim_evidence_links: ClaimEvidenceLink[];
  uncertainty_flags: string[];
  execution: ExecuteSqlResponse | null;
  analysis_document: AnalysisDocument | null;
  visualization: VisualizationSpec | null;
  warnings: string[];
  cancelled?: boolean;
  clarification_request?: ClarificationRequest | null;
  files?: FileArtifactRef[];
};

export type ChatMessageViewModel = {
  id: string;
  backendMessageId?: string;
  role: "user" | "assistant";
  text: string;
  payload?: ChatAskResponse;
  feedback?: ChatFeedbackValue | null;
  feedbackReason?: FeedbackReason | null;
  feedbackComment?: string | null;
  feedbackWhatWentWrong?: string | null;
  feedbackExpectedAnswer?: string | null;
  timestamp: Date;
  thinkingBlocks?: ThinkingBlockEntry[];
  toolCalls?: ToolCallEntry[];
  executionStatuses?: ExecutionStatusEntry[];
  deliveryState: ChatDeliveryState;
  clarificationRequest?: ClarificationRequest | null;
  files?: FileArtifactRef[];
};

export type ResultSurfaceViewModel = {
  connectorLabel: string;
  sourcesLabel: string;
  rowCount: number;
  durationLabel: string;
  resultStatus: string;
  generatedSql: string | null;
  executedSql: string | null;
  optimizationTrace: OptimizationTrace | null;
  hasOptimization: boolean;
  hasData: boolean;
  hasVisualization: boolean;
  hasAnalysisDocument: boolean;
  hasEvidence: boolean;
  defaultTab: "result" | "sql" | "optimizer" | "evidence";
  execution: ExecuteSqlResponse | null;
  warnings: string[];
  analysisDocument: AnalysisDocument | null;
  visualization: VisualizationSpec | null;
  claims: AnswerClaim[];
  evidence: AnswerEvidence[];
  claimEvidenceLinks: ClaimEvidenceLink[];
  uncertaintyFlags: string[];
  recentExecutions: ExecutionHistoryItem[];
};

function dedupePreservingOrder(values: string[]): string[] {
  return [...new Set(values)];
}

const METADATA_TOOL_NAMES = new Set([
  "metadata_table_scoring",
]);

function nextSyntheticSequence(toolCalls: ToolCallEntry[]): number | undefined {
  const maxSequence = toolCalls.reduce((currentMax, call) => {
    if (typeof call.sequence !== "number") {
      return currentMax;
    }
    return Math.max(currentMax, call.sequence);
  }, -1);
  return maxSequence >= 0 ? maxSequence + 1 : undefined;
}

function mergeToolCallsWithSemanticGrounding(args: {
  toolCalls?: ToolCallEntry[];
  payload?: ChatAskResponse;
  timestamp?: Date;
}): ToolCallEntry[] {
  const toolCalls = args.toolCalls ?? [];
  const grounding = args.payload?.capability_trace?.runtime_context?.semantic_grounding;
  const candidates = grounding?.candidates ?? [];
  if (candidates.length === 0) {
    return toolCalls;
  }

  const semanticGroundingArguments = {
    chosen_table_fqn: grounding?.chosen_table_fqn ?? null,
    selection_rationale: grounding?.selection_rationale ?? null,
    candidates: candidates.map((candidate: NonNullable<typeof grounding>["candidates"][number]) => ({
      fqn: candidate.fqn,
      service: candidate.service ?? null,
      database: candidate.database ?? null,
      schema: candidate.schema ?? null,
      relevance_score: candidate.relevance_score,
      matched_on: candidate.matched_on ?? [],
      reason_codes: candidate.reason_codes ?? [],
      certification: candidate.certification ?? null,
      tier: candidate.tier ?? null,
      owners: candidate.owners ?? [],
      owner_teams: candidate.owner_teams ?? [],
      domains: candidate.domains ?? [],
      glossary_terms: candidate.glossary_terms ?? [],
      related_metrics: candidate.related_metrics ?? [],
      quality_status: candidate.quality_status ?? null,
      quality_summary: candidate.quality_summary ?? null,
      usage_count: candidate.usage_count ?? 0,
      suspicious: candidate.suspicious ?? false,
    })),
  };
  const lastMetadataToolIndex = [...toolCalls]
    .map((toolCall, index) => ({ toolCall, index }))
    .reverse()
    .find(({ toolCall }) => METADATA_TOOL_NAMES.has(toolCall.tool_name))?.index;
  const candidateWord =
    candidates.length === 1
      ? "кандидат"
      : candidates.length < 5
        ? "кандидата"
        : "кандидатов";
  const summary = `Ранжировано таблиц-кандидатов: ${candidates.length} ${candidateWord}`;

  if (typeof lastMetadataToolIndex === "number") {
    const mergedToolCalls = [...toolCalls];
    const current = mergedToolCalls[lastMetadataToolIndex];
    mergedToolCalls[lastMetadataToolIndex] = {
      ...current,
      arguments: {
        ...(current?.arguments ?? {}),
        semantic_grounding: semanticGroundingArguments,
      },
      result_summary: current?.result_summary ?? summary,
      success: current?.success ?? true,
      completed_at: current?.completed_at ?? args.timestamp?.toISOString(),
    };
    return mergedToolCalls;
  }

  return [
    ...toolCalls,
    {
      id: `tool-${args.timestamp?.getTime() ?? Date.now()}-semantic-grounding`,
      tool_name: "metadata_table_scoring",
      arguments: {
        semantic_grounding: semanticGroundingArguments,
      },
      result_summary: summary,
      success: true,
      started_at: args.timestamp?.toISOString(),
      completed_at: args.timestamp?.toISOString(),
      sequence: nextSyntheticSequence(toolCalls),
    },
  ];
}

export function buildChatAskResponse(event: DoneEvent, answer: string): ChatAskResponse {
  return {
    answer,
    conversation_id: event.conversation_id ?? null,
    generated_sql: event.generated_sql ?? null,
    optimization_trace: event.optimization_trace ?? null,
    capability_trace: event.capability_trace ?? null,
    selected_database_connector: event.selected_database_connector ?? null,
    assistant_message_id: event.assistant_message_id ?? null,
    metadata_sources: event.metadata_sources,
    context_tables: event.context_tables,
    claims: event.claims ?? [],
    evidence: event.evidence ?? [],
    claim_evidence_links: event.claim_evidence_links ?? [],
    uncertainty_flags: event.uncertainty_flags ?? [],
    execution: event.execution ?? null,
    analysis_document: event.analysis_document ?? null,
    visualization: event.visualization ?? null,
    warnings: dedupePreservingOrder(event.warnings),
    cancelled: event.cancelled ?? false,
    clarification_request: event.clarification_request ?? null,
    files: event.files ?? [],
  };
}

export function resolveDeliveryState(args: {
  isStreaming?: boolean;
  isComplete?: boolean;
  isError?: boolean;
  payload?: ChatAskResponse;
}): ChatDeliveryState {
  if (args.isError) {
    return "error";
  }
  if (args.isStreaming) {
    return "streaming";
  }
  if (args.payload?.cancelled) {
    return "cancelled";
  }
  if (args.isComplete) {
    return "complete";
  }
  return "idle";
}

export function buildResultSurfaceViewModel(
  payload: ChatAskResponse | undefined,
  recentExecutions: ExecutionHistoryItem[]
): ResultSurfaceViewModel {
  const execution = payload?.execution ?? null;
  const hasRows = Boolean(execution?.preview?.rows?.length);
  const hasAnalysisDocument = Boolean(payload?.analysis_document?.blocks?.length);
  const hasVisualization = Boolean(payload?.visualization && hasRows);
  const hasEvidence = Boolean(payload?.claims.length);
  const connectorLabel = payload?.selected_database_connector ?? "Ожидание подключения";
  const rowCount = execution?.row_count ?? execution?.preview?.rows?.length ?? 0;
  const durationLabel = execution?.duration_ms ? `${execution.duration_ms} ms` : "н/д";
  const sourcesLabel = payload?.metadata_sources.length
    ? payload.metadata_sources.join(", ")
    : "Нет семантических источников";

  let defaultTab: ResultSurfaceViewModel["defaultTab"] = "result";
  if (hasAnalysisDocument) {
    defaultTab = "result";
  } else if (!hasRows && payload?.generated_sql) {
    defaultTab = "sql";
  } else if (payload?.optimization_trace?.optimizer_used) {
    defaultTab = "optimizer";
  } else if (!hasRows && hasEvidence) {
    defaultTab = "evidence";
  }

  return {
    connectorLabel,
    sourcesLabel,
    rowCount,
    durationLabel,
    resultStatus: execution?.status ?? "idle",
    generatedSql: payload?.generated_sql ?? null,
    executedSql: execution?.normalized_sql ?? null,
    optimizationTrace: payload?.optimization_trace ?? null,
    hasOptimization: Boolean(payload?.optimization_trace?.optimizer_used),
    hasData: hasRows,
    hasVisualization,
    hasAnalysisDocument,
    hasEvidence,
    defaultTab,
    execution,
    warnings: dedupePreservingOrder(payload?.warnings ?? []),
    analysisDocument: payload?.analysis_document ?? null,
    visualization: payload?.visualization ?? null,
    claims: payload?.claims ?? [],
    evidence: payload?.evidence ?? [],
    claimEvidenceLinks: payload?.claim_evidence_links ?? [],
    uncertaintyFlags: payload?.uncertainty_flags ?? [],
    recentExecutions,
  };
}

export function shortenSql(sql: string): string {
  const compact = sql.replace(/\s+/g, " ").trim();
  return compact.length > 96 ? `${compact.slice(0, 96)}...` : compact;
}

export function formatExecutionTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return timestamp;
  }

  return new Intl.DateTimeFormat("en-GB", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: DISPLAY_TIME_ZONE,
  }).format(date);
}

export function getExecutionPhaseLabel(statuses: ExecutionStatusEntry[]): string | null {
  const latest = statuses[statuses.length - 1];
  if (!latest) {
    return null;
  }

  switch (latest.status) {
    case "queued":
      return "В очереди";
    case "running":
      return "Выполняется SQL";
    case "streaming_results":
      return "Поток результатов";
    case "done":
      return "Выполнение завершено";
    case "failed":
      return "Выполнение не удалось";
    default:
      return latest.status;
  }
}

export function mergeToolCallsWithOptimizationTrace(args: {
  toolCalls?: ToolCallEntry[];
  payload?: ChatAskResponse;
  timestamp?: Date;
}): ToolCallEntry[] {
  const toolCalls = mergeToolCallsWithSemanticGrounding(args);
  const trace = args.payload?.optimization_trace;
  if (!trace?.optimizer_used) {
    return toolCalls;
  }

  const timestamp = args.timestamp?.toISOString();
  const summary = (() => {
    if (trace.applied) {
      return `Применена оптимизация запроса${trace.rules_triggered.length ? `: ${trace.rules_triggered.join(", ")}` : ""}`;
    }
    if (trace.findings.length > 0) {
      const findingWord =
        trace.findings.length === 1
          ? "замечание"
          : trace.findings.length < 5
            ? "замечания"
            : "замечаний";
      return `Только рекомендации: ${trace.findings.length} ${findingWord}`;
    }
    return "SQL проверен перед выполнением";
  })();
  const optimizerArguments = {
    applied: trace.applied,
    accepted_or_rejected: trace.accepted_or_rejected ?? null,
    risk_level: trace.risk_level ?? null,
    confidence: trace.confidence ?? null,
    rules_triggered: trace.rules_triggered,
    skills_used: trace.skills_used,
    evidence_used: trace.evidence_used,
    findings: trace.findings,
    recommended_actions: trace.recommended_actions,
    input_sql: trace.input_sql ?? null,
    optimized_sql_draft: trace.optimized_sql_draft ?? null,
  };
  const existingIndex = toolCalls.findIndex((call) => call.tool_name === "sql_optimization_advisor");
  if (existingIndex >= 0) {
    const mergedToolCalls = [...toolCalls];
    const existingCall = mergedToolCalls[existingIndex];
    mergedToolCalls[existingIndex] = {
      ...existingCall,
      arguments: {
        ...(existingCall?.arguments ?? {}),
        ...optimizerArguments,
      },
      result_summary: summary,
      success: existingCall?.success ?? true,
      completed_at: existingCall?.completed_at ?? timestamp,
    };
    return mergedToolCalls;
  }

  const optimizerToolCall: ToolCallEntry = {
    id: `tool-${args.timestamp?.getTime() ?? Date.now()}-optimizer`,
    tool_name: "sql_optimization_advisor",
    arguments: optimizerArguments,
    result_summary: summary,
    success: true,
    started_at: timestamp,
    completed_at: timestamp,
    sequence: nextSyntheticSequence(toolCalls),
  };

  return [...toolCalls, optimizerToolCall];
}
