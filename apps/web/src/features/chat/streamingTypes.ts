import type { AnalysisDocument } from "@/features/analysis/types";
import type { VisualizationSpec } from "@/features/charts";

export type StreamEventType =
  | "thinking"
  | "tool_call_start"
  | "tool_call_end"
  | "execution_status"
  | "runtime_state"
  | "chunk"
  | "done"
  | "error";

export type ExecutionStatusValue =
  | "queued"
  | "running"
  | "streaming_results"
  | "done"
  | "failed";

export type ClaimVerificationStatus = "verified" | "unverified";
export type EvidenceType =
  | "execution_result"
  | "metadata_entity"
  | "rag_insight"
  | "confluence_page";

export interface AnswerClaim {
  claim_id: string;
  text: string;
  status: ClaimVerificationStatus;
}

export interface AnswerEvidence {
  evidence_id: string;
  type: EvidenceType;
  reference: string;
  title?: string | null;
  snippet?: string | null;
  status: ClaimVerificationStatus;
}

export interface ClaimEvidenceLink {
  claim_id: string;
  evidence_ids: string[];
}

export interface QueryPreview {
  columns: string[];
  rows: Array<Array<string | number | boolean | null>>;
}

export interface ExecuteSqlResponse {
  execution_id: string;
  status: string;
  engine: string;
  normalized_sql: string | null;
  row_count: number | null;
  duration_ms: number | null;
  error_message: string | null;
  preview: QueryPreview;
}

export interface OptimizationCitation {
  kind: string;
  label: string;
  reference: string;
}

export interface OptimizationFinding {
  rule_id: string;
  title: string;
  summary: string;
  severity: string;
  applies_to: string[];
  citations: OptimizationCitation[];
}

export interface OptimizationAction {
  action_id: string;
  kind: string;
  title: string;
  summary: string;
  sql?: string | null;
  applied: boolean;
  citations: OptimizationCitation[];
}

export interface OptimizationTrace {
  optimizer_used: boolean;
  optimizer_provider?: string | null;
  input_sql?: string | null;
  optimized_sql_draft?: string | null;
  input_sql_hash?: string | null;
  optimized_sql_hash?: string | null;
  accepted_or_rejected?: string | null;
  applied: boolean;
  risk_level?: string | null;
  confidence?: number | null;
  engine_scope?: string | null;
  rules_triggered: string[];
  findings: OptimizationFinding[];
  recommended_actions: OptimizationAction[];
  citations: OptimizationCitation[];
  warnings: string[];
  skills_used: string[];
  evidence_used: string[];
}

export interface SemanticGroundingCandidate {
  fqn: string;
  service?: string | null;
  database?: string | null;
  schema?: string | null;
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
    last_status?: string | null;
    last_run_at?: number | null;
    failed_tests: string[];
  } | null;
  usage_count: number;
  suspicious: boolean;
}

export interface SemanticGrounding {
  chosen_table_fqn?: string | null;
  candidates: SemanticGroundingCandidate[];
  selection_rationale?: string | null;
  lineage_summary?: string | null;
}

export interface RuntimeContextTrace {
  metadata_table_columns: Record<string, string[]>;
  semantic_grounding?: SemanticGrounding | null;
  analysis_plan?: AnalysisPlan | null;
  worker_results?: AnalysisWorkerResult[];
  checkpoint_digest?: AnalysisCheckpointDigest | null;
}

export interface CapabilityTrace {
  available_tools: string[];
  used_tools: string[];
  used_providers: string[];
  evidence_origin: string[];
  degraded_capabilities: string[];
  policy_decisions: string[];
  runtime_context?: RuntimeContextTrace | null;
}

interface BaseStreamEvent {
  type: StreamEventType;
  timestamp: string;
}

export interface ThinkingEvent extends BaseStreamEvent {
  type: "thinking";
  content: string;
}

export interface ToolCallStartEvent extends BaseStreamEvent {
  type: "tool_call_start";
  tool_name: string;
  tool_call_id?: string;
  arguments?: Record<string, unknown>;
}

export interface ToolCallEndEvent extends BaseStreamEvent {
  type: "tool_call_end";
  tool_name: string;
  tool_call_id?: string;
  result_summary: string;
  success: boolean;
}

export interface ExecutionStatusEvent extends BaseStreamEvent {
  type: "execution_status";
  status: ExecutionStatusValue;
  message?: string;
}

export interface ChunkEvent extends BaseStreamEvent {
  type: "chunk";
  content: string;
}

export interface RuntimeStateEvent extends BaseStreamEvent {
  type: "runtime_state";
  analysis_plan?: AnalysisPlan | null;
  worker_results?: AnalysisWorkerResult[];
  checkpoint_digest?: AnalysisCheckpointDigest | null;
}

