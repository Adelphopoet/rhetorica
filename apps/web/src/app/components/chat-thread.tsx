"use client";

import { useLayoutEffect, useRef, type ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";

import {
  mergeToolCallsWithOptimizationTrace,
  type ChatFeedbackDetails,
  type ChatFeedbackValue,
  type ChatMessageViewModel,
} from "@/features/chat/chatViewModel";
import { serializeChatMessageBodyForCopy } from "@/features/chat/chatCopy";
import { cn } from "@rhetorica/ui";
import { buildChatTimeline } from "@/features/chat/chatTimeline";
import { CodingRunChat } from "@/features/coding/CodingRunChat";
import type { CodingRun } from "@/features/coding/codingApi";
import { ChatEmptyState } from "./chat-empty-state";
import { ChatMessageComponent } from "./chat-message";
import { ChatThreadSkeleton } from "./chat-thread-skeleton";
import { TypingIndicator } from "./typing-indicator";
import type { ChatWidth } from "./chat-tools";

interface ChatThreadProps {
  chatWidth: ChatWidth;
  conversationId: string | null;
  messages: ChatMessageViewModel[];
  codingRuns?: CodingRun[];
  children?: ReactNode;
  userLabel?: string;
  loading: boolean;
  /** A saved conversation is being fetched; the thread must not fall back to the empty state. */
  threadLoading?: boolean;
  feedbackPendingByMessageId: Record<string, boolean>;
  onFeedback: (
    messageId: string,
    feedback: ChatFeedbackValue,
    messageConversationId: string | null | undefined,
    details?: ChatFeedbackDetails
  ) => void;
  onRepairTurn?: (messageId: string) => void;
  onSelectClarification?: (clarificationId: string, choiceId: string, resolvedValue: string) => void;
  onOpenScenarioCatalog: () => void;
  /** Called when user clicks "Inspect result" on an assistant message */
  onInspectResult?: (messageId: string, executionId?: string | null) => void;
}

export function ChatThread({
  chatWidth,
  conversationId,
  messages,
  codingRuns = [],
  children,
  userLabel,
  loading,
  threadLoading = false,
  feedbackPendingByMessageId,
  onFeedback,
  onRepairTurn,
  onSelectClarification,
  onOpenScenarioCatalog,
  onInspectResult,
}: ChatThreadProps) {
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);
  const previousConversationIdRef = useRef(conversationId);
  const previousLastMessageIdRef = useRef<string | null>(null);
  const lastMessage = messages.at(-1) ?? null;

  useLayoutEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) {
      return;
    }

    if (conversationId !== previousConversationIdRef.current) {
      previousConversationIdRef.current = conversationId;
      previousLastMessageIdRef.current = null;
      stickToBottomRef.current = true;
    }
    const isNewOutgoingMessage =
      lastMessage?.role === "user" && lastMessage.id !== previousLastMessageIdRef.current;
    previousLastMessageIdRef.current = lastMessage?.id ?? null;
    if (isNewOutgoingMessage) {
      stickToBottomRef.current = true;
    }
    if (stickToBottomRef.current) {
      container.scrollTop = container.scrollHeight;
    }
  }, [conversationId, lastMessage, loading]);

  useLayoutEffect(() => {
    const content = contentRef.current;
    if (!content || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      const container = scrollContainerRef.current;
      if (container && stickToBottomRef.current) container.scrollTop = container.scrollHeight;
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [conversationId, messages.length]);

  if (messages.length === 0 && codingRuns.length === 0) {
    if (threadLoading) {
      return <ChatThreadSkeleton chatWidth={chatWidth} />;
    }

    return (
      <div
        className="min-h-0 flex-1 overflow-auto p-4 sm:p-5 xl:p-6"
        data-tour="tour-chat-thread"
      >
        <ChatEmptyState
          chatWidth={chatWidth}
          onOpenScenarioCatalog={onOpenScenarioCatalog}
        />
      </div>
    );
  }

  return (
    <motion.div
      ref={scrollContainerRef}
      onScroll={(event) => {
        const container = event.currentTarget;
        const distanceFromBottom =
          container.scrollHeight - container.scrollTop - container.clientHeight;
        stickToBottomRef.current = distanceFromBottom <= 96;
      }}
      className="chat-thread flex-1 overflow-auto px-4 pb-5 pt-4 sm:px-5 xl:px-6"
      data-tour="tour-chat-thread"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.25 }}
    >
      <div
        ref={contentRef}
        className={cn(
          "mx-auto flex w-full flex-col gap-6",
          chatWidth === 1100 && "max-w-[1100px]",
          chatWidth === 1600 && "max-w-[1600px]"
        )}
      >
        <AnimatePresence initial={false}>
          {buildChatTimeline(messages, codingRuns).map((item) => {
            if (item.kind === "coding") return <CodingRunChat key={item.id} run={item.run} />;
            const message = item.message;
            const mergedToolCalls = mergeToolCallsWithOptimizationTrace({
              toolCalls: message.toolCalls,
              payload: message.payload,
              timestamp: message.timestamp,
            });

            return (
              <ChatMessageComponent
                key={item.id}
                id={message.backendMessageId}
                role={message.role}
                userLabel={message.role === "user" ? userLabel : undefined}
                text={message.text}
                timestamp={message.timestamp}
                thinkingBlocks={message.thinkingBlocks}
                toolCalls={mergedToolCalls}
                executionStatuses={message.executionStatuses}
                payload={message.payload}
                files={message.files}
                clarificationRequest={message.clarificationRequest ?? message.payload?.clarification_request ?? null}
                onSelectClarification={onSelectClarification}
                isStreaming={message.deliveryState === "streaming"}
                isComplete={message.deliveryState === "complete"}
                feedback={message.feedback ?? null}
                feedbackWhatWentWrong={message.feedbackWhatWentWrong ?? null}
                feedbackExpectedAnswer={message.feedbackExpectedAnswer ?? null}
                feedbackLoading={
                  message.backendMessageId
                    ? Boolean(feedbackPendingByMessageId[message.backendMessageId])
                    : false
                }
                copyText={serializeChatMessageBodyForCopy(message.text, message.payload)}
                onFeedback={(messageId, feedback, details) =>
                  onFeedback(messageId, feedback, message.payload?.conversation_id, details)
                }
                onRepairTurn={onRepairTurn}
                deliveryState={message.deliveryState}
                onInspectResult={onInspectResult ? (executionId) => onInspectResult(message.id, executionId) : undefined}
              />
            );
          })}
          {loading ? <TypingIndicator key="typing-indicator" /> : null}
        </AnimatePresence>
        {children}
      </div>
    </motion.div>
  );
}
