"use client";

import { lazy, Suspense, useState } from "react";
import { motion } from "framer-motion";
import { Avatar, Badge, Button, Tooltip, useToast } from "@rhetorica/ui";
import { AlertTriangle, Bot, Check, ChevronDown, Circle, CircleOff, Copy, Download, FileCode2, FlaskConical, LoaderCircle, User } from "lucide-react";
import { cn } from "@rhetorica/ui";
import { CollapsibleThinking } from "./streaming-components";
import { MarkdownMessage } from "./markdown-message";
import {
  getExecutionPhaseLabel,
  type ChatAskResponse,
  type ChatDeliveryState,
  type FeedbackReason,
} from "@/features/chat/chatViewModel";
import { copyTextToClipboard } from "@/features/chat/chatCopy";
import { downloadAnalysisExport, type AnalysisExportFormat } from "@/features/chat/exportApi";
import { AnalysisReport } from "./analysis-report";
import { ClaimEvidenceSection } from "./claim-evidence-section";
import { ClarificationCard } from "./clarification";
import type {
  ExecutionStatusEntry,
  ThinkingBlockEntry,
  ToolCallEntry,
} from "@/features/chat/useChatStreaming";
import type { ClarificationRequest } from "./clarification/clarificationTypes";
import type {
  AnalysisCheckpointDigest,
  AnalysisPlan,
} from "@/features/chat/streamingTypes";

const LazyVisualizationChart = lazy(async () => ({
  default: (await import("@/features/charts")).VisualizationChart,
}));

interface ChatMessageProps {
  id?: string;
  role: "user" | "assistant";
  userLabel?: string;
  text: string;
  timestamp?: Date;
  className?: string;
  thinkingBlocks?: ThinkingBlockEntry[];
  toolCalls?: ToolCallEntry[];
  executionStatuses?: ExecutionStatusEntry[];
  isStreaming?: boolean;
  isComplete?: boolean;
  feedback?: "useful" | "wrong" | "unsafe" | "incomplete" | null;
  feedbackReason?: FeedbackReason | null;
  feedbackComment?: string | null;
  feedbackLoading?: boolean;
  deliveryState?: ChatDeliveryState;
  analysisPlan?: AnalysisPlan | null;
  checkpointDigest?: AnalysisCheckpointDigest | null;
  payload?: ChatAskResponse;
  clarificationRequest?: ClarificationRequest | null;
  onApproveAnalysis?: () => void;
  onResumeAnalysis?: () => void;
  onCancelAnalysis?: () => void;
  onSelectClarification?: (clarificationId: string, choiceId: string, resolvedValue: string) => void;
  copyText: string;
  onFeedback?: (
    messageId: string,
    feedback: "useful" | "wrong" | "unsafe" | "incomplete",
    reason?: FeedbackReason | null,
    comment?: string | null
  ) => void;
  onRepairTurn?: (messageId: string) => void;
  /** Called when user clicks "Inspect result" on an assistant message. Passes execution_id if available */
  onInspectResult?: (executionId?: string | null) => void;
}

