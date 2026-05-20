import type {
  ChatAskResponse,
  ChatMessageViewModel,
  ExecuteSqlResponse,
} from "@/features/chat/chatViewModel";
import type {
  AnalysisCheckpointDigest,
  AnalysisPlan,
  AnalysisWorkerResult,
  ExecutionStatusEntry,
  ThinkingBlockEntry,
  ToolCallEntry,
} from "@/features/chat/streamingTypes";
import { parseAssistantContent } from "./assistantResponseParser";

export type ConversationMessage = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  generated_sql: string | null;
  capability_trace?: ChatAskResponse["capability_trace"];
  optimization_trace: ChatAskResponse["optimization_trace"];
  execution_id: string | null;
  context_tables: string[];
  metadata_sources: string[];
  claims: ChatAskResponse["claims"];
  evidence: ChatAskResponse["evidence"];
  claim_evidence_links: ChatAskResponse["claim_evidence_links"];
  uncertainty_flags: string[];
  selected_connector: string | null;
  analysis_document: ChatAskResponse["analysis_document"];
  visualization: ChatAskResponse["visualization"];
  analysis_plan: ChatAskResponse["analysis_plan"];
  worker_results: ChatAskResponse["worker_results"];
  checkpoint_digest: ChatAskResponse["checkpoint_digest"];
  warnings: string[];
  feedback: ChatMessageViewModel["feedback"];
  feedback_updated_at: string | null;
};

export type ConversationSummary = {
  id: string;
  workspace_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
};

export type ConversationDetail = {
  id: string;
  workspace_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  messages: ConversationMessage[];
};

type ExecutionHistory = {
  execution_id: string;
  status: string;
  engine: string;
  normalized_sql: string | null;
  row_count: number | null;
  duration_ms: number | null;
  warnings: string[];
  error_message: string | null;
};

type ExecutionDetail = {
  history: ExecutionHistory;
  result: ExecuteSqlResponse | null;
};

export type HydratedConversationThread = {
  detail: ConversationDetail;
  messages: ChatMessageViewModel[];
  /** Assistant messages with execution_ids - for lazy loading their execution details */
  assistantExecutionIds: Array<{ messageId: string; executionId: string }>;
};

export type AnalysisRunState = {
  conversation_id: string;
  status: AnalysisCheckpointDigest["status"] | string;
  checkpoint_digest: AnalysisCheckpointDigest | null;
  analysis_plan: AnalysisPlan | null;
  worker_results: AnalysisWorkerResult[];
  resumable: boolean;
  cancellable: boolean;
};

type CachedIntermediateSteps = {
  thinkingBlocks: ThinkingBlockEntry[];
  toolCalls: ToolCallEntry[];
  executionStatuses: ExecutionStatusEntry[];
};

const INTERMEDIATE_STEPS_CACHE_KEY = "rhetorica.chat.intermediateSteps.v1";
const MAX_CACHED_INTERMEDIATE_STEP_MESSAGES = 100;

function readIntermediateStepsCache(): Record<string, CachedIntermediateSteps> {
  if (typeof window === "undefined") {
    return {};
  }

  try {
    const raw = window.localStorage.getItem(INTERMEDIATE_STEPS_CACHE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, CachedIntermediateSteps>)
      : {};
  } catch {
    return {};
  }
}

function writeIntermediateStepsCache(
  cache: Record<string, CachedIntermediateSteps>
): void {
  if (typeof window === "undefined") {
    return;
  }

  const entries = Object.entries(cache).slice(-MAX_CACHED_INTERMEDIATE_STEP_MESSAGES);
  try {
    window.localStorage.setItem(
      INTERMEDIATE_STEPS_CACHE_KEY,
      JSON.stringify(Object.fromEntries(entries))
    );
  } catch {
    // Browser storage is best-effort; history still loads without the live system log.
  }
}

function getCachedIntermediateSteps(
  messageId: string
): CachedIntermediateSteps | null {
  return readIntermediateStepsCache()[messageId] ?? null;
}

