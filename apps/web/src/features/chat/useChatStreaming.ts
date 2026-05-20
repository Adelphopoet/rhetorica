import { useState, useCallback, useRef } from "react";

import {
  applyStreamEvent,
  createCancelledMessage,
  createEmptyIntermediateSteps,
  createInitialStreamingMessage,
  createStreamErrorEvent,
  extractSseData,
  isTerminalEvent,
  parseStreamEvent,
} from "./streamingState";
import type {
  DoneEvent,
  ErrorEvent,
  IntermediateSteps,
  StreamEvent,
  StreamingMessage,
} from "./streamingTypes";

export type {
  ChunkEvent,
  DoneEvent,
  ErrorEvent,
  ExecutionStatusValue,
  OptimizationTrace,
  StreamEvent,
  StreamEventType,
  StreamingMessage,
  ThinkingBlockEntry,
  ToolCallEntry,
  ExecutionStatusEntry,
  IntermediateSteps,
} from "./streamingTypes";

interface UseChatStreamingOptions {
  workspaceId?: string;
  onComplete?: (
    message: StreamingMessage,
    doneEvent: DoneEvent,
    intermediateSteps: IntermediateSteps
  ) => void;
  onError?: (
    error: ErrorEvent,
    message: StreamingMessage | null,
    intermediateSteps: IntermediateSteps
  ) => void;
  generateMessageId?: () => string;
}

interface UseChatStreamingReturn {
  streamingMessage: StreamingMessage | null;
  isStreaming: boolean;
  startStreaming: (
    question: string,
    conversationId?: string,
    clarificationId?: string,
    clarificationValue?: string
  ) => Promise<void>;
  cancelStreaming: () => void;
  clearStreaming: () => void;
}

