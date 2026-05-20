"use client";

import { useEffect, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";

import {
  mergeToolCallsWithOptimizationTrace,
  type ChatFeedbackValue,
  type ChatMessageViewModel,
} from "@/features/chat/chatViewModel";
import { serializeChatMessageBodyForCopy } from "@/features/chat/chatCopy";
import { ChatEmptyState } from "./chat-empty-state";
import { ChatMessageComponent } from "./chat-message";
import { TypingIndicator } from "./typing-indicator";

interface ChatThreadProps {
  messages: ChatMessageViewModel[];
  userLabel?: string;
  loading: boolean;
  scenarioCount: number;
  feedbackPendingByMessageId: Record<string, boolean>;
  onFeedback: (
    messageId: string,
    feedback: ChatFeedbackValue,
    messageConversationId: string | null | undefined
  ) => void;
  onApproveAnalysis?: () => void;
  onResumeAnalysis?: () => void;
  onCancelAnalysis?: () => void;
  onSelectClarification?: (clarificationId: string, choiceId: string, resolvedValue: string) => void;
  onOpenScenarioCatalog: () => void;
  /** Called when user clicks "Inspect result" on an assistant message */
  onInspectResult?: (messageId: string, executionId?: string | null) => void;
}

export function ChatThread({
  messages,
  userLabel,
  loading,
  scenarioCount,
  feedbackPendingByMessageId,
  onFeedback,
  onApproveAnalysis,
  onResumeAnalysis,
  onCancelAnalysis,
  onSelectClarification,
  onOpenScenarioCatalog,
  onInspectResult,
}: ChatThreadProps) {
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex-1 overflow-auto p-4 sm:p-5 xl:p-6">
        <ChatEmptyState
          scenarioCount={scenarioCount}
          onOpenScenarioCatalog={onOpenScenarioCatalog}
        />
      </div>
    );
  }

  return (
    <motion.div
      className="chat-thread flex-1 overflow-auto px-4 pb-5 pt-4 sm:px-5 xl:px-6"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.25 }}
    >
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
        <AnimatePresence initial={false}>
          {messages.map((message) => {
            const mergedToolCalls = mergeToolCallsWithOptimizationTrace({
              toolCalls: message.toolCalls,
              payload: message.payload,
              timestamp: message.timestamp,
            });

            return (
              <ChatMessageComponent
                key={message.id}
                id={message.backendMessageId}
                role={message.role}
                userLabel={message.role === "user" ? userLabel : undefined}
                text={message.text}
                timestamp={message.timestamp}
                thinkingBlocks={message.thinkingBlocks}
                toolCalls={mergedToolCalls}
                executionStatuses={message.executionStatuses}
                payload={message.payload}
                analysisPlan={message.payload?.analysis_plan ?? message.analysisPlan ?? null}
                checkpointDigest={message.payload?.checkpoint_digest ?? message.checkpointDigest ?? null}
                clarificationRequest={message.clarificationRequest ?? message.payload?.clarification_request ?? null}
                onApproveAnalysis={onApproveAnalysis}
                onResumeAnalysis={onResumeAnalysis}
                onCancelAnalysis={onCancelAnalysis}
                onSelectClarification={onSelectClarification}
                isStreaming={message.deliveryState === "streaming"}
                isComplete={message.deliveryState === "complete"}
                feedback={message.feedback ?? null}
                feedbackLoading={
                  message.backendMessageId
                    ? Boolean(feedbackPendingByMessageId[message.backendMessageId])
                    : false
                }
                copyText={serializeChatMessageBodyForCopy(message.text, message.payload)}
                onFeedback={(messageId, feedback) =>
                  onFeedback(messageId, feedback, message.payload?.conversation_id)
                }
                deliveryState={message.deliveryState}
                onInspectResult={onInspectResult ? (executionId) => onInspectResult(message.id, executionId) : undefined}
              />
            );
          })}
          {loading ? <TypingIndicator key="typing-indicator" /> : null}
        </AnimatePresence>
        <div ref={bottomRef} />
      </div>
    </motion.div>
  );
}