export function cacheMessageIntermediateSteps(
  messageId: string | null | undefined,
  steps: CachedIntermediateSteps
): void {
  if (!messageId) {
    return;
  }

  if (
    steps.thinkingBlocks.length === 0 &&
    steps.toolCalls.length === 0 &&
    steps.executionStatuses.length === 0
  ) {
    return;
  }

  writeIntermediateStepsCache({
    ...readIntermediateStepsCache(),
    [messageId]: {
      thinkingBlocks: steps.thinkingBlocks,
      toolCalls: steps.toolCalls,
      executionStatuses: steps.executionStatuses,
    },
  });
}

export function conversationListQueryKey(workspaceId: string) {
  return ["chat", "conversations", workspaceId] as const;
}

export function deriveConversationTitle(question: string): string {
  const compact = question.replace(/\s+/g, " ").trim();
  if (!compact) {
    return "Untitled analysis";
  }

  return compact.length > 80 ? `${compact.slice(0, 77)}...` : compact;
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = "";
    try {
      const body = (await response.json()) as { body?: string; message?: string; detail?: string };
      detail = body.detail ?? body.message ?? body.body ?? "";
    } catch {
      detail = await response.text();
    }

    throw new Error(detail || `Request failed with status ${response.status}`);
  }

  return (await response.json()) as T;
}

export async function listConversations(
  workspaceId: string,
  limit = 24
): Promise<ConversationSummary[]> {
  const params = new URLSearchParams({
    workspace_id: workspaceId,
    limit: String(limit),
  });

  const response = await fetch(`/api/chat/conversations?${params.toString()}`, {
    cache: "no-store",
  });

  return parseJsonResponse<ConversationSummary[]>(response);
}

export async function createConversation(
  workspaceId: string,
  title: string
): Promise<ConversationSummary> {
  const response = await fetch("/api/chat/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workspace_id: workspaceId,
      title,
    }),
  });

  return parseJsonResponse<ConversationSummary>(response);
}

export async function getConversationDetail(
  conversationId: string
): Promise<ConversationDetail> {
  const response = await fetch(`/api/chat/conversations/${conversationId}`, {
    cache: "no-store",
  });

  return parseJsonResponse<ConversationDetail>(response);
}

export async function removeConversation(conversationId: string): Promise<void> {
  const response = await fetch(`/api/chat/conversations/${conversationId}`, {
    method: "DELETE",
  });

  await parseJsonResponse<{ status: string }>(response);
}

export async function getAnalysisRunState(conversationId: string): Promise<AnalysisRunState | null> {
  const response = await fetch(`/api/chat/conversations/${conversationId}/analysis-run`, {
    cache: "no-store",
  });

  if (response.status === 404) {
    return null;
  }

  return parseJsonResponse<AnalysisRunState>(response);
}

export async function resumeAnalysisRun(conversationId: string): Promise<AnalysisRunState> {
  const response = await fetch(`/api/chat/conversations/${conversationId}/analysis-run/resume`, {
    method: "POST",
    cache: "no-store",
  });
  const action = await parseJsonResponse<{
    conversation_id: string;
    status: AnalysisRunState["status"];
    checkpoint_digest: AnalysisCheckpointDigest | null;
  }>(response);
  return {
    conversation_id: action.conversation_id,
    status: action.status,
    checkpoint_digest: action.checkpoint_digest,
    analysis_plan: null,
    worker_results: [],
    resumable: false,
    cancellable: action.status === "running",
  };
}

export async function approveAnalysisRun(conversationId: string): Promise<AnalysisRunState> {
  const response = await fetch(`/api/chat/conversations/${conversationId}/analysis-run/approve`, {
    method: "POST",
    cache: "no-store",
  });
  const action = await parseJsonResponse<{
    conversation_id: string;
    status: AnalysisRunState["status"];
    checkpoint_digest: AnalysisCheckpointDigest | null;
  }>(response);
  return {
    conversation_id: action.conversation_id,
    status: action.status,
    checkpoint_digest: action.checkpoint_digest,
    analysis_plan: null,
    worker_results: [],
    resumable: action.status === "running",
    cancellable: action.status === "running",
  };
}