// Clarification types - matches backend contracts
export type ClarificationChoiceType =
  | "choose_connector"
  | "choose_table"
  | "choose_metric_definition"
  | "choose_time_range"
  | "confirm_assumption"
  | "approve_expensive_query";

export interface ClarificationChoice {
  id: string;
  label: string;
  reason: string;
  provenance_id?: string | null;
}

export interface ClarificationRequest {
  clarification_id: string;
  question: string;
  type: ClarificationChoiceType;
  choices: ClarificationChoice[];
}

export interface DoneEvent extends BaseStreamEvent {
  type: "done";
  conversation_id?: string;
  answer: string;
  generated_sql?: string;
  execution?: ExecuteSqlResponse | null;
  analysis_document?: AnalysisDocument | null;
  visualization?: VisualizationSpec | null;
  analysis_plan?: AnalysisPlan | null;
  worker_results?: AnalysisWorkerResult[];
  checkpoint_digest?: AnalysisCheckpointDigest | null;
  selected_database_connector?: string;
  assistant_message_id?: string;
  metadata_sources: string[];
  context_tables: string[];
  claims: AnswerClaim[];
  evidence: AnswerEvidence[];
  claim_evidence_links: ClaimEvidenceLink[];
  uncertainty_flags: string[];
  capability_trace?: CapabilityTrace | null;
  optimization_trace?: OptimizationTrace;
  warnings: string[];
  cancelled?: boolean;
  clarification_request?: ClarificationRequest | null;
}

export type AnalysisPlanStepStatus =
  | "planned"
  | "running"
  | "done"
  | "blocked"
  | "failed"
  | "cancelled";

export interface AnalysisPlanStep {
  id: string;
  title: string;
  status: AnalysisPlanStepStatus;
  required_evidence: string[];
  evidence_ids: string[];
  blocker?: string | null;
}

export interface AnalysisPlan {
  plan_id?: string;
  goal: string;
  status: "planned" | "running" | "blocked" | "completed" | "failed" | "cancelled";
  steps: AnalysisPlanStep[];
  required_evidence: string[];
  open_questions: string[];
  warnings: string[];
}

export interface AnalysisWorkerResult {
  task_id: string;
  goal: string;
  status: "succeeded" | "failed" | "blocked" | "cancelled";
  key_finding?: string | null;
  evidence_ids: string[];
  blocker?: string | null;
  next_action?: string | null;
  warnings: string[];
}

export interface AnalysisCheckpointDigest {
  run_id: string;
  plan_id?: string | null;
  status: "planned" | "running" | "blocked" | "awaiting_user" | "completed" | "failed" | "cancelled";
  active_step_id?: string | null;
  completed_step_ids: string[];
  blocked_step_id?: string | null;
  budget_state?: {
    pending_approval?: {
      kind?: string;
      tool_name?: string;
      approval_token?: string;
      step_id?: string;
      connector_name?: string;
      sql?: string;
      reason?: string;
    };
    approved_approval_token?: string;
    consumed_approval_token?: string;
    [key: string]: unknown;
  };
  evidence_ids: string[];
  blocker?: string | null;
  next_action?: string | null;
  warnings: string[];
}

export interface ErrorEvent extends BaseStreamEvent {
  type: "error";
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export type StreamEvent =
  | ThinkingEvent
  | ToolCallStartEvent
  | ToolCallEndEvent
  | ExecutionStatusEvent
  | RuntimeStateEvent
  | ChunkEvent
  | DoneEvent
  | ErrorEvent;

export interface ThinkingBlockEntry {
  id: string;
  content: string;
  timestamp: string;
  sequence?: number;
}

export interface ToolCallEntry {
  id: string;
  tool_name: string;
  arguments?: Record<string, unknown>;
  result_summary?: string;
  success?: boolean;
  started_at?: string;
  completed_at?: string;
  sequence?: number;
}

export interface ExecutionStatusEntry {
  status: ExecutionStatusValue;
  message?: string;
  timestamp: string;
  sequence?: number;
}

export interface IntermediateSteps {
  thinkingBlocks: ThinkingBlockEntry[];
  toolCalls: ToolCallEntry[];
  executionStatuses: ExecutionStatusEntry[];
}

export interface StreamingMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  thinkingBlocks: ThinkingBlockEntry[];
  toolCalls: ToolCallEntry[];
  executionStatuses: ExecutionStatusEntry[];
  payload?: DoneEvent;
  analysisPlan?: AnalysisPlan | null;
  checkpointDigest?: AnalysisCheckpointDigest | null;
  workerResults?: AnalysisWorkerResult[];
  timestamp: Date;
  isStreaming: boolean;
  isComplete: boolean;
  isError: boolean;
}
