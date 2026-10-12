"use client";

import { lazy, Suspense, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Badge, Button, Tooltip, useToast } from "@rhetorica/ui";
import { AlertTriangle, Check, ChevronDown, CircleOff, Copy, Download, FileCode2, FlaskConical, LoaderCircle, ThumbsDown, ThumbsUp } from "lucide-react";
import { cn } from "@rhetorica/ui";
import { DISPLAY_TIME_ZONE } from "@/lib/dateTime";
import { CollapsibleThinking } from "./streaming-components";
import { MarkdownMessage } from "./markdown-message";
import {
  getExecutionPhaseLabel,
  type ChatAskResponse,
  type ChatFeedbackDetails,
  type ChatDeliveryState,
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
import { formatFileSize, type FileArtifactRef } from "@/features/files/fileApi";

const LazyVisualizationChart = lazy(async () => ({
  default: (await import("@/features/charts")).VisualizationChart,
}));

interface ChatMessageProps {
  id?: string;
  role: "user" | "assistant";
  userLabel?: string;
  assistantLabel?: string;
  subagentRole?: "developer" | "qa";
  text: string;
  timestamp?: Date;
  className?: string;
  thinkingBlocks?: ThinkingBlockEntry[];
  toolCalls?: ToolCallEntry[];
  executionStatuses?: ExecutionStatusEntry[];
  isStreaming?: boolean;
  isComplete?: boolean;
  feedback?: "useful" | "wrong" | "unsafe" | "incomplete" | null;
  feedbackWhatWentWrong?: string | null;
  feedbackExpectedAnswer?: string | null;
  feedbackLoading?: boolean;
  deliveryState?: ChatDeliveryState;
  payload?: ChatAskResponse;
  files?: FileArtifactRef[];
  clarificationRequest?: ClarificationRequest | null;
  onSelectClarification?: (clarificationId: string, choiceId: string, resolvedValue: string) => void;
  copyText: string;
  onFeedback?: (
    messageId: string,
    feedback: "useful" | "wrong" | "unsafe" | "incomplete",
    details?: ChatFeedbackDetails
  ) => void;
  onRepairTurn?: (messageId: string) => void;
  /** Called when user clicks "Inspect result" on an assistant message. Passes execution_id if available */
  onInspectResult?: (executionId?: string | null) => void;
}

export function ChatMessageComponent({
  id,
  role,
  userLabel,
  assistantLabel,
  subagentRole,
  text,
  timestamp,
  className,
  thinkingBlocks = [],
  toolCalls = [],
  executionStatuses = [],
  isStreaming = false,
  isComplete = false,
  feedback = null,
  feedbackWhatWentWrong = null,
  feedbackExpectedAnswer = null,
  feedbackLoading = false,
  deliveryState = "idle",
  payload,
  files = [],
  clarificationRequest,
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
  const [whatWentWrong, setWhatWentWrong] = useState(feedbackWhatWentWrong || "");
  const [expectedAnswer, setExpectedAnswer] = useState(feedbackExpectedAnswer || "");
  useEffect(() => {
    setWhatWentWrong(feedbackWhatWentWrong || "");
    setExpectedAnswer(feedbackExpectedAnswer || "");
  }, [id, feedback, feedbackWhatWentWrong, feedbackExpectedAnswer]);
  const isUser = role === "user";
  const visibleFiles = files.length > 0 ? files : payload?.files ?? [];
  const resolvedUserLabel = userLabel?.trim() || "Аналитик";
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
  const hasNegativeFeedback = feedback !== null && feedback !== "useful";
  const canShowRepairButton = feedback !== null && feedback !== "useful";
  const canSaveExpandedFeedback = Boolean(whatWentWrong.trim() && expectedAnswer.trim());
  const executionPhase = getExecutionPhaseLabel(executionStatuses);
  const deliveryBadge = (() => {
    if (isUser) {
      return null;
    }
    if (deliveryState === "streaming") {
      return {
        variant: "primary" as const,
        label: executionPhase ?? "Ответ формируется",
        icon: LoaderCircle,
      };
    }
    if (deliveryState === "complete") {
      return {
        variant: "success" as const,
        label: executionPhase ?? "Ответ готов",
        icon: Check,
      };
    }
    if (deliveryState === "cancelled") {
      return {
        variant: "warning" as const,
        label: "Создание ответа отменено",
        icon: CircleOff,
      };
    }
    if (deliveryState === "error") {
      return {
        variant: "danger" as const,
        label: executionPhase ?? "Не удалось сформировать ответ",
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
        title: "Не удалось скопировать",
        description: error instanceof Error ? error.message : "Не удалось скопировать сообщение",
      });
    }
  };

  const handleExport = async (format: AnalysisExportFormat) => {
    if (!exportConversationId || !exportMessageId) {
      addToast({
        type: "error",
        title: "Экспорт недоступен",
        description: "У ответа ещё нет сохранённого снимка результата",
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
        title: "Экспорт готов",
        description: `Снимок ${format.toUpperCase()} скачан`,
      });
    } catch (error) {
      addToast({
        type: "error",
        title: "Не удалось выполнить экспорт",
        description: error instanceof Error ? error.message : "Неизвестная ошибка экспорта",
      });
    } finally {
      setExportingFormat(null);
    }
  };

  const canRenderFeedback = !isUser && deliveryState === "complete" && id && onFeedback && !clarificationRequest;
  const copyButton = (
    <Tooltip content={copied ? "Скопировано" : "Скопировать сообщение"} side="top">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-label="Скопировать сообщение"
        onClick={() => {
          void handleCopy();
        }}
        className="mr-2 h-8 w-8 border border-border-strong/50 p-0 text-secondary transition hover:bg-elevated hover:text-primary focus:ring-0"
      >
        {copied ? (
          <Check className="h-4 w-4 text-success" />
        ) : (
          <Copy className="h-4 w-4" />
        )}
      </Button>
    </Tooltip>
  );

  return (
    <motion.div
      data-testid={`chat-message-${role}`}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
      className={cn(
        "flex gap-3 rounded-[24px]",
        isUser
          ? "border border-accent-border bg-[linear-gradient(180deg,rgb(var(--color-elevated)/0.9),rgb(var(--color-surface)/0.94))] px-5 py-4 shadow-[0_20px_60px_rgb(var(--color-shadow)/calc(0.16*var(--shadow-opacity-scale)))]"
          : "border border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.88),rgb(var(--color-page)/0.94))] px-5 py-4 shadow-[0_20px_60px_rgb(var(--color-shadow)/calc(0.16*var(--shadow-opacity-scale)))]",
        !isUser && deliveryState === "streaming" && "ring-2 ring-accent/30",
        subagentRole === "developer" && "border-l-4 border-l-cyan/60",
        subagentRole === "qa" && "border-l-4 border-l-success/60",
        className
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-[0.18em] text-secondary">
            {isUser ? resolvedUserLabel : assistantLabel ?? "Rhetorica Assistant"}
          </span>
          {subagentRole && <Badge variant={subagentRole === "qa" ? "success" : "info"}>Субагент · {subagentRole === "qa" ? "QA" : "Developer"}</Badge>}
          {timestamp ? (
            <span className="text-xs text-muted">
              {timestamp.toLocaleTimeString("en-GB", {
                hour: "2-digit",
                minute: "2-digit",
                timeZone: DISPLAY_TIME_ZONE,
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
              <div className="min-h-[320px] rounded-xl border border-border bg-surface/[0.78]" />
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
          <p className="whitespace-pre-wrap text-sm leading-7 text-primary">{text}</p>
        ) : hasAnalysisReport ? (
          null
        ) : clarificationRequest ? (
          null
        ) : (
          <MarkdownMessage text={text} />
        )}

        {visibleFiles.length > 0 ? (
          <div className="mt-3 grid gap-2 sm:grid-cols-2" aria-label="Файлы сообщения">
            {visibleFiles.map((file) => (
              <a
                key={file.id}
                href={`/api/files/${encodeURIComponent(file.id)}/content`}
                download={file.filename}
                className="flex min-w-0 items-center gap-3 rounded-xl border border-border-strong bg-surface px-3 py-2 text-left transition hover:border-accent hover:bg-elevated"
              >
                <Download className="h-4 w-4 shrink-0 text-accent" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-primary">{file.filename}</span>
                  <span className="block text-xs text-secondary">{formatFileSize(file.size_bytes)}</span>
                </span>
              </a>
            ))}
          </div>
        ) : null}

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

        {canRenderFeedback ? (
          <div className="mt-5 flex flex-col gap-3">
            <div className="relative -left-0.5 flex flex-wrap items-center gap-2">
              {copyButton}
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={feedbackLoading}
                aria-label="Полезно"
                onClick={() => {
                  onFeedback(id, "useful");
                  setShowFeedbackDetails(false);
                }}
                className={cn(
                  "h-8 w-8 p-0 transition-all focus:ring-0",
                  feedback === "useful"
                    ? "bg-accent/30 text-primary border border-accent shadow-[0_0_0_1px_rgb(var(--color-accent)/0.45)]"
                    : "text-secondary border border-border-strong/50"
                )}
              >
                <ThumbsUp className="h-4 w-4" />
              </Button>

              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={feedbackLoading}
                aria-label="Не полезно"
                aria-pressed={showFeedbackDetails || hasNegativeFeedback}
                onClick={() => setShowFeedbackDetails(true)}
                className={cn(
                  "h-8 w-8 p-0 transition-all focus:ring-0",
                  showFeedbackDetails || hasNegativeFeedback
                    ? "bg-accent/30 text-primary border border-accent shadow-[0_0_0_1px_rgb(var(--color-accent)/0.45)]"
                    : "text-secondary border border-border-strong/50"
                )}
              >
                <ThumbsDown className="h-4 w-4" />
              </Button>
            </div>

            {showFeedbackDetails ? (
              <form
                className="flex max-w-xl flex-col gap-4 rounded-2xl border border-border-strong bg-elevated p-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!canSaveExpandedFeedback || feedbackLoading) {
                    return;
                  }
                  onFeedback(id, "wrong", {
                    whatWentWrong: whatWentWrong.trim(),
                    expectedAnswer: expectedAnswer.trim(),
                  });
                  setShowFeedbackDetails(false);
                }}
              >
                <div className="flex items-start gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-warning/30 bg-warning/10 text-warning">
                    <ThumbsDown className="h-4 w-4" />
                  </span>
                  <span>
                    <span className="block text-sm font-semibold text-primary">Помогите улучшить ответ</span>
                    <span className="mt-0.5 block text-xs leading-5 text-secondary">Опишите проблему и ожидаемый результат — этого достаточно для разбора.</span>
                  </span>
                </div>
                <label className="flex flex-col gap-2 text-sm font-medium text-primary" htmlFor={`${id}-what-went-wrong`}>
                  Что пошло не так? <span className="text-warning" aria-hidden="true">*</span>
                  <textarea
                    id={`${id}-what-went-wrong`}
                    aria-label="Что пошло не так?"
                    required
                    className="min-h-[88px] w-full resize-y rounded-xl border border-control-border bg-surface px-3 py-2.5 text-sm font-normal text-primary placeholder:text-muted outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/20"
                    placeholder="Например: использована неверная метрика или период"
                    value={whatWentWrong}
                    onChange={(event) => setWhatWentWrong(event.target.value)}
                  />
                </label>
                <label className="flex flex-col gap-2 text-sm font-medium text-primary" htmlFor={`${id}-expected-answer`}>
                  Как нужно было ответить? <span className="text-warning" aria-hidden="true">*</span>
                  <textarea
                    id={`${id}-expected-answer`}
                    aria-label="Как нужно было ответить?"
                    required
                    className="min-h-[88px] w-full resize-y rounded-xl border border-control-border bg-surface px-3 py-2.5 text-sm font-normal text-primary placeholder:text-muted outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/20"
                    placeholder="Опишите ожидаемый вывод, данные или формат ответа"
                    value={expectedAnswer}
                    onChange={(event) => setExpectedAnswer(event.target.value)}
                  />
                </label>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="submit"
                    size="sm"
                    variant="secondary"
                    disabled={!canSaveExpandedFeedback || feedbackLoading}
                    className="bg-accent/20 text-primary hover:bg-accent/30"
                  >
                    <Check className="h-4 w-4" />
                    Отправить отзыв
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setShowFeedbackDetails(false);
                    }}
                    className="text-secondary"
                  >
                    Отмена
                  </Button>
                </div>
              </form>
            ) : null}

            {/* Fix answer button for negative feedback */}
            {canShowRepairButton && onRepairTurn ? (
              <div className="mt-2">
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  disabled={feedbackLoading}
                  onClick={() => {
                    if (!feedbackLoading) {
                      onRepairTurn(id);
                    }
                  }}
                  className="gap-2 bg-warning/20 text-warning hover:bg-warning/30 border border-warning/30"
                >
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  Исправить ответ
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}

        {!canRenderFeedback ? (
          <div className="relative -left-0.5 mt-3 flex items-center gap-2">{copyButton}</div>
        ) : null}

        {canExportReport || (canInspect && onInspectResult) ? (
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          {canExportReport ? (
            <div className="relative inline-flex">
              <Tooltip content="Экспорт отчёта" side="top">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={exportingFormat !== null}
                  aria-expanded={isExportMenuOpen}
                  aria-haspopup="menu"
                  aria-label="Экспорт отчёта"
                  onClick={() => {
                    setIsExportMenuOpen((current) => !current);
                  }}
                  className="gap-2 border border-border-strong/60 px-3 text-secondary hover:bg-elevated hover:text-primary"
                >
                  <Download className="h-4 w-4" />
                  {exportingFormat ? `Экспорт ${exportingFormat.toUpperCase()}…` : "Экспорт"}
                  <ChevronDown className="h-4 w-4" />
                </Button>
              </Tooltip>
              {isExportMenuOpen ? (
                <div
                  role="menu"
                  aria-label="Форматы экспорта отчёта"
                  className="absolute right-0 top-full z-20 mt-2 min-w-36 overflow-hidden rounded-2xl border border-border-strong bg-surface p-1 shadow-2xl shadow-shadow/35"
                >
                  {(["html", "pdf", "ipynb"] as AnalysisExportFormat[]).map((format) => (
                    <button
                      key={format}
                      type="button"
                      role="menuitem"
                      className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-xs font-semibold uppercase text-primary transition hover:bg-elevated hover:text-primary"
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
            <Tooltip content="Просмотреть результат" side="top">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label="Просмотреть результат"
                onClick={() => {
                  onInspectResult(payload?.execution?.execution_id ?? null);
                }}
                className="gap-2 border border-border-strong/60 px-3 text-secondary hover:bg-elevated hover:text-primary"
              >
                <FlaskConical className="h-4 w-4" />
                Просмотр
              </Button>
            </Tooltip>
          ) : null}
        </div>
        ) : null}
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
    <details className="mt-3 rounded-xl border border-border-strong bg-surface/[0.62]">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs font-semibold uppercase tracking-[0.12em] text-secondary marker:hidden">
        <FileCode2 className="h-4 w-4 text-accent" />
        SQL
        <span className="ml-auto rounded-full border border-border-strong px-2 py-0.5 text-[11px] normal-case tracking-normal text-secondary">
          {engine}
        </span>
      </summary>
      <div className="border-t border-border p-3">
        <SqlCodeBlock title={hasDistinctExecutedSql ? "Выполненный SQL" : "SQL"} sql={primarySql} />
        {hasDistinctExecutedSql && generatedSql ? (
          <div className="mt-3">
            <SqlCodeBlock title="Исходный черновик SQL" sql={generatedSql} />
          </div>
        ) : null}
      </div>
    </details>
  );
}

function SqlCodeBlock({ title, sql }: { title: string; sql: string }) {
  return (
    <section>
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary">
        {title}
      </p>
      <pre className="max-h-[360px] overflow-auto rounded-lg border border-border bg-page p-3 text-xs leading-6 text-primary">
        <code>{sql}</code>
      </pre>
    </section>
  );
}