export async function cancelAnalysisRun(conversationId: string): Promise<AnalysisRunState> {
  const response = await fetch(`/api/chat/conversations/${conversationId}/analysis-run/cancel`, {
    method: "POST",
    cache: "no-store",
  });
  const action = await parseJsonResponse<{
    conversation_id: string;
    status: AnalysisRunState["status"];
    checkpoint_digest: AnalysisCheckpointDigest | null;
  }>(response);
  return {
    conversation_id: action.conversation_id,
    status: action.status,
    checkpoint_digest: action.checkpoint_digest,
    analysis_plan: null,
    worker_results: [],
    resumable: false,
    cancellable: false,
  };
}

/**
 * Fetches execution detail for a specific execution_id.
 * Returns null if execution is not found (404).
 */
export async function getExecutionDetail(executionId: string): Promise<ExecutionDetail | null> {
  const response = await fetch(`/api/sql/executions/${executionId}`, {
    cache: "no-store",
  });

  if (response.status === 404) {
    return null;
  }

  return parseJsonResponse<ExecutionDetail>(response);
}

function buildExecutionPayload(detail: ExecutionDetail | null): ExecuteSqlResponse | null {
  if (!detail) {
    return null;
  }

  if (detail.result) {
    return detail.result;
  }

  return {
    execution_id: detail.history.execution_id,
    status: detail.history.status as ExecuteSqlResponse["status"],
    engine: detail.history.engine as ExecuteSqlResponse["engine"],
    normalized_sql: detail.history.normalized_sql,
    row_count: detail.history.row_count,
    duration_ms: detail.history.duration_ms,
    error_message: detail.history.error_message,
    preview: {
      columns: [],
      rows: [],
    },
  };
}

/**
 * Lazy-loads execution detail for a specific message.
 * Returns execution payload or null if not available.
 * This is used to fetch old execution details on demand when user selects an old message.
 */
export async function lazyLoadExecution(executionId: string): Promise<ExecuteSqlResponse | null> {
  const detail = await getExecutionDetail(executionId);
  return buildExecutionPayload(detail);
}

function buildStoredAssistantPayload(
  message: ConversationMessage,
  answer: string,
  execution: ExecuteSqlResponse | null
): ChatAskResponse {
  return {
    answer,
    conversation_id: message.conversation_id,
    generated_sql: message.generated_sql ?? execution?.normalized_sql ?? null,
    capability_trace: message.capability_trace ?? null,
    optimization_trace: message.optimization_trace ?? null,
    selected_database_connector: message.selected_connector,
    assistant_message_id: message.id,
    metadata_sources: message.metadata_sources,
    context_tables: message.context_tables,
    claims: message.claims,
    evidence: message.evidence,
    claim_evidence_links: message.claim_evidence_links,
    uncertainty_flags: message.uncertainty_flags,
    execution,
    analysis_document: message.analysis_document,
    visualization: message.visualization,
    analysis_plan: message.analysis_plan ?? message.capability_trace?.runtime_context?.analysis_plan ?? null,
    worker_results: message.worker_results ?? message.capability_trace?.runtime_context?.worker_results ?? [],
    checkpoint_digest: message.checkpoint_digest ?? message.capability_trace?.runtime_context?.checkpoint_digest ?? null,
    warnings: message.warnings,
    cancelled: false,
  };
}

function buildStoredThinkingBlocks(
  message: ConversationMessage,
  thinkingBlocks: string[]
): ThinkingBlockEntry[] {
  return thinkingBlocks.map((content, index) => ({
    id: `thinking-${message.id}-${index}`,
    content,
    timestamp: message.created_at,
    sequence: index,
  }));
}