export function useChatStreaming(options: UseChatStreamingOptions = {}): UseChatStreamingReturn {
  const {
    workspaceId = "default",
    onComplete,
    onError,
    generateMessageId = () => `assistant-${Date.now()}`,
  } = options;

  const [streamingMessage, setStreamingMessage] = useState<StreamingMessage | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);

  const abortControllerRef = useRef<AbortController | null>(null);
  const currentMessageRef = useRef<StreamingMessage | null>(null);
  const intermediateStepsRef = useRef<IntermediateSteps>(createEmptyIntermediateSteps());
  const requestIdRef = useRef(0);

  const isCurrentRequest = useCallback((requestId: number) => {
    return requestIdRef.current === requestId;
  }, []);

  const commitMessage = useCallback(
    (requestId: number, message: StreamingMessage) => {
      if (!isCurrentRequest(requestId)) {
        return;
      }

      currentMessageRef.current = message;
      setStreamingMessage(message);
    },
    [isCurrentRequest]
  );

  const updateWithEvent = useCallback(
    (requestId: number, event: StreamEvent): StreamingMessage | null => {
      if (!isCurrentRequest(requestId)) {
        return null;
      }

      const previous = currentMessageRef.current;
      if (!previous) {
        return null;
      }

      const updated = applyStreamEvent(previous, event, intermediateStepsRef.current);
      commitMessage(requestId, updated);
      return updated;
    },
    [commitMessage, isCurrentRequest]
  );

  const startStreaming = useCallback(
    async (
      question: string,
      conversationId?: string,
      clarificationId?: string,
      clarificationValue?: string
    ) => {
      const requestId = requestIdRef.current + 1;
      requestIdRef.current = requestId;

      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      const controller = new AbortController();
      abortControllerRef.current = controller;

      const initialMessage = createInitialStreamingMessage(generateMessageId);
      currentMessageRef.current = initialMessage;
      setStreamingMessage(initialMessage);
      setIsStreaming(true);
      intermediateStepsRef.current = createEmptyIntermediateSteps();

      const completeRequest = (
        event: DoneEvent | ErrorEvent,
        updatedMessage: StreamingMessage | null
      ) => {
        if (!isCurrentRequest(requestId)) {
          return;
        }

        setIsStreaming(false);
        abortControllerRef.current = null;

        if (event.type === "done" && onComplete && updatedMessage) {
          onComplete(updatedMessage, event, {
            thinkingBlocks: [...intermediateStepsRef.current.thinkingBlocks],
            toolCalls: intermediateStepsRef.current.toolCalls.map((call) => ({ ...call })),
            executionStatuses: [...intermediateStepsRef.current.executionStatuses],
          });
          return;
        }

        if (event.type === "error") {
          onError?.(event, updatedMessage, {
            thinkingBlocks: [...intermediateStepsRef.current.thinkingBlocks],
            toolCalls: intermediateStepsRef.current.toolCalls.map((call) => ({ ...call })),
            executionStatuses: [...intermediateStepsRef.current.executionStatuses],
          });
        }
      };

      try {
        const response = await fetch("/api/chat/ask/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question,
            workspace_id: workspaceId,
            conversation_id: conversationId,
            run_sql: true,
            ...(clarificationId && {
              selected_clarification_id: clarificationId,
              selected_clarification_value: clarificationValue,
            }),
          }),
          signal: controller.signal,
        });

        if (!response.ok) {
          const errorText = await response.text();
          throw new Error(`HTTP ${response.status}: ${errorText}`);
        }

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error("ReadableStream not supported");
        }

        const decoder = new TextDecoder();
        let buffer = "";
        let terminalEventSeen = false;

        const flushRawEvent = (rawEvent: string) => {
          if (!isCurrentRequest(requestId)) {
            return;
          }

          const data = extractSseData(rawEvent);
          if (!data) {
            return;
          }

          const event = parseStreamEvent(data);
          if (!event) {
            return;
          }

          const updatedMessage = updateWithEvent(requestId, event);
          if (isTerminalEvent(event)) {
            terminalEventSeen = true;
            completeRequest(event, updatedMessage);
          }
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done || !isCurrentRequest(requestId)) {
            break;
          }

          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

          let boundary = buffer.indexOf("\n\n");
          while (boundary !== -1) {
            const rawEvent = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            flushRawEvent(rawEvent);
            boundary = buffer.indexOf("\n\n");
          }
        }

        if (isCurrentRequest(requestId) && buffer.trim()) {
          flushRawEvent(buffer);
        }

        // A closed stream without a terminal event is transport failure, not success.
        if (isCurrentRequest(requestId) && !terminalEventSeen) {
          const errorEvent: ErrorEvent = {
            type: "error",
            timestamp: new Date().toISOString(),
            code: "stream_ended_without_terminal_event",
            message: "Stream closed before final response was delivered.",
          };
          const updatedMessage = updateWithEvent(requestId, errorEvent);
          completeRequest(errorEvent, updatedMessage);
        }
      } catch (error) {
        if (!isCurrentRequest(requestId)) {
          return;
        }

        if (error instanceof Error && error.name === "AbortError") {
          const current = currentMessageRef.current;
          if (current) {
            commitMessage(requestId, createCancelledMessage(current));
          }
          setIsStreaming(false);
          return;
        }

        const errorEvent = createStreamErrorEvent(error);
        const updatedMessage = updateWithEvent(requestId, errorEvent);
        setIsStreaming(false);
        onError?.(errorEvent, updatedMessage, {
          thinkingBlocks: [...intermediateStepsRef.current.thinkingBlocks],
          toolCalls: intermediateStepsRef.current.toolCalls.map((call) => ({ ...call })),
          executionStatuses: [...intermediateStepsRef.current.executionStatuses],
        });
      } finally {
        if (isCurrentRequest(requestId)) {
          abortControllerRef.current = null;
        }
      }
    },
    [commitMessage, generateMessageId, isCurrentRequest, onComplete, onError, updateWithEvent, workspaceId]
  );

  const cancelStreaming = useCallback(() => {
    if (!abortControllerRef.current) {
      return;
    }

    requestIdRef.current += 1;
    abortControllerRef.current.abort();
    abortControllerRef.current = null;

    const current = currentMessageRef.current;
    if (current) {
      const cancelledMessage = createCancelledMessage(current);
      currentMessageRef.current = cancelledMessage;
      setStreamingMessage(cancelledMessage);
    }

    setIsStreaming(false);
  }, []);

  const clearStreaming = useCallback(() => {
    requestIdRef.current += 1;

    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }

    setStreamingMessage(null);
    currentMessageRef.current = null;
    intermediateStepsRef.current = createEmptyIntermediateSteps();
    setIsStreaming(false);
  }, []);

  return {
    streamingMessage,
    isStreaming,
    startStreaming,
    cancelStreaming,
    clearStreaming,
  };
}