export function ChatMessageComponent({
  id,
  role,
  userLabel,
  text,
  timestamp,
  className,
  thinkingBlocks = [],
  toolCalls = [],
  executionStatuses = [],
  isStreaming = false,
  isComplete = false,
  feedback = null,
  feedbackReason = null,
  feedbackComment = null,
  feedbackLoading = false,
  deliveryState = "idle",
  analysisPlan = null,
  checkpointDigest = null,
  payload,
  clarificationRequest,
  onApproveAnalysis,
  onResumeAnalysis,
  onCancelAnalysis,
  onSelectClarification,
  copyText,
  onFeedback,
  onRepairTurn,
  onInspectResult,
}: ChatMessageProps) {
  const { addToast } = useToast();
  const [copied, setCopied] = useState(false);
  const [exportingFormat, setExportingFormat] = useState<AnalysisExportFormat | null>(null);
  const [isExportMenuOpen, setIsExportMenuOpen] = useState(false);
  const [showFeedbackDetails, setShowFeedbackDetails] = useState(false);
  const [selectedReason, setSelectedReason] = useState<FeedbackReason | null>(feedbackReason);
  const [feedbackCommentText, setFeedbackCommentText] = useState<string>(feedbackComment || "");
  const isUser = role === "user";
  const resolvedUserLabel = userLabel?.trim() || "Analyst";
  const hasIntermediateSteps = thinkingBlocks.length > 0 || toolCalls.length > 0 || executionStatuses.length > 0;
  const analysisDocument = payload?.analysis_document ?? null;
  const hasAnalysisReport = !isUser && Boolean(analysisDocument?.blocks.length);
  const standaloneVisualization =
    !isUser && !hasAnalysisReport && payload?.visualization && payload?.execution?.preview
      ? payload.visualization
      : null;
  const rowCount = payload?.execution?.row_count ?? payload?.execution?.preview?.rows?.length ?? null;
  const durationLabel = payload?.execution?.duration_ms ? `${payload.execution.duration_ms} ms` : null;
  const exportConversationId = payload?.conversation_id;
  const exportMessageId = payload?.assistant_message_id;
  const hasExportableReport = Boolean(analysisDocument || payload?.visualization);
  const canExportReport = !isUser && Boolean(hasExportableReport && exportConversationId && exportMessageId);
  const generatedSql = payload?.generated_sql?.trim() || null;
  const executedSql = payload?.execution?.normalized_sql?.trim() || null;
  const hasSqlSnapshot = !isUser && Boolean(generatedSql || executedSql);
  // Show inspect button for assistant messages with execution data or stored SQL
  const canInspect = !isUser && Boolean(
    payload?.execution ||
    generatedSql ||
    (payload?.claims && payload.claims.length > 0) ||
    (payload?.evidence && payload.evidence.length > 0)
  );
  const feedbackOptions: Array<{
    value: "useful" | "wrong" | "unsafe" | "incomplete";
    label: string;
  }> = [
    { value: "useful", label: "Useful" },
    { value: "wrong", label: "Wrong" },
    { value: "unsafe", label: "Unsafe" },
    { value: "incomplete", label: "Incomplete" },
  ];

  const feedbackReasonOptions: Array<{
    value: FeedbackReason;
    label: string;
    description: string;
  }> = [
    { value: "wrong_source", label: "Wrong Source", description: "Wrong table or data source" },
    { value: "wrong_metric", label: "Wrong Metric", description: "Incorrect calculation logic" },
    { value: "unsafe_sql", label: "Unsafe SQL", description: "SQL is potentially unsafe" },
    { value: "incomplete", label: "Incomplete", description: "Missing information or scope" },
    { value: "bad_chart", label: "Bad Chart", description: "Visualization is wrong" },
    { value: "other", label: "Other", description: "Other issue" },
  ];

  const selectedFeedbackLabel = feedbackOptions.find((item) => item.value === feedback)?.label;
  const canShowReasonSelector = feedback === "wrong" || feedback === "incomplete";
  const canShowRepairButton = feedback !== null && feedback !== "useful";
  const executionPhase = getExecutionPhaseLabel(executionStatuses);
  const deliveryBadge = (() => {
    if (isUser) {
      return null;
    }
    if (deliveryState === "streaming") {
      return {
        variant: "primary" as const,
        label: executionPhase ?? "Streaming answer",
        icon: LoaderCircle,
      };
    }
    if (deliveryState === "complete") {
      return {
        variant: "success" as const,
        label: executionPhase ?? "Answer ready",
        icon: Check,
      };
    }
    if (deliveryState === "cancelled") {
      return {
        variant: "warning" as const,
        label: "Generation cancelled",
        icon: CircleOff,
      };
    }
    if (deliveryState === "error") {
      return {
        variant: "danger" as const,
        label: executionPhase ?? "Answer failed",
        icon: AlertTriangle,
      };
    }
    return null;
  })();
  const DeliveryIcon = deliveryBadge?.icon;

  const handleCopy = async () => {
    try {
      await copyTextToClipboard(copyText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      addToast({
        type: "error",
        title: "Copy failed",
        description: error instanceof Error ? error.message : "Message could not be copied.",
      });
    }
  };

  const handleExport = async (format: AnalysisExportFormat) => {
    if (!exportConversationId || !exportMessageId) {
      addToast({
        type: "error",
        title: "Export unavailable",
        description: "This answer does not have a persisted snapshot yet.",
      });
      return;
    }

    setExportingFormat(format);
    setIsExportMenuOpen(false);
    try {
      await downloadAnalysisExport({
        conversationId: exportConversationId,
        messageId: exportMessageId,
        format,
      });
      addToast({
        type: "success",
        title: "Export ready",
        description: `${format.toUpperCase()} snapshot downloaded.`,
      });
    } catch (error) {
      addToast({
        type: "error",
        title: "Export failed",
        description: error instanceof Error ? error.message : "Unknown export failure.",
      });
    } finally {
      setExportingFormat(null);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
      className={cn(
        "flex gap-3 rounded-[24px] border p-4 shadow-[0_20px_60px_rgba(0,0,0,0.16)]",
        isUser
          ? "border-[#36598a] bg-[linear-gradient(180deg,rgba(17,29,49,0.9),rgba(12,20,34,0.94))]"
          : "border-[#1f2a3f] bg-[linear-gradient(180deg,rgba(11,17,30,0.88),rgba(9,14,25,0.94))]",
        deliveryState === "streaming" ? "ring-2 ring-[#5da8ff]/30 ring-opacity-50" : "",
        className
      )}
    >
      <Avatar
        fallback={isUser ? "U" : "AI"}
        size="md"
        className={isUser ? "bg-[#5da8ff]/20" : "bg-[#38c67d]/20"}
      >
        {isUser ? (
          <User className="h-5 w-5 text-[#5da8ff]" />
        ) : (
          <Bot className="h-5 w-5 text-[#38c67d]" />
        )}
      </Avatar>
      <div className="min-w-0 flex-1">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-[0.18em] text-[#a9b8d4]">
            {isUser ? resolvedUserLabel : "Rhetorica Assistant"}
          </span>
          {timestamp ? (
            <span className="text-xs text-[#7484a3]">
              {timestamp.toLocaleTimeString("en-GB", {
                hour: "2-digit",
                minute: "2-digit",
                timeZone: "UTC",
              })}
            </span>
          ) : null}
          {deliveryBadge && DeliveryIcon ? (
            <Badge
              variant={deliveryBadge.variant}
              className={cn(
                "ml-auto gap-1.5",
                deliveryState === "streaming" ? "animate-pulse" : ""
              )}
            >
              <DeliveryIcon
                className={cn("h-3.5 w-3.5", deliveryState === "streaming" ? "animate-spin" : "")}
              />
              {deliveryBadge.label}
            </Badge>
          ) : null}
        </div>

        {/* Intermediate steps (thinking, tool calls, execution) */}
        {hasIntermediateSteps && (
          <CollapsibleThinking
            thinkingBlocks={thinkingBlocks}
            toolCalls={toolCalls}
            executionStatuses={executionStatuses}
            defaultExpanded={false}
            isStreaming={isStreaming && !isComplete}
          />
        )}

        {!isUser && analysisPlan ? (
          <AnalysisPlanBlock
            plan={analysisPlan}
            checkpoint={checkpointDigest}
            onApprove={onApproveAnalysis}
            onResume={onResumeAnalysis}
            onCancel={onCancelAnalysis}
          />
        ) : null}

        {hasAnalysisReport && analysisDocument ? (
          <AnalysisReport
            document={analysisDocument}
            fallbackPreview={payload?.execution?.preview ?? null}
            fallbackVisualization={payload?.visualization ?? null}
            meta={{
              connectorLabel: payload?.selected_database_connector,
              sourcesLabel: payload?.metadata_sources.length ? payload.metadata_sources.join(", ") : null,
              rowCount,
              durationLabel,
              resultStatus: payload?.execution?.status ?? null,
            }}
          />
        ) : null}

        {standaloneVisualization ? (
          <Suspense
            fallback={
              <div className="min-h-[320px] rounded-xl border border-[#1d2c46] bg-[#09111e]/78" />
            }
          >
            <LazyVisualizationChart
              spec={standaloneVisualization}
              preview={payload?.execution?.preview ?? null}
            />
          </Suspense>
        ) : null}

        {/* Clarification request - shows instead of regular answer */}
        {!isUser && clarificationRequest && onSelectClarification ? (
          <ClarificationCard
            clarification={clarificationRequest}
            onSelect={(choiceId, resolvedValue) =>
              onSelectClarification(clarificationRequest.clarification_id, choiceId, resolvedValue)
            }
            disabled={deliveryState === "streaming"}
          />
        ) : null}

        {/* Final answer - hidden when clarification is pending */}
        {isUser ? (
          <p className="whitespace-pre-wrap text-sm leading-7 text-[#edf3ff]">{text}</p>
        ) : hasAnalysisReport ? (
          null
        ) : clarificationRequest ? (
          null
        ) : (
          <MarkdownMessage text={text} />
        )}

        {/* Claims with evidence - hidden when clarification is pending */}
        {!isUser && !clarificationRequest && payload?.claims && payload.claims.length > 0 ? (
          <ClaimEvidenceSection
            claims={payload.claims}
            evidence={payload.evidence ?? []}
            claimEvidenceLinks={payload.claim_evidence_links ?? []}
          />
        ) : null}

        {hasSqlSnapshot && !clarificationRequest ? (
          <SqlSnapshotBlock
            generatedSql={generatedSql}
            executedSql={executedSql}
            engine={payload?.execution?.engine ?? "SQL"}
          />
        ) : null}

        {!isUser && deliveryState === "complete" && id && onFeedback && !clarificationRequest ? (
          <div className="mt-3 flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              {feedbackOptions.map((item) => (
                <Button
                  key={item.value}
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    onFeedback(id, item.value);
                    // Show reason selector for negative feedback
                    if (item.value === "wrong" || item.value === "incomplete") {
                      setShowFeedbackDetails(true);
                    } else {
                      setShowFeedbackDetails(false);
                    }
                  }}
                  className={cn(
                    "h-7 px-2.5 transition-all",
                    feedback === item.value
                      ? "bg-[#5da8ff]/30 text-white border border-[#5da8ff] shadow-[0_0_0_1px_rgba(93,168,255,0.45)]"
                      : "text-[#8ea1c3] border border-[#2e3d59]/50"
                  )}
                >
                  {feedback === item.value ? <Check className="h-3.5 w-3.5" /> : null}
                  {item.label}
                </Button>
              ))}
              <span className="text-xs text-[#8ea1c3]">
                {feedbackLoading ? "Saving..." : selectedFeedbackLabel ? `Selected: ${selectedFeedbackLabel}` : "Choose reaction"}
              </span>
            </div>

            {/* Reason selector and comment field for negative feedback */}
            {showFeedbackDetails && canShowReasonSelector ? (
              <div className="flex flex-col gap-2 rounded-lg border border-[#2e3d59]/50 bg-[#0f1828] p-3">
                <div className="flex flex-wrap gap-2">
                  {feedbackReasonOptions.map((option) => (
                    <Button
                      key={option.value}
                      type="button"
                      size="sm"
                      variant={selectedReason === option.value ? "secondary" : "ghost"}
                      onClick={() => setSelectedReason(option.value)}
                      className={cn(
                        "h-6 text-xs",
                        selectedReason === option.value
                          ? "bg-[#5da8ff]/20 text-white border border-[#5da8ff]"
                          : "text-[#8ea1c3] border border-[#2e3d59]/50 hover:bg-[#132033]"
                      )}
                    >
                      {option.label}
                    </Button>
                  ))}
                </div>
                <textarea
                  className="min-h-[60px] w-full resize-none rounded-lg border border-[#2e3d59]/50 bg-[#07101d]/62 px-3 py-2 text-xs text-[#d8e4f8] placeholder:text-[#7484a3] focus:border-[#5da8ff] focus:outline-none"
                  placeholder="Add a comment (optional)..."
                  value={feedbackCommentText}
                  onChange={(e) => setFeedbackCommentText(e.target.value)}
                />
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      onFeedback(id, feedback!, selectedReason, feedbackCommentText);
                      setShowFeedbackDetails(false);
                    }}
                    className="bg-[#5da8ff]/20 text-white hover:bg-[#5da8ff]/30"
                  >
                    Save
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => setShowFeedbackDetails(false)}
                    className="text-[#8ea1c3]"
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            ) : null}

            {/* Fix answer button for negative feedback */}
            {canShowRepairButton && onRepairTurn ? (
              <div className="mt-2">
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  onClick={() => onRepairTurn(id)}
                  className="gap-2 bg-[#f1b45f]/20 text-[#f1b45f] hover:bg-[#f1b45f]/30 border border-[#f1b45f]/30"
                >
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  Fix Answer
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="mt-3 flex flex-wrap justify-end gap-2">
          {canExportReport ? (
            <div className="relative inline-flex">
              <Tooltip content="Export report" side="top">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={exportingFormat !== null}
                  aria-expanded={isExportMenuOpen}
                  aria-haspopup="menu"
                  aria-label="Export report"
                  onClick={() => {
                    setIsExportMenuOpen((current) => !current);
                  }}
                  className="gap-2 border border-[#2e3d59]/60 px-3 text-[#9db1d3] hover:bg-[#132033] hover:text-white"
                >
                  <Download className="h-4 w-4" />
                  {exportingFormat ? `Exporting ${exportingFormat.toUpperCase()}` : "Export"}
                  <ChevronDown className="h-4 w-4" />
                </Button>
              </Tooltip>
              {isExportMenuOpen ? (
                <div
                  role="menu"
                  aria-label="Export report formats"
                  className="absolute right-0 top-full z-20 mt-2 min-w-36 overflow-hidden rounded-2xl border border-[#2e3d59] bg-[#0b1423] p-1 shadow-2xl shadow-black/35"
                >
                  {(["html", "pdf", "ipynb"] as AnalysisExportFormat[]).map((format) => (
                    <button
                      key={format}
                      type="button"
                      role="menuitem"
                      className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-xs font-semibold uppercase text-[#d5e2fb] transition hover:bg-[#162238] hover:text-[#edf3ff]"
                      onClick={() => {
                        void handleExport(format);
                      }}
                    >
                      <Download className="h-4 w-4" />
                      {format.toUpperCase()}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
          {canInspect && onInspectResult ? (
            <Tooltip content="Inspect result" side="top">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label="Inspect result"
                onClick={() => {
                  onInspectResult(payload?.execution?.execution_id ?? null);
                }}
                className="gap-2 border border-[#2e3d59]/60 px-3 text-[#9db1d3] hover:bg-[#132033] hover:text-white"
              >
                <FlaskConical className="h-4 w-4" />
                Inspect
              </Button>
            </Tooltip>
          ) : null}
          <Tooltip content={copied ? "Copied" : "Copy message"} side="top">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label="Copy message"
              onClick={() => {
                void handleCopy();
              }}
              className="gap-2 border border-[#2e3d59]/60 px-3 text-[#9db1d3] hover:bg-[#132033] hover:text-white"
            >
              {copied ? (
                <Check className="h-4 w-4 text-[#38c67d]" />
              ) : (
                <Copy className="h-4 w-4" />
              )}
              {copied ? "Copied" : "Copy"}
            </Button>
          </Tooltip>
        </div>
      </div>
    </motion.div>
  );
}

function SqlSnapshotBlock({
  generatedSql,
  executedSql,
  engine,
}: {
  generatedSql: string | null;
  executedSql: string | null;
  engine: string;
}) {
  const hasDistinctExecutedSql = Boolean(executedSql && executedSql !== generatedSql);
  const primarySql = executedSql ?? generatedSql;
  if (!primarySql) {
    return null;
  }

  return (
    <details className="mt-3 rounded-xl border border-[#22314d] bg-[#07101d]/62">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs font-semibold uppercase tracking-[0.12em] text-[#9db1d3] marker:hidden">
        <FileCode2 className="h-4 w-4 text-[#89b7ff]" />
        SQL
        <span className="ml-auto rounded-full border border-[#2d4268] px-2 py-0.5 text-[11px] normal-case tracking-normal text-[#8fa5c8]">
          {engine}
        </span>
      </summary>
      <div className="border-t border-[#1b2740] p-3">
        <SqlCodeBlock title={hasDistinctExecutedSql ? "Executed SQL" : "SQL"} sql={primarySql} />
        {hasDistinctExecutedSql && generatedSql ? (
          <div className="mt-3">
            <SqlCodeBlock title="Original SQL draft" sql={generatedSql} />
          </div>
        ) : null}
      </div>
    </details>
  );
}

function SqlCodeBlock({ title, sql }: { title: string; sql: string }) {
  return (
    <section>
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#7f93b4]">
        {title}
      </p>
      <pre className="max-h-[360px] overflow-auto rounded-lg border border-[#1b2740] bg-[#050b14] p-3 text-xs leading-6 text-[#dbe8ff]">
        <code>{sql}</code>
      </pre>
    </section>
  );
}

function AnalysisPlanBlock({
  plan,
  checkpoint,
  onApprove,
  onResume,
  onCancel,
}: {
  plan: AnalysisPlan;
  checkpoint?: AnalysisCheckpointDigest | null;
  onApprove?: () => void;
  onResume?: () => void;
  onCancel?: () => void;
}) {
  const statusLabel = checkpoint?.status ?? plan.status;
  const pendingApproval = checkpoint?.budget_state?.pending_approval;
  const canApprove = statusLabel === "awaiting_user" && Boolean(pendingApproval);
  const canResume = !canApprove && ["blocked", "awaiting_user", "failed"].includes(statusLabel);
  const canCancel = ["planned", "running", "blocked", "awaiting_user"].includes(statusLabel);
  return (
    <section className="mb-3 rounded-lg border border-[#2e3d59] bg-[#0f1828] p-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-[0.16em] text-[#a9b8d4]">
          Plan
        </h3>
        <div className="flex shrink-0 items-center gap-2">
          {canApprove && onApprove ? (
            <Button size="sm" variant="secondary" onClick={onApprove}>
              Approve
            </Button>
          ) : null}
          {canResume && onResume ? (
            <Button size="sm" variant="secondary" onClick={onResume}>
              Resume
            </Button>
          ) : null}
          {canCancel && onCancel ? (
            <Button size="sm" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
          ) : null}
          <Badge variant={statusLabel === "completed" ? "success" : "primary"}>
            {statusLabel}
          </Badge>
        </div>
      </div>
      {pendingApproval?.reason ? (
        <div className="mb-2 text-xs text-[#f1b45f]">{pendingApproval.reason}</div>
      ) : null}
      <ol className="space-y-2">
        {plan.steps.map((step) => {
          const isRunning = step.status === "running";
          const isDone = step.status === "done";
          const StepIcon = isDone ? Check : isRunning ? LoaderCircle : Circle;
          return (
            <li key={step.id} className="flex items-start gap-2 text-sm text-[#d8e4f8]">
              <StepIcon
                className={cn(
                  "mt-0.5 h-4 w-4 shrink-0",
                  isDone ? "text-[#38c67d]" : "text-[#5da8ff]",
                  isRunning ? "animate-spin" : ""
                )}
              />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span>{step.title}</span>
                  <span className="text-xs text-[#7484a3]">{step.status}</span>
                </div>
                {step.evidence_ids.length > 0 ? (
                  <div className="mt-1 text-xs text-[#8ea1c3]">
                    Evidence: {step.evidence_ids.join(", ")}
                  </div>
                ) : null}
                {step.blocker ? (
                  <div className="mt-1 text-xs text-[#f1b45f]">{step.blocker}</div>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