function mapConversationToMessages(
  detail: ConversationDetail,
  executionByMessageId: Map<string, ExecuteSqlResponse | null>
): ChatMessageViewModel[] {
  return detail.messages.map((message) => {
    const isAssistant = message.role === "assistant";
    const parsedContent = isAssistant
      ? parseAssistantContent(message.content)
      : { answer: message.content, thinkingBlocks: [] };
    const text =
      parsedContent.thinkingBlocks.length > 0
        ? parsedContent.answer.trim() || message.content
        : parsedContent.answer || message.content;
    const payload = isAssistant
      ? buildStoredAssistantPayload(
          message,
          text,
          executionByMessageId.get(message.id) ?? null
        )
      : undefined;
    const storedThinkingBlocks = isAssistant
      ? buildStoredThinkingBlocks(message, parsedContent.thinkingBlocks)
      : [];
    const cachedIntermediateSteps = isAssistant
      ? getCachedIntermediateSteps(message.id)
      : null;

    return {
      id: message.id,
      backendMessageId: isAssistant ? message.id : undefined,
      role: message.role,
      text,
      payload,
      feedback: message.feedback,
      timestamp: new Date(message.created_at),
      thinkingBlocks:
        cachedIntermediateSteps?.thinkingBlocks.length
          ? cachedIntermediateSteps.thinkingBlocks
          : isAssistant
            ? storedThinkingBlocks
            : undefined,
      toolCalls: cachedIntermediateSteps?.toolCalls,
      executionStatuses: cachedIntermediateSteps?.executionStatuses,
      deliveryState: "complete",
    };
  });
}

export function mergeHydratedMessages(
  currentMessages: ChatMessageViewModel[],
  hydratedMessages: ChatMessageViewModel[]
): ChatMessageViewModel[] {
  const currentByBackendMessageId = new Map(
    currentMessages
      .filter((message) => message.backendMessageId)
      .map((message) => [message.backendMessageId as string, message])
  );

  return hydratedMessages.map((message) => {
    const persistedId = message.backendMessageId;
    if (!persistedId) {
      return message;
    }

    const current = currentByBackendMessageId.get(persistedId);
    if (!current) {
      return message;
    }

    // Merge payload: use current's answer/partial data, but hydrate with execution from DB
    const mergedPayload = current.payload && message.payload
      ? {
          ...message.payload, // Hydrated data first (execution, generated_sql, etc.)
          ...current.payload, // Override with current's real-time data
          // Preserve execution from hydrated if current doesn't have it
          execution: current.payload.execution ?? message.payload.execution,
          generated_sql: current.payload.generated_sql ?? message.payload.generated_sql,
        }
      : current.payload ?? message.payload;

    return {
      ...message,
      id: current.id,
      payload: mergedPayload,
      thinkingBlocks:
        current.thinkingBlocks && current.thinkingBlocks.length > 0
          ? current.thinkingBlocks
          : message.thinkingBlocks,
      toolCalls:
        current.toolCalls && current.toolCalls.length > 0
          ? current.toolCalls
          : message.toolCalls,
      executionStatuses:
        current.executionStatuses && current.executionStatuses.length > 0
          ? current.executionStatuses
          : message.executionStatuses,
      deliveryState: current.deliveryState === "complete" ? current.deliveryState : message.deliveryState,
    };
  });
}

export async function loadConversationThread(
  conversationId: string
): Promise<HydratedConversationThread> {
  const detail = await getConversationDetail(conversationId);
  const assistantMessages = detail.messages.filter(
    (message): message is ConversationMessage & { execution_id: string } =>
      message.role === "assistant" && typeof message.execution_id === "string" && message.execution_id.length > 0
  );

  // Collect all assistant execution_ids for lazy loading
  const assistantExecutionIds = assistantMessages.map((message) => ({
    messageId: message.id,
    executionId: message.execution_id,
  }));

  // Only load the latest execution on initial load to avoid N requests
  const latestExecutionCarrier = assistantMessages.at(-1);
  const latestExecutionDetail = latestExecutionCarrier
    ? await getExecutionDetail(latestExecutionCarrier.execution_id)
    : null;
  const executionByMessageId = new Map<string, ExecuteSqlResponse | null>();

  if (latestExecutionCarrier) {
    executionByMessageId.set(
      latestExecutionCarrier.id,
      buildExecutionPayload(latestExecutionDetail)
    );
  }

  return {
    detail,
    messages: mapConversationToMessages(detail, executionByMessageId),
    assistantExecutionIds,
  };
}
